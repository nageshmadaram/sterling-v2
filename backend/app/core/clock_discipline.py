"""Read actual Linux kernel clock discipline without requiring systemd or privileges."""
from __future__ import annotations

import ctypes
import platform
from typing import Tuple


class _Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class _Timex(ctypes.Structure):
    # glibc/Linux 64-bit struct timex. Unsupported ABIs fail closed below.
    _fields_ = [
        ("modes", ctypes.c_uint), ("offset", ctypes.c_long), ("freq", ctypes.c_long),
        ("maxerror", ctypes.c_long), ("esterror", ctypes.c_long), ("status", ctypes.c_int),
        ("constant", ctypes.c_long), ("precision", ctypes.c_long), ("tolerance", ctypes.c_long),
        ("time", _Timeval), ("tick", ctypes.c_long), ("ppsfreq", ctypes.c_long),
        ("jitter", ctypes.c_long), ("shift", ctypes.c_int), ("stabil", ctypes.c_long),
        ("jitcnt", ctypes.c_long), ("calcnt", ctypes.c_long), ("errcnt", ctypes.c_long),
        ("stbcnt", ctypes.c_long), ("tai", ctypes.c_int), ("reserved", ctypes.c_int * 11),
    ]


def kernel_clock_synchronised() -> Tuple[bool, str]:
    """modes=0 is a query: it cannot adjust the clock or change discipline flags."""
    if (platform.system() != "Linux" or ctypes.sizeof(ctypes.c_long) != 8
            or ctypes.sizeof(_Timex) != 208
            or platform.machine().lower() not in {"x86_64", "amd64", "aarch64", "arm64"}):
        return False, "kernel_clock_unsupported_abi"
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        query = libc.adjtimex
        query.argtypes = [ctypes.POINTER(_Timex)]
        query.restype = ctypes.c_int
        observation = _Timex()  # zeroed modes: read-only
        state = query(ctypes.byref(observation))
        if state < 0:
            return False, f"kernel_clock_unavailable:errno={ctypes.get_errno()}"
        # TIME_ERROR=5; STA_UNSYNC=0x40; STA_CLOCKERR=0x1000.
        if state not in {0, 1, 2, 3, 4} or observation.status & (0x40 | 0x1000):
            return False, f"kernel_clock_not_synchronised:state={state},status={observation.status:#x}"
        if observation.maxerror < 0 or observation.maxerror > 500_000:
            return False, f"kernel_clock_error_bound:{observation.maxerror}us"
        return True, f"kernel_synchronised:state={state},maxerror={observation.maxerror}us"
    except Exception as exc:
        return False, f"kernel_clock_unavailable:{type(exc).__name__}"
