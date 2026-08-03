"""Manual file transcription: audio or video in, .txt (+ .srt) out.

Decoding uses PyAV (already a faster-whisper dependency), which reads the
audio track of video files too. Runs on a worker thread; the dashboard polls
progress() until phase is done or error.
"""
import threading
import time
from pathlib import Path

import numpy as np

_state = {"phase": "idle", "detail": "", "out": None, "started": 0.0}
_lock = threading.Lock()

AUDIO_EXTS = {"wav", "mp3", "m4a", "aac", "flac", "ogg", "opus", "wma", "aiff", "aif"}
VIDEO_EXTS = {"mp4", "mov", "m4v", "mkv", "webm", "avi"}


def progress():
    with _lock:
        return dict(_state)


def _set(phase, detail="", out=None):
    with _lock:
        _state.update(phase=phase, detail=detail, out=out)


def decode(path):
    """Any audio/video file -> mono float32 ndarray at 16kHz."""
    import av
    chunks = []
    with av.open(str(path)) as container:
        stream = container.streams.audio[0]
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        for frame in container.decode(stream):
            for out in resampler.resample(frame):
                chunks.append(out.to_ndarray())
        for out in resampler.resample(None):  # flush
            chunks.append(out.to_ndarray())
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    pcm = np.concatenate(chunks, axis=1).flatten()
    return pcm.astype(np.float32) / 32768.0


def _srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_outputs(src, segments):
    """Sidecar .txt and .srt next to the source file; returns the .txt path."""
    src = Path(src)
    txt = src.with_suffix(".txt")
    txt.write_text("\n".join(s["text"] for s in segments) + "\n")
    srt = src.with_suffix(".srt")
    lines = []
    for i, s in enumerate(segments, 1):
        lines += [str(i), f"{_srt_time(s['start'])} --> {_srt_time(s['end'])}",
                  s["text"], ""]
    srt.write_text("\n".join(lines))
    return txt


def start(path, transcriber):
    """Kick off transcription of `path`; returns False if one is running."""
    with _lock:
        if _state["phase"] in ("decoding", "transcribing"):
            return False
        _state.update(phase="decoding", detail=Path(path).name, out=None,
                      started=time.time())

    def _run():
        try:
            audio = decode(path)
            if audio.size < 1600:  # under 0.1s of audio
                _set("error", "No audio track found in that file.")
                return
            mins = audio.size / 16000 / 60
            _set("transcribing", f"{Path(path).name} ({mins:.0f} min)")
            segments = transcriber.transcribe_segments(audio)
            if not segments:
                _set("error", "No speech detected in that file.")
                return
            out = write_outputs(path, segments)
            _set("done", out.name, str(out))
        except Exception as e:
            _set("error", str(e))

    threading.Thread(target=_run, daemon=True).start()
    return True
