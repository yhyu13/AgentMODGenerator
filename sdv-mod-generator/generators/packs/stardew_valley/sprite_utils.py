"""Pixel-art post-processing for the sprite_generator plan.

Turns a high-resolution generated image into a game-usable N×N pixel-art
sprite. The deterministic core (downsample/quantize/encode_png/decode_png)
is pure stdlib; only ``decode_jpeg`` pulls in Pillow (lazily) because
MiniMax image-01 returns JPEG where gpt-image returns PNG. The generator
I/O (image API call) lives elsewhere; this module turns whatever the model
returns into a real sprite.

See doc/sprite-generator-plan.md for the full plan.
"""
from __future__ import annotations

import colorsys
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


def _sort_key(c: tuple[int, int, int]) -> tuple[float, float, float]:
    """Order colors for bucketing: chromatic by hue, near-gray by luminance.

    Luminance-only ordering merges distinct hues that happen to share a
    brightness (a dark orange folds into a brown). Sorting by hue first
    keeps the color identity; the low-saturation guard keeps gray ramps
    coherent, because hue is undefined for near-gray.
    """
    h, l, s = colorsys.rgb_to_hls(c[0] / 255.0, c[1] / 255.0, c[2] / 255.0)
    if s < 0.15:
        return (1.0, l, h)  # near-gray: by luminance
    return (0.0, h, l)  # chromatic: by hue, then luminance


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
        fg_sorted = sorted(fg, key=_sort_key)
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


def decode_jpeg(jpeg: bytes) -> tuple[list[tuple[int, int, int]], int, int]:
    """Decode a JPEG into (row-major RGB pixels, width, height) via Pillow.

    MiniMax image-01 returns JPEG (``image_base64``), unlike gpt-image's
    PNG. JPEG decode needs Pillow (the PNG path above is pure stdlib);
    Pillow is imported lazily so the deterministic PNG path keeps working
    without it installed.
    """
    import io

    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - only when Pillow absent
        raise RuntimeError(
            "JPEG decode requires Pillow (pip install pillow); "
            "MiniMax image-01 returns JPEG"
        ) from exc

    with Image.open(io.BytesIO(jpeg)) as img:
        img = img.convert("RGB")
        width, height = img.size
        pixels = list(img.getdata())
    return pixels, width, height


def decode_image(data: bytes) -> tuple[list[tuple[int, int, int]], int, int]:
    """Decode a PNG or JPEG byte string into (row-major RGB pixels, w, h).

    Sniffs the magic bytes: PNG signature → :func:`decode_png` (pure
    stdlib), JPEG SOI (``FF D8 FF``) → :func:`decode_jpeg` (Pillow).
    Raises ValueError for anything else, so a provider that changes format
    fails loudly instead of silently producing a corrupt sprite.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return decode_png(data)
    if data[:3] == b"\xff\xd8\xff":
        return decode_jpeg(data)
    raise ValueError(f"unsupported image format (magic bytes {data[:4]!r})")
