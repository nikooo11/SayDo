"""Local STT.

Engines (stt.engine in config.yaml):
  parakeet       — NVIDIA Parakeet TDT 0.6b via MLX (Apple-silicon GPU).
                   Best accuracy AND lowest latency; English only.
  faster-whisper — Whisper via CTranslate2 on CPU. Multilingual fallback.
  mlx-whisper    — Whisper on the Neural Engine (optional path).

Parakeet has no initial-prompt vocabulary biasing, so custom-dictionary terms
are enforced downstream (misspelling corrections + LLM cleanup vocabulary).
"""
import queue
import tempfile
import threading
import wave
from pathlib import Path

import numpy as np

PARAKEET_DEFAULT = "mlx-community/parakeet-tdt-0.6b-v2"


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
        try:
            from parakeet_mlx import from_pretrained
            self._model = from_pretrained(model_name)
        except Exception as e:
            self.error = e
            self._ready.set()
            return
        self._ready.set()
        while True:
            path, box, done = self._q.get()
            try:
                box["result"] = self._model.transcribe(
                    path, chunk_duration=120.0, overlap_duration=15.0)
            except Exception as e:
                box["error"] = e
            done.set()

    def transcribe(self, path):
        box, done = {}, threading.Event()
        self._q.put((path, box, done))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box["result"]


def _write_wav(audio, sample_rate=16000):
    """float32 mono ndarray -> temp 16-bit wav path (parakeet-mlx wants a file)."""
    path = tempfile.mktemp(suffix=".wav", prefix="saydo-")
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm.tobytes())
    return path


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
            path = _write_wav(audio)
            try:
                result = self._pk.transcribe(path)
            finally:
                Path(path).unlink(missing_ok=True)
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
