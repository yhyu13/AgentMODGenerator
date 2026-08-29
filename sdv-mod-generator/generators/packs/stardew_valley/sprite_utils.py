"""Pixel-art post-processing for the sprite_generator plan.

Turns a high-resolution generated image into a game-usable N×N pixel-art
sprite, pure stdlib (no Pillow). The generator I/O (image API call) lives
elsewhere; this module is the deterministic core that makes a real sprite
out of whatever the model returns.

See doc/sprite-generator-plan.md for the full plan.
"""
from __future__ import annotations

import zlib


def downsample(
    pixels: list[tuple[int, int, int]],
    width: int,
    height: int,
    target: int = 16,
) -> list[list[tuple[int, int, int]]]:
    """Shrink a W×H RGB image (row-major) to a target×target grid.

    Each output cell is the integer mean of the source block it covers.
    Uses integer arithmetic (no float rounding), so block boundaries are
    exact and the last block absorbs any remainder when ``width``/``height``
    are not divisible by ``target``.
    """
    grid: list[list[tuple[int, int, int]]] = []
    for ty in range(target):
        y0 = ty * height // target
        y1 = (ty + 1) * height // target
        row: list[tuple[int, int, int]] = []
        for tx in range(target):
            x0 = tx * width // target
            x1 = (tx + 1) * width // target
            rs = gs = bs = 0
            n = 0
            for y in range(y0, max(y1, y0 + 1)):
                for x in range(x0, max(x1, x0 + 1)):
                    r, g, b = pixels[y * width + x]
                    rs += r
                    gs += g
                    bs += b
                    n += 1
            row.append((rs // n, gs // n, bs // n))
        grid.append(row)
    return grid


def _luminance(c: tuple[int, int, int]) -> float:
    r, g, b = c
    return 0.299 * r + 0.587 * g + 0.114 * b


def quantize(
    grid: list[list[tuple[int, int, int]]],
    palette: int = 16,
) -> tuple[
    list[list[tuple[int, int, int, int]]],
    list[tuple[int, int, int, int]],
]:
    """Quantize an RGB grid to ≤``palette`` RGBA colors.

    The brightest cluster (colors within 10% of the max luminance) is
    treated as background and collapsed to a single transparent color
    (alpha=0). Remaining colors are kept exact when they already fit,
    otherwise bucketed by luminance into ``palette-1`` clusters. Game
    sprites need the transparent surround, so background merging is the
    point — near-white JPEG dither (249/251/252) must become one hole,
    not three.
    """
    flat = [cell for row in grid for cell in row]
    unique = list(dict.fromkeys(flat))

    max_lum = max(_luminance(c) for c in unique)
    bg_set = {c for c in unique if _luminance(c) >= 0.9 * max_lum}
    fg = [c for c in unique if c not in bg_set]

    mapping: dict[tuple[int, int, int], tuple[int, int, int, int]] = {}
    if bg_set:
        bg_avg = tuple(sum(c[i] for c in bg_set) // len(bg_set) for i in range(3))
        for c in bg_set:
            mapping[c] = (bg_avg[0], bg_avg[1], bg_avg[2], 0)

    if len(fg) <= palette - 1:
        for c in fg:
            mapping[c] = (c[0], c[1], c[2], 255)
    else:
        fg_sorted = sorted(fg, key=_luminance)
        n_buckets = max(palette - 1, 1)
        buckets: list[list[tuple[int, int, int]]] = [[] for _ in range(n_buckets)]
        for i, c in enumerate(fg_sorted):
            idx = min(i * n_buckets // len(fg_sorted), n_buckets - 1)
            buckets[idx].append(c)
        for bucket in buckets:
            if not bucket:
                continue
            avg = tuple(sum(c[i] for c in bucket) // len(bucket) for i in range(3))
            for c in bucket:
                mapping[c] = (avg[0], avg[1], avg[2], 255)

    result_grid = [[mapping[c] for c in row] for row in grid]
    palette_list = list(dict.fromkeys(mapping.values()))
    return result_grid, palette_list


def encode_png(grid: list[list[tuple[int, int, int, int]]]) -> bytes:
    """Encode an RGBA grid as a PNG byte string (8-bit RGBA, no filter).

    Pure stdlib — the generator writes the result straight into the zip
    via ``add_file``, so no Pillow dependency.
    """
    height = len(grid)
    width = len(grid[0]) if height else 0

    def chunk(ctype: bytes, data: bytes) -> bytes:
        return (
            len(data).to_bytes(4, "big")
            + ctype
            + data
            + zlib.crc32(ctype + data).to_bytes(4, "big")
        )

    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes([8, 6, 0, 0, 0])  # 8-bit, RGBA, deflate, no filter, no interlace
    )
    raw = b""
    for row in grid:
        raw += bytes([0])  # filter type 0 (None)
        for r, g, b, a in row:
            raw += bytes([r, g, b, a])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def decode_png(png: bytes) -> tuple[list[tuple[int, int, int]], int, int]:
    """Decode an 8-bit RGB/RGBA PNG into (row-major RGB pixels, width, height).

    Alpha is dropped — generated images are opaque, and the sprite pipeline
    works in RGB until ``quantize`` re-introduces transparency. Handles the
    four non-interlaced row filters.
    """
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos = 8
    width = height = color_type = None
    idat = b""
    while pos < len(png):
        length = int.from_bytes(png[pos:pos + 4], "big")
        ctype = png[pos + 4:pos + 8]
        chunk = png[pos + 8:pos + 8 + length]
        if ctype == b"IHDR":
            width = int.from_bytes(chunk[0:4], "big")
            height = int.from_bytes(chunk[4:8], "big")
            color_type = chunk[9]
        elif ctype == b"IDAT":
            idat += chunk
        elif ctype == b"IEND":
            break
        pos += 12 + length

    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
    bpp = channels
    raw = zlib.decompress(idat)
    stride = width * bpp

    def paeth(a: int, b: int, c: int) -> int:
        p = a + b - c
        pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
        if pa <= pb and pa <= pc:
            return a
        return b if pb <= pc else c

    rows: list[bytearray] = []
    prev = bytearray(stride)
    i = 0
    for _ in range(height):
        f = raw[i]
        i += 1
        line = bytearray(raw[i:i + stride])
        i += stride
        if f == 1:
            for x in range(bpp, stride):
                line[x] = (line[x] + line[x - bpp]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                left = line[x - bpp] if x >= bpp else 0
                up = prev[x]
                ul = prev[x - bpp] if x >= bpp else 0
                line[x] = (line[x] + paeth(left, up, ul)) & 0xFF
        rows.append(line)
        prev = line

    pixels = [
        (rows[y][x * bpp], rows[y][x * bpp + 1], rows[y][x * bpp + 2])
        for y in range(height)
        for x in range(width)
    ]
    return pixels, width, height
