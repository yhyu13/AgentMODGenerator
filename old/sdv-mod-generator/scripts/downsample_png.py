"""Downsample the 1024x1024 probe to 16x16, quantize, and ASCII-render the shape.

Answers: does the fish silhouette survive a naive downsample + palette quantize?
Pure stdlib. Reuses the PNG unfilter logic.
"""
import sys
import zlib
from collections import Counter

PNG = sys.argv[1] if len(sys.argv) > 1 else r".wolf\tmp_sprite_probe.png"
data = open(PNG, "rb").read()
pos = 8
width = height = color_type = None
idat = b""
while pos < len(data):
    length = int.from_bytes(data[pos:pos+4], "big")
    ctype = data[pos+4:pos+8]
    chunk = data[pos+8:pos+8+length]
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

def paeth(a, b, c):
    p = a + b - c
    pa, pb, pc = abs(p-a), abs(p-b), abs(p-c)
    if pa <= pb and pa <= pc:
        return a
    return b if pb <= pc else c

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

TARGET = 16
scale = width // TARGET
cells = []
for ty in range(TARGET):
    row = []
    for tx in range(TARGET):
        rs = gs = bs = 0
        for dy in range(scale):
            for dx in range(scale):
                off = (tx*scale+dx) * bpp
                r = rows[ty*scale+dy][off]
                g = rows[ty*scale+dy][off+1]
                b = rows[ty*scale+dy][off+2]
                rs += r; gs += g; bs += b
        n = scale*scale
        row.append((rs//n, gs//n, bs//n))
    cells.append(row)

uniq = set(c for row in cells for c in row)
print(f"downsampled_unique_colors={len(uniq)}")

# luminance ASCII map (0=dark .. 9=bright)
chars = " .:-=+*#%@"
print("--- luminance map (16x16) ---")
for row in cells:
    line = ""
    for (r, g, b) in row:
        l = int((0.299*r + 0.587*g + 0.114*b) / 256 * (len(chars)-1))
        line += chars[min(l, len(chars)-1)]
    print(line)

# simple 8-color quantize: background (brightest cluster) + 7 dominant buckets
flat = [c for row in cells for c in row]
# split into "background-ish" (all channels > 230) vs "foreground"
bg = [c for c in flat if all(v > 230 for v in c)]
fg = [c for c in flat if not all(v > 230 for v in c)]
print(f"background_cells={len(bg)} foreground_cells={len(fg)}")

def bucket(c):
    r, g, b = c
    if all(v > 230 for v in c):
        return 0  # bg
    # quantize by hue-ish sign pattern to 7 buckets
    idx = 1 + (1 if r > 150 else 0) + (2 if g > 150 else 0) + (4 if b > 150 else 0)
    return min(idx, 7)

print("--- quantized map (0=bg) ---")
for row in cells:
    print("".join(str(bucket(c)) for c in row))
