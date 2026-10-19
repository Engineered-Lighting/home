"""Intel Open Image Denoise through its C API (ctypes), for the mesh lightmaps.

Nothing is downloaded: point OIDN_DIR (or --oidn-dir) at a folder holding an
existing OpenImageDenoise 2.x library. Known workstation installs are probed.
The "RTLightmap" filter is OIDN's lightmap denoiser (HDR, colour only).
"""
from __future__ import annotations

import ctypes
import glob
import os
import sys

import numpy as np

CANDIDATES = [
    r"C:\Program Files\Side Effects Software\Houdini *\bin",
    r"C:\Program Files\Blender Foundation\Blender *\blender.shared",
    r"C:\Program Files\Epic Games\UE_*\Engine\Binaries\Win64",
]
FLOAT3 = 3


def _find(explicit=None):
    names = ["OpenImageDenoise.dll"] if sys.platform == "win32" else ["libOpenImageDenoise.so.2", "libOpenImageDenoise.so"]
    dirs = [explicit, os.environ.get("OIDN_DIR")] + sorted(sum((glob.glob(c) for c in CANDIDATES), []), reverse=True)
    for d in dirs:
        if d:
            for n in names:
                if os.path.exists(os.path.join(d, n)):
                    return d, os.path.join(d, n)
    return None, None


class Denoiser:
    def __init__(self, oidn_dir=None):
        d, lib = _find(oidn_dir)
        if not lib:
            raise RuntimeError("OpenImageDenoise library not found; set OIDN_DIR")
        if hasattr(os, "add_dll_directory"):
            self._dll_dir = os.add_dll_directory(d)
        L = self.L = ctypes.CDLL(lib)
        vp, sz, cp = ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p
        L.oidnNewDevice.restype = vp
        L.oidnNewDevice.argtypes = [ctypes.c_int]
        L.oidnCommitDevice.argtypes = [vp]
        L.oidnGetDeviceInt.argtypes = [vp, cp]
        L.oidnGetDeviceInt.restype = ctypes.c_int
        L.oidnGetDeviceError.argtypes = [vp, ctypes.POINTER(cp)]
        L.oidnGetDeviceError.restype = ctypes.c_int
        L.oidnNewBuffer.argtypes = [vp, sz]
        L.oidnNewBuffer.restype = vp
        L.oidnWriteBuffer.argtypes = [vp, sz, sz, vp]
        L.oidnReadBuffer.argtypes = [vp, sz, sz, vp]
        L.oidnReleaseBuffer.argtypes = [vp]
        L.oidnNewFilter.argtypes = [vp, cp]
        L.oidnNewFilter.restype = vp
        L.oidnSetFilterImage.argtypes = [vp, cp, vp, ctypes.c_int, sz, sz, sz, sz, sz]
        L.oidnCommitFilter.argtypes = [vp]
        L.oidnExecuteFilter.argtypes = [vp]
        L.oidnReleaseFilter.argtypes = [vp]
        L.oidnReleaseDevice.argtypes = [vp]
        self.dev = L.oidnNewDevice(0)
        L.oidnCommitDevice(self.dev)
        self._check()
        self.library = lib
        self.version = L.oidnGetDeviceInt(self.dev, b"version")
        self.device_type = {1: "cpu", 2: "sycl", 3: "cuda", 4: "hip", 5: "metal"}.get(
            L.oidnGetDeviceInt(self.dev, b"type"), "unknown")

    def _check(self):
        msg = ctypes.c_char_p()
        if self.L.oidnGetDeviceError(self.dev, ctypes.byref(msg)):
            raise RuntimeError("OIDN: " + (msg.value or b"").decode())

    def lightmap(self, rgb):
        """Denoise one HDR lightmap (H, W, 3) float -> float32 (H, W, 3)."""
        img = np.ascontiguousarray(rgb, np.float32)
        h, w, _ = img.shape
        nbytes = img.nbytes
        L = self.L
        src, dst = L.oidnNewBuffer(self.dev, nbytes), L.oidnNewBuffer(self.dev, nbytes)
        L.oidnWriteBuffer(src, 0, nbytes, img.ctypes.data)
        f = L.oidnNewFilter(self.dev, b"RTLightmap")
        L.oidnSetFilterImage(f, b"color", src, FLOAT3, w, h, 0, 0, 0)
        L.oidnSetFilterImage(f, b"output", dst, FLOAT3, w, h, 0, 0, 0)
        L.oidnCommitFilter(f)
        L.oidnExecuteFilter(f)
        self._check()
        out = np.empty_like(img)
        L.oidnReadBuffer(dst, 0, nbytes, out.ctypes.data)
        L.oidnReleaseFilter(f)
        L.oidnReleaseBuffer(src)
        L.oidnReleaseBuffer(dst)
        return out

    def describe(self):
        v = self.version
        return {"library": "OpenImageDenoise", "version": f"{v // 10000}.{v // 100 % 100}.{v % 100}",
                "device": self.device_type, "filter": "RTLightmap"}
