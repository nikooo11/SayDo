"""Mic capture: continuous 500ms pre-roll ring buffer + on-demand recording."""
import collections
import threading

import numpy as np
import sounddevice as sd


class Recorder:
    """The stream is opened on demand (hotkey press) and released after a short
    linger, so macOS's orange mic-in-use indicator only shows while dictating."""

    def __init__(self, sample_rate=16000, channels=1, preroll_ms=500, blocksize=320):
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        preroll_blocks = max(1, int(sample_rate * preroll_ms / 1000 / blocksize))
        self._preroll = collections.deque(maxlen=preroll_blocks)
        self._chunks = []
        self._recording = False
        self.level = 0.0
        self._lock = threading.Lock()
        self._stream = None

    def _callback(self, indata, frames, time_info, status):
        block = indata.copy()
        self.level = float(np.sqrt((block ** 2).mean()))  # RMS for waveform + level meter
        with self._lock:
            if self._recording:
                self._chunks.append(block)
            else:
                self._preroll.append(block)

    @property
    def is_open(self):
        return self._stream is not None

    def open(self, resolver=None):
        """Open the mic bound to the CURRENT system default (or the device the
        resolver picks). PortAudio is re-initialized first so it sees hardware
        that (dis)connected since the last open."""
        if self._stream is not None:
            return
        sd._terminate()
        sd._initialize()
        device = resolver() if resolver else None
        kwargs = {"device": device} if device is not None else {}
        self._preroll.clear()
        self._stream = sd.InputStream(
            samplerate=self.sample_rate, channels=self.channels, dtype="float32",
            blocksize=self.blocksize, callback=self._callback, **kwargs,
        )
        self._stream.start()

    def release(self):
        """Close the stream so macOS drops the mic-in-use indicator. No-op if
        recording (caller guards, but stay safe)."""
        with self._lock:
            if self._recording:
                return
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self.level = 0.0

    def rebuild(self, resolver=None):
        """Re-open an already-open stream against the current default device.
        Call only while not recording; returns False if busy."""
        with self._lock:
            if self._recording:
                return False
        if self._stream is None:
            return True  # nothing open; the next open() binds fresh anyway
        try:
            self._stream.stop()
            self._stream.close()
        except Exception:
            pass
        self._stream = None
        self.open(resolver)
        return True

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
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
