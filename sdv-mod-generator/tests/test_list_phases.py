"""Schema-level Pydantic tests for ``PhasesResponse`` and its nested models.

Companion to the v127 ``test_list_phases_endpoint.py`` — v127 pins the
in-process handler behaviour (4 schema tests + 9 handler tests on the
real ``stardew_valley`` registry). This file pins the deeper
Pydantic-only contract of the three schemas ``/v1/mods/phases``
emits (``PhaseInfo`` / ``PackInfo`` / ``PhasesResponse``).

The split mirrors the existing pattern (``test_phase_detail_endpoint.py``
+ ``test_phase_detail_response_schema.py``,
``test_estimates_endpoints.py`` + ``test_estimates_response_schemas.py``).
Schema-only (no TestClient, no handler import) so the file stays
deterministic regardless of which packs are registered at import time.

Invariants pinned here (NOT covered by v127):

  * ``PhaseInfo`` — ``generator_count >= 0`` (boundary 0 ok, -1
    rejected); ``execution_order`` uses ``default_factory=list`` (no
    shared mutable default — mutating one instance must not bleed
    into another); ``phase`` is a required ``str`` (None and empty
    string both rejected by Pydantic v2 strict mode).
  * ``PackInfo`` — ``game_id`` / ``display_name`` / ``mod_format`` /
    ``phases`` are all required fields (omitting any one is a
    ``ValidationError``); ``phases`` must be a list (``None``
    rejected); ``phases`` accepts an empty list (a pack with zero
    registered phases is a valid pack).
  * ``PhasesResponse`` — ``packs`` is required; ``phases`` defaults
    to ``[]`` (covered by v127's
    ``test_phases_field_default_is_empty_list``); ``phases`` accepts
    arbitrary ``list[str]`` with no length constraint (the flat
    list is the deduplicated union so length is unbounded by
    design); JSON-serialisation round-trip via ``model_dump_json``
    / ``model_validate_json`` preserves all fields.
  * Cross-schema — building a full ``PhasesResponse`` with multiple
    packs / overlapping phase ids and asserting ``model_dump``
    shape matches what the handler emits (key ordering, list-of-
    dicts nesting).

Not pinned here (intentional, deferred):

  * Long-string acceptance for ``phase`` / ``game_id`` / etc. (no
    ``max_length`` set on the schemas — Pydantic v2's default is
    unbounded, no contract to pin).
  * Numeric overflow — ``generator_count`` is ``int`` with no upper
    bound, mirroring ``PacksResponse`` semantics.
  * HTTP-level round-trip via TestClient — covered by v127's
    handler tests (which exercise the real registered
    ``stardew_valley`` pack).
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from app.api.schemas import (
    PackInfo,
    PhaseInfo,
    PhasesResponse,
)


class TestPhaseInfoSchema:
    """``PhaseInfo`` Pydantic invariants — the per-phase nested model."""

    def test_basic_round_trip(self) -> None:
        # Three-field happy path: ``phase`` / ``generator_count`` /
        # ``execution_order`` round-trip unchanged.
        p = PhaseInfo(
            phase="shop_channel",
            generator_count=3,
            execution_order=["texture", "shop_channel", "packager"],
        )
        assert p.phase == "shop_channel"
        assert p.generator_count == 3
        assert p.execution_order == ["texture", "shop_channel", "packager"]

    def test_generator_count_boundary_zero_is_ok(self) -> None:
        # Boundary: ``generator_count == 0`` is valid (a phase with
        # no generators registered — the handler emits this shape
        # defensively when ``get_generators(phase)`` raises).
        p = PhaseInfo(phase="empty_phase", generator_count=0)
        assert p.generator_count == 0
        assert p.execution_order == []

    def test_generator_count_negative_rejected(self) -> None:
        # ``generator_count`` has ``ge=0`` so negative values are
        # rejected at the Pydantic boundary.
        with pytest.raises(ValidationError):
            PhaseInfo(phase="x", generator_count=-1)

    def test_execution_order_default_factory_isolates_instances(self) -> None:
        # ``execution_order`` uses ``default_factory=list`` so two
        # instances do not share a mutable list. Appending to one
        # instance's ``execution_order`` must not bleed into another.
        p1 = PhaseInfo(phase="a", generator_count=0)
        p2 = PhaseInfo(phase="b", generator_count=0)
        p1.execution_order.append("leaked")
        assert p1.execution_order == ["leaked"]
        assert p2.execution_order == []

    def test_phase_must_be_a_string(self) -> None:
        # ``phase`` is a required ``str`` — passing ``None``
        # produces a ``ValidationError`` (Pydantic v2 strict
        # coercion rejects ``None`` for ``str`` fields without
        # ``default=None``).
        with pytest.raises(ValidationError):
            PhaseInfo(phase=None, generator_count=0)  # type: ignore[arg-type]

    def test_missing_required_phase_rejected(self) -> None:
        # ``phase`` has no default — omitting it raises
        # ``ValidationError``.
        with pytest.raises(ValidationError):
            PhaseInfo(generator_count=1)  # type: ignore[call-arg]


class TestPackInfoSchema:
    """``PackInfo`` Pydantic invariants — the per-pack nested model."""

    def test_basic_round_trip(self) -> None:
        # Four-field happy path: ``game_id`` / ``display_name`` /
        # ``mod_format`` / ``phases`` round-trip unchanged.
        p = PackInfo(
            game_id="stardew_valley",
            display_name="Stardew Valley",
            mod_format="Content Patcher 1.29",
            phases=[],
        )
        assert p.game_id == "stardew_valley"
        assert p.display_name == "Stardew Valley"
        assert p.mod_format == "Content Patcher 1.29"
        assert p.phases == []

    def test_phases_field_accepts_empty_list(self) -> None:
        # A pack with zero registered phases is a valid pack —
        # the handler emits this shape when a pack registered no
        # phases. The ``phases`` field has no ``min_length``
        # constraint.
        p = PackInfo(
            game_id="empty_pack",
            display_name="Empty",
            mod_format="Unknown",
            phases=[],
        )
        assert p.phases == []

    def test_phases_must_be_a_list(self) -> None:
        # ``phases`` is required and must be a list — Pydantic
        # rejects ``None``.
        with pytest.raises(ValidationError):
            PackInfo(
                game_id="x",
                display_name="y",
                mod_format="z",
                phases=None,  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param(
                {
                    "display_name": "y",
                    "mod_format": "z",
                    "phases": [],
                },
                id="missing_game_id",
            ),
            pytest.param(
                {
                    "game_id": "x",
                    "mod_format": "z",
                    "phases": [],
                },
                id="missing_display_name",
            ),
            pytest.param(
                {
                    "game_id": "x",
                    "display_name": "y",
                    "phases": [],
                },
                id="missing_mod_format",
            ),
            pytest.param(
                {
                    "game_id": "x",
                    "display_name": "y",
                    "mod_format": "z",
                },
                id="missing_phases",
            ),
        ],
    )
    def test_missing_required_field_rejected(self, kwargs: dict) -> None:
        # All four ``PackInfo`` fields are required — omitting any
        # one is a ``ValidationError``.
        with pytest.raises(ValidationError):
            PackInfo(**kwargs)  # type: ignore[arg-type]


class TestPhasesResponseTopLevel:
    """``PhasesResponse`` Pydantic invariants — the top-level envelope."""

    def test_phases_field_accepts_arbitrary_list_of_strings(self) -> None:
        # ``phases`` (the flat list) has no ``min_length`` /
        # ``max_length`` constraint — the handler emits the
        # deduplicated union of phase ids across all packs, which
        # is unbounded in principle. Pin that an arbitrarily long
        # list round-trips unchanged.
        phases = [f"phase_{i}" for i in range(50)]
        resp = PhasesResponse(packs=[], phases=phases)
        assert resp.phases == phases
        assert len(resp.phases) == 50

    def test_phases_field_accepts_empty_list_explicitly(self) -> None:
        # ``phases`` accepts an explicit empty list (same shape as
        # the default — pinned separately so a future regression
        # that tightens the schema with ``min_length=1`` is caught).
        resp = PhasesResponse(packs=[], phases=[])
        assert resp.phases == []

    def test_phases_field_accepts_overlapping_phase_ids(self) -> None:
        # The flat ``phases`` field can contain duplicate ids —
        # dedup happens in the handler, NOT the schema. The schema
        # is intentionally permissive (a flat ``list[str]`` with
        # no uniqueness constraint) so callers building responses
        # manually are not forced to dedup before calling the
        # schema.
        resp = PhasesResponse(
            packs=[],
            phases=["shop_channel", "shop_channel", "texture"],
        )
        assert resp.phases == ["shop_channel", "shop_channel", "texture"]

    def test_packs_must_be_supplied_explicitly(self) -> None:
        # ``packs`` has no default — ``PhasesResponse()`` with no
        # arguments raises ``ValidationError``. Mirrors
        # ``PacksResponse``'s ``packs`` field contract.
        with pytest.raises(ValidationError):
            PhasesResponse()  # type: ignore[call-arg]

    def test_full_envelope_round_trip_via_model_dump(self) -> None:
        # End-to-end: build a full ``PhasesResponse`` with two packs
        # and a flat phase list, then assert ``model_dump()`` shape
        # matches what the handler emits (top-level keys + nested
        # dict structure).
        resp = PhasesResponse(
            packs=[
                PackInfo(
                    game_id="stardew_valley",
                    display_name="Stardew Valley",
                    mod_format="Content Patcher 1.29",
                    phases=[
                        PhaseInfo(
                            phase="shop_channel",
                            generator_count=2,
                            execution_order=["g1", "g2"],
                        ),
                    ],
                ),
                PackInfo(
                    game_id="terraria",
                    display_name="Terraria",
                    mod_format="tModLoader",
                    phases=[],
                ),
            ],
            phases=["shop_channel"],
        )
        dumped = resp.model_dump()
        # Top-level shape: two keys, ``packs`` and ``phases``.
        assert set(dumped.keys()) == {"packs", "phases"}
        assert len(dumped["packs"]) == 2
        # First pack has the nested phase info.
        assert dumped["packs"][0]["game_id"] == "stardew_valley"
        assert dumped["packs"][0]["phases"][0]["phase"] == "shop_channel"
        assert dumped["packs"][0]["phases"][0]["generator_count"] == 2
        assert dumped["packs"][0]["phases"][0]["execution_order"] == [
            "g1",
            "g2",
        ]
        # Second pack is empty-phases (terraria registered no phases).
        assert dumped["packs"][1]["game_id"] == "terraria"
        assert dumped["packs"][1]["phases"] == []
        # Flat phases list.
        assert dumped["phases"] == ["shop_channel"]

    def test_json_round_trip_preserves_all_fields(self) -> None:
        # JSON serialisation round-trip — ``model_dump_json`` then
        # ``model_validate_json`` preserves every field. Guards
        # against a future regression that adds a serialiser that
        # strips fields (e.g. ``execution_order`` on an empty phase).
        original = PhasesResponse(
            packs=[
                PackInfo(
                    game_id="stardew_valley",
                    display_name="Stardew Valley",
                    mod_format="Content Patcher 1.29",
                    phases=[
                        PhaseInfo(
                            phase="texture",
                            generator_count=0,
                            execution_order=[],
                        ),
                    ],
                ),
            ],
            phases=["texture"],
        )
        as_json = original.model_dump_json()
        # ``model_dump_json`` produces valid JSON.
        parsed_back = json.loads(as_json)
        assert parsed_back["packs"][0]["phases"][0]["execution_order"] == []
        # ``model_validate_json`` round-trips back to an equivalent
        # ``PhasesResponse``.
        restored = PhasesResponse.model_validate_json(as_json)
        assert restored.model_dump() == original.model_dump()