"""Generate a deterministic sprite mod zip for the real SMAPI load gate.

Usage: py scripts/generate_sprite_demo.py [dest_zip]

Produces a Content Patcher mod (manifest.json + content.json + Assets/sprite.png)
using the deterministic sprite sample, so the real-game load test can run
without an image API key.
"""
import asyncio
import json
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["SPRITE_DETERMINISTIC"] = "1"

from generators.core import GeneratorInput  # noqa: E402
from generators.packs.stardew_valley.features.sprite import SpriteGenerator  # noqa: E402


def _inp() -> GeneratorInput:
    return {
        "prompt": "a glowing blue carp fish",
        "hint": {"game": "stardew_valley", "phase": "sprite"},
        "request_id": "sprite_demo",
        "game": "stardew_valley",
        "prior_outputs": {},
        "t2_feedback": "",
    }


async def _generate() -> tuple[dict, dict, bytes]:
    out = await SpriteGenerator().generate(_inp())
    return out.files["manifest.json"], out.files["content.json"], out.files["Assets/sprite.png"]


def main() -> None:
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        Path(__file__).resolve().parent.parent / "mods" / "sprite_demo.zip"
    )
    manifest, content, png = asyncio.run(_generate())
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, indent=2))
        zf.writestr("content.json", json.dumps(content, indent=2))
        zf.writestr("Assets/sprite.png", png)
    print(f"wrote {dest} ({dest.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
