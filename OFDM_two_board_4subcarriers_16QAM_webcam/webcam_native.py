"""Optional RFSoC word reader and JPEG-to-MP4 muxer using the board's FFmpeg libraries."""
import ctypes as ct
import os
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path

import numpy as np


@lru_cache(maxsize=1)
def native_library():
    if os.name != "posix":
        return None
    try:
        with tempfile.TemporaryDirectory(prefix="ofdm-webcam-") as tmp:
            target = Path(tmp) / "webcam.so"
            subprocess.run(["cc", "-std=c11", "-O2", "-fPIC", "-shared",
                            str(Path(__file__).with_suffix(".c")), "-o", str(target),
                            "-lavformat", "-lavcodec", "-lavutil"], capture_output=True,
                           check=True, timeout=30)
            lib = ct.CDLL(str(target))
    except (OSError, subprocess.SubprocessError) as exc:
        detail = getattr(exc, "stderr", b"") or b""
        print("Native acceleration unavailable. Using aligned Python reads and OpenCV.",
              detail.decode(errors="replace")[-300:] or str(exc), flush=True)
        return None
    lib.copy_device_words.argtypes = [ct.c_void_p, ct.c_void_p, ct.c_size_t]
    lib.copy_device_words.restype = None
    lib.movie_open.argtypes = [ct.POINTER(ct.c_void_p), ct.c_char_p, ct.c_double, ct.c_int, ct.c_int]
    lib.movie_write.argtypes = [ct.c_void_p, ct.c_char_p, ct.c_int]
    lib.movie_close.argtypes = [ct.c_void_p]
    return lib


def read_words(view, native):
    if view.ndim != 1 or view.dtype != np.dtype("<u4") or not view.flags.c_contiguous or view.ctypes.data % 4:
        raise ValueError("BRAM read requires a contiguous aligned uint32 view")
    if native is None:
        return np.fromiter((view[i] for i in range(view.size)), dtype="<u4", count=view.size).tobytes()
    result = np.empty(view.size, dtype="<u4")
    native.copy_device_words(view.ctypes.data, result.ctypes.data, view.size)
    return result.tobytes()


def write_words(view, values, native):
    values = np.require(values, dtype="<u4", requirements=["C", "A"])
    if (view.ndim != 1 or view.dtype != values.dtype or view.shape != values.shape
            or not view.flags.c_contiguous or view.ctypes.data % 4):
        raise ValueError("BRAM write requires matching aligned uint32 views")
    if native is None:
        for i, word in enumerate(values):
            view[i] = word
    else:
        native.copy_device_words(values.ctypes.data, view.ctypes.data, view.size)


class JpegMovie:
    def __init__(self, lib, filename, fps, width, height):
        self.lib = lib
        self.handle = ct.c_void_p()
        self.check(lib.movie_open(ct.byref(self.handle), os.fsencode(filename), fps, width, height))

    @staticmethod
    def check(code):
        if code < 0:
            raise OSError(f"FFmpeg MP4 operation failed (error {code})")

    def write(self, jpeg):
        self.check(self.lib.movie_write(self.handle, jpeg, len(jpeg)))

    def release(self):
        self.check(self.lib.movie_close(self.handle))
