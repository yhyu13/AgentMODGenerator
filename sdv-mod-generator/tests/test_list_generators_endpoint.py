"""Tests for ``GET /v1/mods/generators`` — schema + handler.

Pinned: schema invariants (``execution_position`` is unconstrained
``int`` — no ``ge=0``); handler happy path on real ``stardew_valley``
pack; 404 for unknown ``game`` / ``phase`` (distinct detail strings);
defensive 404 when ``get_generators(phase)`` raises ``ValueError``
(NOT a 500); echo invariant that ``response.game`` / ``response.phase``
mirror the query parameters exactly.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routes import list_generators


class TestGeneratorInfoSchema:
    """Pydantic invariants for :class:`GeneratorInfo`."""

    def test_basic_round_trip(self) -> None:
        from app.api.schemas import GeneratorInfo

        g = GeneratorInfo(
            name="shop_item_pool_generator",
            phase="shop_channel",
            game="stardew_valley",
            execution_position=1,
        )
        assert g.name == "shop_item_pool_generator"
        assert g.phase == "shop_channel"
        assert g.game == "stardew_valley"
        assert g.execution_position == 1

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"phase": "p", "game": "g", "execution_position": 0},
            {"name": "n", "game": "g", "execution_position": 0},
            {"name": "n", "phase": "p", "execution_position": 0},
            {"name": "n", "phase": "p", "game": "g"},
        ],
    )
    def test_each_field_is_required(self, kwargs) -> None:
        # Each of the four fields is required. Parametrised so a
        # future refactor adding a default fails loudly.
        from app.api.schemas import GeneratorInfo

        with pytest.raises(ValidationError):
            GeneratorInfo(**kwargs)  # type: ignore[call-arg]

    def test_execution_position_negative_is_accepted(self) -> None:
        # ``execution_position`` is a bare ``Field(...)`` with no
        # ``ge=0`` — negative value accepted. Handler always
        # produces values in ``range(len(execution_order))`` so
        # negatives cannot appear in production traffic; pinning
        # so a future refactor adding ``ge=0`` is deliberate.
        from app.api.schemas import GeneratorInfo

        g = GeneratorInfo(
            name="x", phase="p", game="g", execution_position=-1,
        )
        assert g.execution_position == -1


class TestGeneratorsResponseSchema:
    """Pydantic invariants for :class:`GeneratorsResponse`."""

    def test_basic_round_trip(self) -> None:
        from app.api.schemas import GeneratorInfo, GeneratorsResponse

        resp = GeneratorsResponse(
            game="stardew_valley",
            phase="shop_channel",
            generators=[
                GeneratorInfo(
                    name="g1", phase="shop_channel",
                    game="stardew_valley", execution_position=0,
                ),
            ],
        )
        assert resp.game == "stardew_valley"
        assert resp.phase == "shop_channel"
        assert len(resp.generators) == 1
        assert resp.generators[0].execution_position == 0

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"phase": "p", "generators": []},
            {"game": "g", "generators": []},
            {"game": "g", "phase": "p"},
        ],
    )
    def test_each_field_is_required(self, kwargs) -> None:
        # All three top-level fields required. Parametrised so a
        # future refactor adding a default fails loudly.
        from app.api.schemas import GeneratorsResponse

        with pytest.raises(ValidationError):
            GeneratorsResponse(**kwargs)  # type: ignore[call-arg]


class TestListGeneratorsEndpoint:
    """In-process tests for the ``list_generators`` handler."""

    async def test_shop_channel_returns_eleven_generators(self) -> None:
        # Pin count + first/last so a future re-ordering in
        # ``stardew_valley/__init__.py`` is a deliberate change.
        result = await list_generators(
            game="stardew_valley", phase="shop_channel",
        )
        assert result.game == "stardew_valley"
        assert result.phase == "shop_channel"
        assert len(result.generators) == 11
        assert result.generators[0].name == "manifest_generator"
        assert result.generators[0].execution_position == 0
        assert result.generators[-1].name == "content_json_generator"
        assert result.generators[-1].execution_position == 10

    async def test_execution_position_matches_enumerate(self) -> None:
        # ``execution_position`` is the 0-based index in
        # ``execution_order``. Handler builds via
        # ``enumerate(execution_order)``.
        result = await list_generators(
            game="stardew_valley", phase="weather_event",
        )
        for idx, g in enumerate(result.generators):
            assert g.execution_position == idx

    async def test_echo_invariant_for_every_generator(self) -> None:
        # Handler fills ``phase`` / ``game`` from the closure over
        # the query parameters, NOT from the pack's own state.
        # Pinned per-entry so a future refactor reusing a cached
        # ``GeneratorInfo`` from another phase fails loudly.
        result = await list_generators(
            game="stardew_valley", phase="custom_crafting",
        )
        assert len(result.generators) == 3
        for g in result.generators:
            assert g.game == "stardew_valley"
            assert g.phase == "custom_crafting"

    async def test_unknown_game_returns_404(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await list_generators(game="nope", phase="shop_channel")
        assert exc_info.value.status_code == 404
        assert "Unknown game pack: nope" in str(exc_info.value.detail)

    async def test_unknown_phase_returns_404(self) -> None:
        # Distinct detail string from the "unknown game" 404 —
        # collapsing the two is a deliberate change.
        with pytest.raises(HTTPException) as exc_info:
            await list_generators(
                game="stardew_valley", phase="not_a_phase",
            )
        assert exc_info.value.status_code == 404
        assert "not_a_phase" in str(exc_info.value.detail)
        assert "Unknown game pack" not in str(exc_info.value.detail)

    async def test_get_generators_value_error_returns_404(self) -> None:
        # Defensive: pack advertises phase in ``list_phases()`` but
        # ``get_generators()`` raises ``ValueError`` — 404 (NOT 500).
        fake_pack = MagicMock()
        fake_pack.list_phases.return_value = ["tricky_phase"]
        fake_pack.get_generators.side_effect = ValueError(
            "phase not registered",
        )
        with patch(
            "generators.core.get_game_pack",
            return_value=fake_pack,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await list_generators(
                    game="tricky_pack", phase="tricky_phase",
                )
        assert exc_info.value.status_code == 404
        assert "not available" in str(exc_info.value.detail)
        assert "tricky_phase" in str(exc_info.value.detail)

    async def test_single_generator_phase(self) -> None:
        # ``texture`` is a single-generator phase. Pinned so a
        # future expansion to multiple generators is deliberate.
        result = await list_generators(
            game="stardew_valley", phase="texture",
        )
        assert len(result.generators) == 1
        assert result.generators[0].name == "texture_generator"
        assert result.generators[0].execution_position == 0
        assert result.generators[0].game == "stardew_valley"
        assert result.generators[0].phase == "texture"