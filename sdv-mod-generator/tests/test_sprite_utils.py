"""Tests for sprite_utils — pixel-art post-processing (downsample + quantize).

The module is the core of the sprite_generator plan (see
doc/sprite-generator-plan.md): it turns a high-resolution generated image
into a game-usable N×N pixel-art sprite. These tests pin the pure-function
contract at the seam, so the generator I/O stays untested (mocked) and the
downstream CP-shape wiring is covered by the real SMAPI load gate instead.
"""
from __future__ import annotations

import pytest

from generators.packs.stardew_valley.sprite_utils import (
    decode_image,
    decode_jpeg,
    decode_png,
    downsample,
    encode_png,
    quantize,
)


class TestDownsample:
    """Pin the size-reduction contract: block-average, target×target grid."""

    def test_4x4_to_2x2_block_average(self) -> None:
        # 4x4 gray image, row-major. Expected values are hand-computed
        # block averages, independent of the implementation.
        pixels = [
            (0, 0, 0), (10, 10, 10), (20, 20, 20), (30, 30, 30),
            (40, 40, 40), (50, 50, 50), (60, 60, 60), (70, 70, 70),
            (80, 80, 80), (90, 90, 90), (100, 100, 100), (110, 110, 110),
            (120, 120, 120), (130, 130, 130), (140, 140, 140), (150, 150, 150),
        ]
        result = downsample(pixels, width=4, height=4, target=2)
        assert result == [
            [(25, 25, 25), (45, 45, 45)],
            [(105, 105, 105), (125, 125, 125)],
        ]

    def test_3x3_to_2x2_uneven_blocks(self) -> None:
        # 3x3 → 2x2: the last row/col block absorbs the remainder.
        # Hand-computed with integer block boundaries:
        #   row0=[row0], row1=[row1,row2]; col0=[col0], col1=[col1,col2]
        pixels = [
            (0, 0, 0), (10, 10, 10), (20, 20, 20),
            (30, 30, 30), (40, 40, 40), (50, 50, 50),
            (60, 60, 60), (70, 70, 70), (80, 80, 80),
        ]
        result = downsample(pixels, width=3, height=3, target=2)
        assert result == [
            [(0, 0, 0), (15, 15, 15)],
            [(45, 45, 45), (60, 60, 60)],
        ]


class TestQuantize:
    """Pin the palette-reduction contract: ≤palette colors, brightest
    cluster becomes transparent background (alpha=0)."""

    def test_brightest_color_becomes_transparent_background(self) -> None:
        grid = [
            [(255, 255, 255), (0, 0, 0)],
            [(0, 0, 0), (255, 255, 255)],
        ]
        result_grid, palette = quantize(grid, palette=2)
        # White → alpha 0 (transparent), black → alpha 255 (opaque).
        assert result_grid[0][0][3] == 0
        assert result_grid[0][1][3] == 255
        assert result_grid[1][0][3] == 255
        assert result_grid[1][1][3] == 0
        # RGB is preserved; only alpha changes.
        assert result_grid[0][0][:3] == (255, 255, 255)
        assert result_grid[0][1][:3] == (0, 0, 0)
        assert len(palette) <= 2

    def test_palette_compression(self) -> None:
        # 4 distinct grays, palette=2 → collapses to ≤2 cells: transparent
        # background (bright cluster) + one dark color.
        grid = [
            [(10, 10, 10), (20, 20, 20)],
            [(200, 200, 200), (210, 210, 210)],
        ]
        result_grid, palette = quantize(grid, palette=2)
        unique = {cell for row in result_grid for cell in row}
        assert len(unique) <= 2
        # The bright cluster (200/210) becomes transparent background.
        assert result_grid[1][0][3] == 0
        assert result_grid[1][1][3] == 0
        # The dark cluster stays opaque.
        assert result_grid[0][0][3] == 255
        assert result_grid[0][1][3] == 255

    def test_hue_bucketing_keeps_distinct_hues_separate(self) -> None:
        # Green (0,250,0) and a brownish-olive (120,90,30) have near-equal
        # luminance (146.75 vs 158.4) but different hue. Luminance-only
        # bucketing would sort them adjacent and merge them into a mud,
        # losing the green. Hue-aware bucketing puts the two orange-family
        # colors together and keeps green in its own bucket.
        grid = [
            [(255, 255, 255), (255, 140, 0)],
            [(0, 250, 0), (120, 90, 30)],
        ]
        result_grid, _palette = quantize(grid, palette=3)
        opaque = {c for row in result_grid for c in row if c[3] == 255}
        # Green survives as a distinct opaque color.
        assert (0, 250, 0, 255) in opaque
        # Exactly two opaque colors: green + the merged orange family.
        assert len(opaque) == 2


class TestPipeline:
    """End-to-end: downsample then quantize preserves foreground shape."""

    def test_downsample_then_quantize_preserves_foreground(self) -> None:
        # 8x8 white image with a 4x4 dark square in the bottom-right
        # quadrant, aligned to the 4x4 downsample grid.
        pixels = [
            (0, 0, 0) if x >= 4 and y >= 4 else (255, 255, 255)
            for y in range(8)
            for x in range(8)
        ]
        grid = downsample(pixels, width=8, height=8, target=4)
        result_grid, palette = quantize(grid, palette=4)
        # The dark square survives as exactly the bottom-right 2x2 block
        # of the 4x4 grid; the rest is transparent background.
        opaque = [
            (ty, tx)
            for ty in range(4)
            for tx in range(4)
            if result_grid[ty][tx][3] == 255
        ]
        assert opaque == [(2, 2), (2, 3), (3, 2), (3, 3)]
        assert len(palette) <= 4


class TestEncodePng:
    """Pin the PNG encoder: valid signature/IHDR and raw pixel bytes."""

    def test_encode_rgba_grid(self) -> None:
        import struct
        import zlib

        grid = [
            [(255, 0, 0, 255), (0, 255, 0, 128)],
            [(0, 0, 255, 0), (10, 20, 30, 255)],
        ]
        png = encode_png(grid)

        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        assert struct.unpack(">I", png[16:20])[0] == 2  # width
        assert struct.unpack(">I", png[20:24])[0] == 2  # height
        assert png[24] == 8  # bit depth
        assert png[25] == 6  # color type RGBA

        # Decompress IDAT; each row is filter byte 0 + RGBA pixels.
        pos = 8
        idat = b""
        while pos < len(png):
            length = struct.unpack(">I", png[pos:pos + 4])[0]
            ctype = png[pos + 4:pos + 8]
            chunk = png[pos + 8:pos + 8 + length]
            if ctype == b"IDAT":
                idat += chunk
            pos += 12 + length
        raw = zlib.decompress(idat)
        stride = 2 * 4 + 1  # 2 pixels * 4 channels + 1 filter byte
        assert raw[0] == 0
        assert tuple(raw[1:5]) == (255, 0, 0, 255)
        assert tuple(raw[5:9]) == (0, 255, 0, 128)
        assert raw[stride] == 0
        assert tuple(raw[stride + 1:stride + 5]) == (0, 0, 255, 0)
        assert tuple(raw[stride + 5:stride + 9]) == (10, 20, 30, 255)


class TestDecodePng:
    """Round-trip: encode then decode recovers RGB pixels (alpha dropped)."""

    def test_decode_png_roundtrip(self) -> None:
        grid = [
            [(255, 0, 0, 255), (0, 255, 0, 128)],
            [(0, 0, 255, 0), (10, 20, 30, 255)],
        ]
        png = encode_png(grid)
        pixels, width, height = decode_png(png)
        assert (width, height) == (2, 2)
        assert pixels == [
            (255, 0, 0),
            (0, 255, 0),
            (0, 0, 255),
            (10, 20, 30),
        ]


class TestDecodeJpeg:
    """JPEG decode via Pillow (MiniMax image-01 returns JPEG, not PNG)."""

    def test_decode_jpeg_roundtrip(self) -> None:
        pytest.importorskip("PIL")
        import io

        from PIL import Image

        img = Image.new("RGB", (8, 8), (200, 30, 30))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)

        pixels, width, height = decode_jpeg(buf.getvalue())
        assert (width, height) == (8, 8)
        assert len(pixels) == 64
        # JPEG is lossy; assert channel dominance, not exact values.
        for r, g, b in pixels:
            assert r > 150 and g < 100 and b < 100

    def test_jpeg_magic_is_ffd8ff(self) -> None:
        pytest.importorskip("PIL")
        import io

        from PIL import Image

        img = Image.new("RGB", (4, 4), (1, 2, 3))
        buf = io.BytesIO()
        img.save(buf, format="JPEG")
        assert buf.getvalue()[:3] == b"\xff\xd8\xff"


class TestDecodeImage:
    """Format sniffing: PNG → decode_png, JPEG → decode_jpeg, else ValueError."""

    def test_sniffs_png(self) -> None:
        png = encode_png([[(10, 20, 30, 255)]])
        pixels, width, height = decode_image(png)
        assert (width, height) == (1, 1)
        assert pixels == [(10, 20, 30)]

    def test_sniffs_jpeg(self) -> None:
        pytest.importorskip("PIL")
        import io

        from PIL import Image

        img = Image.new("RGB", (8, 8), (200, 0, 0))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        pixels, width, height = decode_image(buf.getvalue())
        assert (width, height) == (8, 8)
        assert pixels[0][0] > 150 and pixels[0][1] < 100

    def test_unknown_format_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported image format"):
            decode_image(b"\x00\x01\x02\x03not-an-image")


class TestJpegPipeline:
    """End-to-end JPEG path: decode → downsample → quantize yields a sprite.

    This is the benchmark for the MiniMax adapter — before ``decode_image``
    existed, a JPEG response hit ``decode_png`` and died on the PNG-signature
    assert. Here a synthetic MiniMax-style JPEG (dark blob on white) must
    survive decode + downsample + quantize with a non-trivial foreground.
    """

    def test_jpeg_decodes_into_valid_sprite(self) -> None:
        pytest.importorskip("PIL")
        import io

        from PIL import Image, ImageDraw

        img = Image.new("RGB", (64, 64), (255, 255, 255))
        draw = ImageDraw.Draw(img)
        draw.ellipse([16, 16, 48, 48], fill=(10, 20, 30))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)

        pixels, width, height = decode_image(buf.getvalue())
        assert (width, height) == (64, 64)

        grid = downsample(pixels, width, height, target=16)
        rgba_grid, palette = quantize(grid, palette=16)

        opaque = [c for row in rgba_grid for c in row if c[3] == 255]
        assert len(palette) <= 16
        assert len(opaque) > 0, "foreground collapsed to background"
        # The dark blob must survive JPEG lossiness — a couple of cells, not
        # the whole grid, but never zero.
        assert len(opaque) >= 4, f"foreground too small after JPEG: {len(opaque)}"
