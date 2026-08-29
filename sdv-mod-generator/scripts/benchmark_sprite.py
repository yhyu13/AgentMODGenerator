"""Benchmark sprite post-processing: does downsample+quantize turn a
generated image into a game-usable sprite?

Three numbers, each with a baseline:
  1. unique_colors:  source image vs quantized sprite (target <= 16)
  2. flat_16x16:     fraction of 16x16 blocks that are single-color
                     (source is ~1-25%, quantized target 100%)
  3. foreground_cells: opaque cells in the 16x16 sprite (shape survival)

Usage: py scripts/benchmark_sprite.py <path-to-generated.png>
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from generators.packs.stardew_valley.sprite_utils import (  # noqa: E402
    decode_png,
    downsample,
    quantize,
)


def flat_ratio(
    pixels: list[tuple[int, int, int]], width: int, height: int, block: int = 16
) -> float:
    """Fraction of block×block cells that are a single color."""
    flat = total = 0
    for by in range(0, height, block):
        for bx in range(0, width, block):
            total += 1
            first = pixels[by * width + bx]
            if all(
                pixels[(by + dy) * width + (bx + dx)] == first
                for dy in range(block)
                for dx in range(block)
                if by + dy < height and bx + dx < width
            ):
                flat += 1
    return flat / total


def main() -> None:
    png = Path(sys.argv[1]).read_bytes()
    pixels, width, height = decode_png(png)
    src_colors = len(set(pixels))
    src_flat = flat_ratio(pixels, width, height)

    grid = downsample(pixels, width, height, target=16)
    rgba, palette = quantize(grid, palette=16)
    after_colors = len({c for row in rgba for c in row})
    foreground = sum(1 for row in rgba for c in row if c[3] == 255)

    print(f"source: {width}x{height}, unique_colors={src_colors}, "
          f"flat_16x16={src_flat:.1%}")
    print(f"sprite: 16x16, unique_colors={after_colors}, "
          f"palette_size={len(palette)}, foreground_cells={foreground}")
    ok = after_colors <= 16 and foreground > 0
    print(f"verdict: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
