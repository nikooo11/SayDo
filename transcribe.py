"""Local STT via faster-whisper (CTranslate2) with Silero VAD gating silence."""


class Transcriber:
    def __init__(self, cfg):
        self.cfg = cfg
        engine = cfg.get("engine", "faster-whisper")
        if engine == "mlx-whisper":
            import mlx_whisper  # optional Neural-Engine path
            self._mlx = mlx_whisper
            self._model = None
        else:
            from faster_whisper import WhisperModel
            from bundle import bundled_model
            self._mlx = None
            name = cfg.get("model", "base")
            self._model = WhisperModel(
                bundled_model(name) or name,
                device="cpu",
                compute_type=cfg.get("compute_type", "int8"),
            )

    def transcribe(self, audio):
        """audio: mono float32 numpy array at 16kHz. Returns text."""
        if audio.size == 0:
            return ""
        if self._mlx is not None:
            result = self._mlx.transcribe(audio, language=self.cfg.get("language"))
            return result.get("text", "").strip()
        import dictionary
        vocab = dictionary.words()
        segments, _ = self._model.transcribe(
            audio,
            language=self.cfg.get("language"),
            vad_filter=True,
            beam_size=1,
            # bias recognition toward the user's custom vocabulary
            initial_prompt=("Vocabulary: " + ", ".join(vocab) + ".") if vocab else None,
        )
        return " ".join(seg.text.strip() for seg in segments).strip()
