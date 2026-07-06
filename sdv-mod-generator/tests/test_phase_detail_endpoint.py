"""Tests for the GET /v1/mods/phases/{phase_id} endpoint.

Pins the handler-direct behaviour of ``get_phase_detail``
(routes.py:947) — first-hit-wins, the ``matched=False`` graceful
degrade, the defensive ``None`` skip, the empty-registry sentinel.
Schema invariants live in ``test_phase_detail_response_schema.py``.

Hermetic: patches ``generators.core.list_game_packs`` and
``generators.core.get_game_pack`` directly (the handler imports
those names at call time inside its function body, routes.py:983).
Mirrors the recipe in ``test_known_phases.py`` / ``test_list_packs.py``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.api.schemas import PhaseDetailResponse


def _pack(
    phases: list[str], *, game_id: str, execution_order: list[str]
) -> SimpleNamespace:
    """Fake ``GamePack`` whose 3 handler-called methods return the
    supplied values. The handler reads ``game_id`` / ``display_name`` /
    ``mod_format`` via ``getattr`` (routes.py:1043-1045) and
    ``execution_order`` off the ``PhaseGenerators`` (routes.py:1035).
    """
    return SimpleNamespace(
        list_phases=lambda: list(phases),
        get_manifest=lambda: SimpleNamespace(
            game_id=game_id,
            display_name=f"Display {game_id}",
            mod_format=f"Format {game_id}",
        ),
        get_generators=lambda _p: SimpleNamespace(
            execution_order=list(execution_order)
        ),
    )


class TestPhaseDetailMatchedHappyPath:
    """Matched-phase path: all 9 envelope fields populated."""

    async def test_matched_phase_populates_all_fields(self):
        from app.api.routes import get_phase_detail

        pack = _pack(
            ["shop_channel"],
            game_id="stardew_valley",
            execution_order=["texture_gen", "shop_channel_gen"],
        )
        with patch(
            "generators.core.list_game_packs", return_value=["stardew_valley"]
        ), patch("generators.core.get_game_pack", return_value=pack):
            result = await get_phase_detail("shop_channel")

        assert isinstance(result, PhaseDetailResponse)
        assert result.matched is True
        assert result.phase == "shop_channel"
        assert result.game_id == "stardew_valley"
        assert result.display_name == "Display stardew_valley"
        assert result.mod_format == "Format stardew_valley"
        assert result.execution_order == ["texture_gen", "shop_channel_gen"]
        assert result.generator_count == 2
        # Live ``app.estimation`` values — pinned loosely so this file
        # does not couple to the ``_PHASE_SECONDS`` table.
        assert result.estimated_seconds > 0
        assert result.default_seconds > 0


class TestPhaseDetailUnknownPhase:
    """Unmatched phase id → ``matched=False`` graceful degrade."""

    async def test_unknown_phase_returns_matched_false_with_defaults(self):
        from app.api.routes import get_phase_detail

        # Pack lists shop_channel, not the asked-for phase.
        pack = _pack(["shop_channel"], game_id="x", execution_order=[])
        with patch(
            "generators.core.list_game_packs", return_value=["x"]
        ), patch("generators.core.get_game_pack", return_value=pack):
            result = await get_phase_detail("nonexistent_phase")

        assert result.matched is False
        assert result.phase == "nonexistent_phase"
        assert result.game_id == ""
        assert result.display_name == ""
        assert result.mod_format == ""
        assert result.execution_order == []
        assert result.generator_count == 0
        # ``estimated_seconds`` falls back to ``_DEFAULT_SECONDS`` —
        # useful precisely because callers can render "no specific
        # estimate, default N seconds".
        assert result.estimated_seconds == result.default_seconds


class TestPhaseDetailRegistryEdgeCases:
    """Defensive branch: pack id listed but not resolvable."""

    async def test_unresolvable_pack_id_yields_matched_false(self):
        from app.api.routes import get_phase_detail

        with patch(
            "generators.core.list_game_packs", return_value=["missing_pack"]
        ), patch("generators.core.get_game_pack", return_value=None):
            result = await get_phase_detail("shop_channel")

        assert result.matched is False
        assert result.game_id == ""
        assert result.execution_order == []
        assert result.generator_count == 0


class TestPhaseDetailFirstHitWins:
    """Two packs registering the same phase → only the first wins."""

    async def test_first_pack_with_phase_wins(self):
        from app.api.routes import get_phase_detail

        pack_a = _pack(["shop_channel"], game_id="pack_a",
                        execution_order=["gen_a1", "gen_a2"])
        pack_b = _pack(["shop_channel"], game_id="pack_b",
                        execution_order=["gen_b1", "gen_b2"])
        # ``get_game_pack`` returns different packs per ``pack_id``
        # arg — the handler walks ``list_game_packs()`` and calls
        # ``get_game_pack(pack_id)`` per id.
        def _resolver(pack_id: str) -> SimpleNamespace:
            return {"a": pack_a, "b": pack_b}[pack_id]

        with patch(
            "generators.core.list_game_packs", return_value=["a", "b"]
        ), patch("generators.core.get_game_pack", side_effect=_resolver):
            result = await get_phase_detail("shop_channel")

        assert result.matched is True
        assert result.game_id == "pack_a"
        assert result.display_name == "Display pack_a"
        assert result.execution_order == ["gen_a1", "gen_a2"]
        # Second pack's identifiers must NOT leak into the response.
        assert "pack_b" not in result.game_id
        assert "gen_b1" not in result.execution_order


class TestPhaseDetailEmptyRegistry:
    """Belt-and-suspenders sentinel: no packs registered."""

    async def test_empty_registry_yields_matched_false(self):
        from app.api.routes import get_phase_detail

        with patch("generators.core.list_game_packs", return_value=[]):
            result = await get_phase_detail("shop_channel")

        assert result.matched is False
        assert result.game_id == ""
        assert result.display_name == ""
        assert result.mod_format == ""
        assert result.execution_order == []
        assert result.generator_count == 0
        assert result.estimated_seconds > 0