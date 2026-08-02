"""Mic capture: continuous 500ms pre-roll ring buffer + on-demand recording."""
import collections
import threading

import numpy as np
import sounddevice as sd


class Recorder:
    def __init__(self, sample_rate=16000, channels=1, preroll_ms=500, blocksize=320,
                 device=None):
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        preroll_blocks = max(1, int(sample_rate * preroll_ms / 1000 / blocksize))
        self._preroll = collections.deque(maxlen=preroll_blocks)
        self._chunks = []
        self._recording = False
        self.level = 0.0
        self._lock = threading.Lock()
        kwargs = {"device": device} if device is not None else {}
        self._stream = sd.InputStream(
            samplerate=sample_rate, channels=channels, dtype="float32",
            blocksize=blocksize, callback=self._callback, **kwargs,
        )

    def _callback(self, indata, frames, time_info, status):
        block = indata.copy()
        self.level = float(np.sqrt((block ** 2).mean()))  # RMS for waveform + level meter
        with self._lock:
            if self._recording:
                self._chunks.append(block)
            else:
                self._preroll.append(block)

    def start_stream(self):
        self._stream.start()

    def start(self):
        """Begin recording; the pre-roll buffer is prepended so the first word isn't clipped."""
        with self._lock:
            self._chunks = list(self._preroll)
            self._preroll.clear()
            self._recording = True

    def stop(self):
        """Stop recording and return mono float32 audio."""
        with self._lock:
            self._recording = False
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).flatten()

    def close(self):
        self._stream.stop()
        self._stream.close()
