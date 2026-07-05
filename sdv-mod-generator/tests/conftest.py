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
        # v111 — clear the new env-var targets of the
        # ``discord_app_id_valid`` and ``api_key_configured`` bool
        # wrappers so the v111 "unset is False" tests reach their
        # ``False`` default deterministically across hosts (e.g.
        # a developer with a real ``API_KEY`` in their local
        # ``.env`` would otherwise see ``api_key_configured is True``
        # in the unset-default test). Same pattern as the
        # pre-existing ``DISCORD_BOT_TOKEN`` entry — the v110
        # ``discord_bot_configured`` tests rely on that one. The
        # v111 tests do belt-and-suspenders ``monkeypatch.delenv``
        # inside each test so the conftest addition is purely a
        # safety net (the in-test delenv is the primary defense).
        "DISCORD_APP_ID",
        "API_KEY",
        # v113 — clear the new env-var target of the
        # ``api_owner_configured`` bool wrapper so the v113
        # "unset is False" test reaches its ``False`` default
        # deterministically across hosts (e.g. a developer with
        # a real ``API_OWNER_USER_ID`` in their local ``.env``
        # would otherwise see ``api_owner_configured is True`` in
        # the unset-default test). Same pattern as the v110 +
        # v111 conftest entries above for ``DISCORD_BOT_TOKEN``,
        # ``DISCORD_APP_ID``, and ``API_KEY``. The v113 tests do
        # belt-and-suspenders ``monkeypatch.delenv`` inside each
        # test so the conftest addition is purely a safety net
        # (the in-test delenv is the primary defense).
        "API_OWNER_USER_ID",
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
