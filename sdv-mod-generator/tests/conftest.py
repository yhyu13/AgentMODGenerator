"""Pytest fixtures and shared test setup."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# Tests assume a deterministic environment: no LLM provider configured
# (so the T2 gate falls through to "no LLM" instead of trying to call
# out), no live SOCKS proxy (avoids httpx 'socksio not installed' errors
# on hosts where ALL_PROXY is set), no real Discord token, and prod
# secrets check disabled. Importing app.config would call load_dotenv
# and pull all of this in from config/.env, so we clear it before any
# test module is collected.
@pytest.fixture(autouse=True)
def _isolate_test_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "DISCORD_BOT_TOKEN",
        "ALL_PROXY",
        "all_proxy",
    ):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def shop_channel_prompt() -> str:
    return "make a TV shopping channel that sells seeds on Sundays"


@pytest.fixture
def texture_prompt() -> str:
    return "replace the parsnip crop sprite"


@pytest.fixture
def sdv_item_names() -> list[str]:
    return [
        "Parsnip Seeds", "Melon Seeds", "Pumpkin Seeds", "Crystalarium",
        "Auto-Grabber", "Deluxe Speed-Gro", "Quality Fertilizer",
        "BigCraftable", "Scarecrow", "Brick Floor",
    ]


@pytest.fixture
def sample_shops_tsv() -> str:
    return (
        "ItemType\tItemName\tItemName2\tPrice\tStock\n"
        "Object\tParsnip Seeds\t\t50\t10\n"
        "Object\tMelon Seeds\t\t250\t5\n"
    )


@pytest.fixture
def sample_manifest() -> dict:
    return {
        "Format": "1.29.0",
        "UniqueID": "tv_shopping_network",
        "Name": "TV Shopping Network",
        "Author": "AI Generator",
        "Version": "1.0.0",
        "Description": "A TV shopping channel.",
        "ContentPackFor": {"UniqueID": "Pathoschild.ContentPatcher"},
    }


@pytest.fixture
def malformed_manifest() -> dict:
    return {
        "Format": "1.29.0",
        "Name": "TV Shopping Network",
    }
