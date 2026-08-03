"""Tiny CoreAudio bridge: which input device is the system default right now.

PortAudio snapshots the device list when it initializes, so it never notices
AirPods connecting or the default input changing. Polling this signature and
rebuilding the stream when it changes keeps SayDo on the mic the user expects.
"""
import ctypes
import ctypes.util
import struct

_ca = ctypes.CDLL(ctypes.util.find_library("CoreAudio"))


def _fourcc(s):
    return struct.unpack(">I", s.encode())[0]


class _PropertyAddress(ctypes.Structure):
    _fields_ = [("mSelector", ctypes.c_uint32),
                ("mScope", ctypes.c_uint32),
                ("mElement", ctypes.c_uint32)]


_SYSTEM_OBJECT = 1
_GLOBAL = _fourcc("glob")
_MAIN = 0


def default_input_id():
    addr = _PropertyAddress(_fourcc("dIn "), _GLOBAL, _MAIN)
    dev = ctypes.c_uint32(0)
    size = ctypes.c_uint32(ctypes.sizeof(dev))
    err = _ca.AudioObjectGetPropertyData(_SYSTEM_OBJECT, ctypes.byref(addr),
                                         0, None, ctypes.byref(size), ctypes.byref(dev))
    return dev.value if err == 0 else 0


def input_signature():
    """Default-input id + every device id: changes whenever the default moves
    or hardware (dis)connects."""
    addr = _PropertyAddress(_fourcc("dev#"), _GLOBAL, _MAIN)
    size = ctypes.c_uint32(0)
    if _ca.AudioObjectGetPropertyDataSize(_SYSTEM_OBJECT, ctypes.byref(addr),
                                          0, None, ctypes.byref(size)) != 0:
        return (default_input_id(),)
    n = size.value // 4
    arr = (ctypes.c_uint32 * n)()
    _ca.AudioObjectGetPropertyData(_SYSTEM_OBJECT, ctypes.byref(addr),
                                   0, None, ctypes.byref(size), ctypes.byref(arr))
    return (default_input_id(),) + tuple(arr)
