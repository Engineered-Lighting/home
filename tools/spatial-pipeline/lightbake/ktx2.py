"""Minimal KTX2 writer/reader for uncompressed 2D and 2D-array textures.

Only what the lighting layer needs: one mip level, no supercompression, 8-bit
UNORM/SRGB and 16-bit SFLOAT formats, with a Khronos Basic Data Format
Descriptor. three.js KTX2Loader reads these uncompressed vkFormats directly.
"""
from __future__ import annotations

import struct

import numpy as np

IDENTIFIER = b"\xabKTX 20\xbb\r\n\x1a\n"

# vkFormat: (bytes per channel, channels, transfer, dtype, float)
FORMATS = {
    9: (1, 1, 1, np.uint8, False),     # VK_FORMAT_R8_UNORM
    37: (1, 4, 1, np.uint8, False),    # VK_FORMAT_R8G8B8A8_UNORM
    43: (1, 4, 2, np.uint8, False),    # VK_FORMAT_R8G8B8A8_SRGB
    76: (2, 1, 1, np.float16, True),   # VK_FORMAT_R16_SFLOAT
}
NAMES = {"r8": 9, "rgba8": 37, "rgba8_srgb": 43, "r16f": 76}


def _dfd(vk):
    bpc, nch, transfer, _, is_float = FORMATS[vk]
    samples = b""
    for c in range(nch):
        channel = c if c < 3 else 15  # R, G, B, A
        qualifiers = 0
        if is_float:
            qualifiers |= 0x80 | 0x40  # float, signed
        if transfer == 2 and c == 3:
            qualifiers |= 0x10         # linear (alpha in an sRGB format)
        lower, upper = (0xBF800000, 0x3F800000) if is_float else (0, (1 << (8 * bpc)) - 1)
        samples += struct.pack("<HBB4BII", c * 8 * bpc, 8 * bpc - 1, channel | qualifiers, 0, 0, 0, 0, lower, upper)
    block_size = 24 + len(samples)
    block = struct.pack("<IHH4B4B8B", 0, 2, block_size, 1, 1, transfer, 0, 0, 0, 0, 0,
                        bpc * nch, 0, 0, 0, 0, 0, 0, 0) + samples
    return struct.pack("<I", 4 + len(block)) + block


def write(path, data, fmt, kv=None):
    """data: (H, W, C) or (layers, H, W, C) array matching the format."""
    vk = NAMES[fmt]
    bpc, nch, _, dtype, _ = FORMATS[vk]
    a = np.asarray(data)
    if a.ndim == 2:
        a = a[..., None]
    layers = 0
    if a.ndim == 4:
        layers = a.shape[0]
    h, w, c = a.shape[-3:]
    if c != nch:
        raise ValueError(f"{fmt} needs {nch} channels, got {c}")
    payload = np.ascontiguousarray(a.astype(dtype)).astype(np.dtype(dtype).newbyteorder("<")).tobytes()
    dfd = _dfd(vk)
    kvd = b""
    for k, v in sorted({"KTXwriter": "home lightbake", **(kv or {})}.items()):
        entry = k.encode() + b"\0" + v.encode() + b"\0"
        kvd += struct.pack("<I", len(entry)) + entry + b"\0" * (-len(entry) % 4)
    header_len = 12 + 4 * 9 + 4 * 4 + 8 * 2 + 24  # identifier, header, index, one level
    dfd_off = header_len
    kvd_off = dfd_off + len(dfd)
    data_off = kvd_off + len(kvd)
    align = max(bpc * nch, 4)
    pad = -data_off % align
    data_off += pad
    out = IDENTIFIER + struct.pack("<9I", vk, bpc, w, h, 0, layers, 1, 1, 0)
    out += struct.pack("<IIIIQQ", dfd_off, len(dfd), kvd_off, len(kvd), 0, 0)
    out += struct.pack("<QQQ", data_off, len(payload), len(payload))
    out += dfd + kvd + b"\0" * pad + payload
    with open(path, "wb") as f:
        f.write(out)
    return len(out)


def read(path):
    b = open(path, "rb").read()
    if b[:12] != IDENTIFIER:
        raise ValueError("not a KTX2 file")
    vk, type_size, w, h, depth, layers, faces, levels, scheme = struct.unpack_from("<9I", b, 12)
    off, length, _ = struct.unpack_from("<QQQ", b, 12 + 36 + 32)
    bpc, nch, _, dtype, _ = FORMATS[vk]
    a = np.frombuffer(b, dtype=np.dtype(dtype).newbyteorder("<"), count=length // bpc, offset=off)
    shape = ((layers,) if layers else ()) + (h, w, nch)
    return a.reshape(shape), {"vkFormat": vk, "typeSize": type_size, "layers": layers, "levels": levels,
                              "supercompression": scheme}
