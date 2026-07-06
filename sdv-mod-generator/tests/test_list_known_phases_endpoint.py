"""Tests for ``GET /v1/mods/phases/known``.

Schema-level invariants for ``KnownPhasesResponse`` plus in-process
handler tests for ``list_known_phases``. The endpoint is a thin alias
for ``PhasesResponse.phases`` — same sorted/deduped union of phase ids
across all registered packs, without the per-pack breakdown. The
``count`` field mirrors ``len(phases)`` (handler-enforced, NOT
schema-enforced — pinned here).

The three read-only phase endpoints form a family:
    * ``/v1/mods/phases`` — full per-pack breakdown
    * ``/v1/mods/phases/known`` — flat phase list (this file)
    * ``/v1/mods/phases/{phase_id}`` — single-phase detail

Unlike ``list_phases``, this handler does NOT call ``get_generators``
on any pack — it only walks ``pack.list_phases()`` — so the
``ValueError``-on-``get_generators`` defensive path cannot fire here.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.api.schemas import KnownPhasesResponse


class TestKnownPhasesResponseSchema:
    """Pydantic invariants for ``KnownPhasesResponse``."""

    def test_basic_round_trip(self) -> None:
        resp = KnownPhasesResponse(phases=["shop_channel"], count=1)
        assert resp.phases == ["shop_channel"]
        assert resp.count == 1

    def test_empty_phases_with_zero_count(self) -> None:
        # ``phases=[]`` + ``count=0`` is valid (no packs registered).
        resp = KnownPhasesResponse(phases=[], count=0)
        assert resp.phases == []
        assert resp.count == 0

    def test_count_must_be_non_negative(self) -> None:
        # ``Field(ge=0)`` rejects -1.
        with pytest.raises(ValidationError):
            KnownPhasesResponse(phases=["shop_channel"], count=-1)

    def test_count_len_mismatch_is_schema_accepted(self) -> None:
        # The schema does NOT enforce ``count == len(phases)`` — that
        # invariant is the handler's responsibility. Pin that contract
        # so a future refactor adding a model_validator is a
        # deliberate, visible change.
        resp = KnownPhasesResponse(phases=["a", "b"], count=0)
        assert resp.phases == ["a", "b"]
        assert resp.count == 0

    def test_phases_is_required(self) -> None:
        with pytest.raises(ValidationError):
            KnownPhasesResponse(count=0)  # type: ignore[call-arg]

    def test_count_is_required(self) -> None:
        with pytest.raises(ValidationError):
            KnownPhasesResponse(phases=["a"])  # type: ignore[call-arg]

    def test_phases_must_be_a_list(self) -> None:
        # ``phases`` has no default — ``None`` is rejected (callers
        # cannot accidentally substitute ``None`` for an empty list).
        with pytest.raises(ValidationError):
            KnownPhasesResponse(phases=None, count=0)  # type: ignore[arg-type]

    def test_phases_must_contain_strings(self) -> None:
        # ``phases`` is ``list[str]`` — non-string entries are rejected
        # by Pydantic v2 strict mode.
        with pytest.raises(ValidationError):
            KnownPhasesResponse(phases=[1, 2], count=2)  # type: ignore[list-item]

    def test_json_round_trip_preserves_fields(self) -> None:
        original = KnownPhasesResponse(
            phases=["shop_channel", "texture", "weather_event"],
            count=3,
        )
        raw = original.model_dump_json()
        restored = KnownPhasesResponse.model_validate_json(raw)
        assert restored == original
        # Raw JSON has the two documented keys (catches a future
        # field rename / alias flip that would silently break clients
        # caching the response).
        as_dict = json.loads(raw)
        assert as_dict["phases"] == [
            "shop_channel",
            "texture",
            "weather_event",
        ]
        assert as_dict["count"] == 3


class TestListKnownPhasesEndpoint:
    """In-process tests for the ``list_known_phases`` handler."""

    async def test_returns_non_empty_phases_for_registered_pack(self) -> None:
        # ``stardew_valley`` is registered on master, so the response
        # must contain at least one phase id; ``count`` mirrors
        # ``len(phases)``.
        from app.api.routes import list_known_phases

        result = await list_known_phases()
        assert len(result.phases) >= 1
        assert result.count == len(result.phases)
        assert result.count >= 1

    async def test_phases_are_sorted_and_deduplicated(self) -> None:
        # Load-bearing for UI dropdowns: sorted lexicographically, and
        # set semantics — a phase id shared by two packs appears
        # exactly once.
        from app.api.routes import list_known_phases

        result = await list_known_phases()
        assert result.phases == sorted(result.phases)
        assert len(result.phases) == len(set(result.phases))

    async def test_count_equals_len_phases_invariant(self) -> None:
        # ``count == len(phases)`` is the handler's contract — pinned
        # separately from the sorted/deduped invariant above.
        from app.api.routes import list_known_phases

        result = await list_known_phases()
        assert result.count == len(result.phases)

    async def test_pack_id_listed_but_unresolvable_is_silently_skipped(
        self,
    ) -> None:
        # Defensive: a pack id advertised by ``list_game_packs()`` but
        # not resolvable via ``get_game_pack()`` is silently skipped
        # rather than raising. Same pattern ``list_phases`` and
        # ``list_packs`` use.
        from unittest.mock import patch

        from app.api.routes import list_known_phases

        with patch(
            "generators.core.list_game_packs",
            return_value=["missing_pack"],
        ), patch(
            "generators.core.get_game_pack",
            return_value=None,
        ):
            result = await list_known_phases()

        assert result.phases == []
        assert result.count == 0

    async def test_empty_registry_returns_empty_lists(self) -> None:
        # No packs registered → ``phases=[]`` + ``count=0`` (no phantom
        # entries).
        from unittest.mock import patch

        from app.api.routes import list_known_phases

        with patch(
            "generators.core.list_game_packs",
            return_value=[],
        ):
            result = await list_known_phases()

        assert result.phases == []
        assert result.count == 0

    async def test_two_packs_sharing_a_phase_are_deduplicated(self) -> None:
        # Dedup invariant: two packs registering the same phase id
        # produce a flat ``phases`` list with that id appearing exactly
        # once. Important for downstream clients that treat the flat
        # list as a deduplicated canonical index of known phases
        # (used to populate UI dropdowns and validate
        # ``POST /v1/mods/generate`` ``phase`` parameters).
        from unittest.mock import MagicMock, patch

        from app.api.routes import list_known_phases

        def _make_pack(game_id: str, phases: list[str]) -> MagicMock:
            pack = MagicMock()
            pack.list_phases.return_value = phases
            return pack

        pack_a = _make_pack("pack_a", ["shop_channel", "weather_event"])
        pack_b = _make_pack("pack_b", ["shop_channel", "texture"])

        with patch(
            "generators.core.list_game_packs",
            return_value=["pack_a", "pack_b"],
        ), patch(
            "generators.core.get_game_pack",
            side_effect=lambda pid: pack_a if pid == "pack_a" else pack_b,
        ):
            result = await list_known_phases()

        # ``shop_channel`` shared by both packs appears once;
        # sorted lexicographically.
        assert result.phases == ["shop_channel", "texture", "weather_event"]
        assert result.count == len(result.phases)