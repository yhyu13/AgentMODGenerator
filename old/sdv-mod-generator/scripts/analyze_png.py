"""Analyze a generated PNG for pixel-art viability (pure stdlib, no PIL).

Answers three questions with numbers:
1. Is it grid-aligned (color changes only on 16px boundaries, blocks are flat)?
2. Is the palette clean (few unique colors, no anti-aliasing gradient)?
3. Is the background a solid, removable color?
"""
import sys
import zlib
from collections import Counter

PNG = sys.argv[1] if len(sys.argv) > 1 else r".wolf\tmp_sprite_probe.png"

data = open(PNG, "rb").read()
assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"

pos = 8
width = height = bit_depth = color_type = None
idat = b""
while pos < len(data):
    length = int.from_bytes(data[pos:pos+4], "big")
    ctype = data[pos+4:pos+8]
    chunk = data[pos+8:pos+8+length]
    if ctype == b"IHDR":
        width = int.from_bytes(chunk[0:4], "big")
        height = int.from_bytes(chunk[4:8], "big")
        bit_depth = chunk[8]
        color_type = chunk[9]
    elif ctype == b"IDAT":
        idat += chunk
    elif ctype == b"IEND":
        break
    pos += 12 + length

channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color_type]
bpp = channels * (bit_depth // 8)
print(f"size={width}x{height} color_type={color_type} channels={channels} bit_depth={bit_depth}")

raw = zlib.decompress(idat)
stride = width * bpp

def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c

rows = []
prev = bytearray(stride)
i = 0
for _ in range(height):
    f = raw[i]; i += 1
    line = bytearray(raw[i:i+stride]); i += stride
    if f == 1:
        for x in range(bpp, stride):
            line[x] = (line[x] + line[x-bpp]) & 0xFF
    elif f == 2:
        for x in range(stride):
            line[x] = (line[x] + prev[x]) & 0xFF
    elif f == 3:
        for x in range(stride):
            left = line[x-bpp] if x >= bpp else 0
            line[x] = (line[x] + ((left + prev[x]) >> 1)) & 0xFF
    elif f == 4:
        for x in range(stride):
            left = line[x-bpp] if x >= bpp else 0
            up = prev[x]
            ul = prev[x-bpp] if x >= bpp else 0
            line[x] = (line[x] + paeth(left, up, ul)) & 0xFF
    rows.append(line)
    prev = line

def px(x, y):
    off = x * bpp
    return tuple(rows[y][off:off+channels])

# 1. unique colors
colors = Counter()
for y in range(height):
    for x in range(width):
        colors[px(x, y)] += 1
print(f"unique_colors={len(colors)}")
print("top_colors:", colors.most_common(8))

# 2. background = corners (assume the 4 corners are background)
corners = [px(0, 0), px(width-1, 0), px(0, height-1), px(width-1, height-1)]
print(f"corner_colors={corners}")
bg = Counter(corners).most_common(1)[0][0]
bg_count = sum(n for c, n in colors.items() if tuple(c[:3]) == tuple(bg[:3]))
print(f"background_pixel_ratio={bg_count / (width*height):.3f}")

# 3. grid alignment: is each 16x16 block flat?
block = 16
flat_blocks = total_blocks = 0
for by in range(0, height, block):
    for bx in range(0, width, block):
        c0 = px(bx, by)
        flat = all(px(bx+dx, by+dy) == c0 for dy in range(block) for dx in range(block))
        total_blocks += 1
        flat_blocks += flat
print(f"flat_16x16_blocks={flat_blocks}/{total_blocks} ({flat_blocks/total_blocks:.1%})")

# 4. anti-aliasing heuristic: count colors that are "between" bg and a saturated color
#    (mid luminance, low saturation) — a proxy for AA gradient pixels
def lum(c):
    r, g, b = c[0], c[1], c[2]
    return 0.299*r + 0.587*g + 0.114*b
bg_lum = lum(bg)
mid = 0
for c, n in colors.items():
    l = lum(c)
    mx, mn = max(c[:3]), min(c[:3])
    sat = (mx - mn) if mx else 0
    if bg_lum < 200 and 40 < l < bg_lum - 20 and sat < 30:
        mid += n
    elif bg_lum >= 200 and 40 < l < 200 and sat < 30:
        mid += n
print(f"mid_gray_aa_proxy_pixels={mid} ({mid/(width*height):.2%})")
