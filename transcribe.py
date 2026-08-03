"""Local STT.

Engines (stt.engine in config.yaml):
  parakeet       — NVIDIA Parakeet TDT 0.6b via MLX (Apple-silicon GPU).
                   Best accuracy AND lowest latency; English only.
  faster-whisper — Whisper via CTranslate2 on CPU. Multilingual fallback.
  mlx-whisper    — Whisper on the Neural Engine (optional path).

Parakeet has no initial-prompt vocabulary biasing, so custom-dictionary terms
are enforced downstream (misspelling corrections, fuzzy dictionary snapping,
LLM cleanup vocabulary).
"""
import queue
import threading

import numpy as np

PARAKEET_DEFAULT = "mlx-community/parakeet-tdt-0.6b-v2"


CHUNK_S = 120.0    # long recordings are chunked to bound attention memory
OVERLAP_S = 15.0


def _pk_transcribe_array(model, audio):
    """model.transcribe() for an in-memory float32 array. Mirrors the library's
    file path minus load_audio(), which shells out to ffmpeg per call: the
    packaged app has no homebrew on PATH (dictation would die), and skipping
    the temp wav + subprocess also shaves per-dictation latency."""
    import mlx.core as mx
    from parakeet_mlx.alignment import (
        merge_longest_common_subsequence,
        merge_longest_contiguous,
        sentences_to_result,
        tokens_to_sentences,
    )
    from parakeet_mlx.audio import get_logmel
    from parakeet_mlx.parakeet import DecodingConfig

    cfg = model.preprocessor_config
    decoding = DecodingConfig()
    data = mx.array(audio.astype(np.float32))
    sr = cfg.sample_rate
    if audio.size / sr <= CHUNK_S:
        return model.generate(get_logmel(data, cfg), decoding_config=decoding)[0]

    chunk = int(CHUNK_S * sr)
    overlap = int(OVERLAP_S * sr)
    all_tokens = []
    for start in range(0, len(data), chunk - overlap):
        end = min(start + chunk, len(data))
        if end - start < cfg.hop_length:
            break  # prevent zero-length log mel
        result = model.generate(get_logmel(data[start:end], cfg),
                                decoding_config=decoding)[0]
        offset = start / sr
        for sentence in result.sentences:
            for token in sentence.tokens:
                token.start += offset
                token.end = token.start + token.duration
        if not all_tokens:
            all_tokens = result.tokens
            continue
        try:
            all_tokens = merge_longest_contiguous(
                all_tokens, result.tokens, overlap_duration=OVERLAP_S)
        except RuntimeError:
            all_tokens = merge_longest_common_subsequence(
                all_tokens, result.tokens, overlap_duration=OVERLAP_S)
    return sentences_to_result(tokens_to_sentences(all_tokens, decoding.sentence))


class _ParakeetWorker:
    """Owns the MLX model on one dedicated thread. MLX streams are bound to
    the thread that first evaluates the graph, so calling the model from the
    dictation/file-transcription worker threads directly raises
    'There is no Stream in current thread'. Serializing every request through
    this thread fixes that and stops concurrent jobs contending for the GPU."""

    def __init__(self, model_name):
        self._q = queue.Queue()
        self._ready = threading.Event()
        self.error = None
        self._model = None
        threading.Thread(target=self._run, args=(model_name,), daemon=True).start()
        self._ready.wait()

    def _run(self, model_name):
        # Dictation latency rides on this thread's priority: TDT decoding
        # submits hundreds of tiny Metal kernels per utterance, and at
        # default QoS in a background app each submission eats a scheduling
        # penalty (observed ~1x realtime in-app vs ~0.02x for the same model
        # in a foreground CLI). User-interactive QoS removes it.
        try:
            import ctypes
            ctypes.CDLL(None).pthread_set_qos_class_self_np(0x21, 0)
        except Exception:
            pass
        try:
            from parakeet_mlx import from_pretrained
            self._model = from_pretrained(model_name)
            # Wire the model into GPU-resident memory. Without this, macOS
            # pages MLX buffers out after a few idle minutes and the next
            # dictation pays seconds of re-faulting (observed 8-15s for an
            # utterance that transcribes in <1s when hot).
            import mlx.core as mx
            mx.set_wired_limit(3 * 1024 ** 3)
        except Exception as e:
            self.error = e
            self._ready.set()
            return
        self._ready.set()
        while True:
            audio, box, done = self._q.get()
            try:
                box["result"] = _pk_transcribe_array(self._model, audio)
            except Exception as e:
                box["error"] = e
            done.set()

    def transcribe(self, audio):
        """audio: mono float32 ndarray at 16kHz -> AlignedResult."""
        box, done = {}, threading.Event()
        self._q.put((audio, box, done))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box["result"]


class Transcriber:
    def __init__(self, cfg):
        self.cfg = cfg
        self.engine = cfg.get("engine", "parakeet")
        self._mlx = None
        self._pk = None
        self._model = None
        if self.engine == "parakeet":
            worker = _ParakeetWorker(cfg.get("parakeet_model", PARAKEET_DEFAULT))
            if worker.error is not None:
                print(f"Parakeet unavailable ({worker.error}); "
                      "falling back to faster-whisper")
                self.engine = "faster-whisper"
            else:
                self._pk = worker
        if self.engine == "mlx-whisper":
            import mlx_whisper  # optional Neural-Engine path
            self._mlx = mlx_whisper
        elif self.engine == "faster-whisper":
            from faster_whisper import WhisperModel
            from bundle import bundled_model
            name = cfg.get("model", "base")
            self._model = WhisperModel(
                bundled_model(name) or name,
                device="cpu",
                compute_type=cfg.get("compute_type", "int8"),
            )

    @property
    def model_label(self):
        if self._pk is not None:
            return "parakeet"
        return self.cfg.get("model", "base")

    def transcribe(self, audio):
        """audio: mono float32 numpy array at 16kHz. Returns text."""
        if audio.size == 0:
            return ""
        return " ".join(s["text"] for s in self.transcribe_segments(audio)).strip()

    def transcribe_segments(self, audio):
        """Timestamped transcription: [{start, end, text}, ...]. Used for both
        live dictation (joined) and file transcription (txt + srt export)."""
        if audio.size == 0:
            return []
        if self._pk is not None:
            result = self._pk.transcribe(audio)
            out = [{"start": float(s.start), "end": float(s.end),
                    "text": s.text.strip()}
                   for s in getattr(result, "sentences", []) if s.text.strip()]
            if not out and result.text.strip():
                out = [{"start": 0.0, "end": audio.size / 16000.0,
                        "text": result.text.strip()}]
            return out
        if self._mlx is not None:
            result = self._mlx.transcribe(audio, language=self.cfg.get("language"))
            segs = result.get("segments") or []
            if segs:
                return [{"start": float(s["start"]), "end": float(s["end"]),
                         "text": s["text"].strip()}
                        for s in segs if s["text"].strip()]
            text = result.get("text", "").strip()
            return [{"start": 0.0, "end": audio.size / 16000.0, "text": text}] if text else []
        import dictionary
        vocab = dictionary.words()
        segments, _ = self._model.transcribe(
            audio,
            language=self.cfg.get("language"),
            vad_filter=True,
            # beam search over greedy: measurably fewer recognition errors
            beam_size=int(self.cfg.get("beam_size", 5)),
            # bias recognition toward the user's custom vocabulary
            initial_prompt=("Vocabulary: " + ", ".join(vocab) + ".") if vocab else None,
        )
        return [{"start": float(s.start), "end": float(s.end), "text": s.text.strip()}
                for s in segments if s.text.strip()]
