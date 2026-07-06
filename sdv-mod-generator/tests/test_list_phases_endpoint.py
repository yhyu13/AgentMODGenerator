"""Tests for the GET /v1/mods/phases endpoint.

Covers:
- ``PhasesResponse`` Pydantic schema-level invariants (basic
  construction, default empty phases, packs-must-be-list
  rejection, missing-fields rejection).
- ``list_phases`` handler:
    - happy path: at least one registered pack (``stardew_valley``)
      appears with its ``game_id`` / ``display_name`` / ``mod_format``
      and a non-empty ``phases`` list
    - structural invariants: every :class:`PhaseInfo`'s
      ``generator_count == len(execution_order)``, flat ``phases``
      list is the sorted deduplicated union of every phase id
      across all packs
    - defensive: a pack that raises ``ValueError`` on
      ``get_generators`` still appears with ``generator_count=0``
      and an empty ``execution_order`` rather than producing a 500
    - defensive: a pack id that ``list_game_packs`` advertises but
      ``get_game_pack`` returns None for is silently skipped
    - defensive: when ``list_game_packs`` returns an empty list, the
      endpoint returns an empty ``packs`` list and an empty ``phases``
      flat list
    - canonical-order invariant: the flat ``phases`` field is
      ``sorted(...)`` of the union, so two packs exposing the same
      phase ids produce the same flat list (deterministic for
      clients caching the response)
    - dedup invariant: a phase registered by two packs appears
      exactly once in the flat ``phases`` field
- does NOT exercise any DB or Redis call: the endpoint is a static
  read over the registered :class:`GamePack` registry, exactly like
  ``/v1/packs`` and ``/v1/mods/phases/known`` — the three
  endpoints form the read-only phase / pack registry family.
"""
from __future__ import annotations

import pytest


class TestPhasesResponseSchema:
    """Pydantic schema-level tests for PhasesResponse."""

    def test_basic_construction(self):
        """Minimal round-trip: one pack with one phase, flat phases
        field mirrors the single phase id."""
        from app.api.schemas import (
            PhasesResponse,
            PackInfo,
            PhaseInfo,
        )

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
            ],
            phases=["shop_channel"],
        )
        assert len(resp.packs) == 1
        assert resp.packs[0].game_id == "stardew_valley"
        assert resp.packs[0].phases[0].generator_count == 2
        assert resp.phases == ["shop_channel"]

    def test_empty_packs_list_with_empty_flat_phases(self):
        """``phases`` defaults to ``[]`` (via ``default_factory``) —
        no packs registered means the flat list is empty. Same
        convention the ``KnownPhasesResponse`` ``phases`` field uses."""
        from app.api.schemas import PhasesResponse

        resp = PhasesResponse(packs=[])
        assert resp.packs == []
        assert resp.phases == []

    def test_packs_must_be_a_list(self):
        """``packs`` has no default — callers must explicitly pass
        the (possibly empty) list. Pydantic rejects ``None``."""
        from pydantic import ValidationError

        from app.api.schemas import PhasesResponse

        with pytest.raises(ValidationError):
            PhasesResponse(packs=None)  # type: ignore[arg-type]

    def test_phases_field_default_is_empty_list(self):
        """``phases`` defaults to ``[]`` (default_factory=list) so
        callers can omit it. Pydantic does not require it."""
        from app.api.schemas import (
            PhasesResponse,
            PackInfo,
        )

        resp = PhasesResponse(
            packs=[
                PackInfo(
                    game_id="empty_pack",
                    display_name="Empty",
                    mod_format="Unknown",
                    phases=[],
                ),
            ],
        )
        assert resp.phases == []


class TestListPhasesEndpoint:
    """Tests for the list_phases handler."""

    async def test_returns_at_least_one_pack(self):
        """Master registers the ``stardew_valley`` pack on import,
        so the response must always contain at least one pack."""
        from app.api.routes import list_phases

        result = await list_phases()
        assert len(result.packs) >= 1

    async def test_flat_phases_is_sorted_and_deduped(self):
        """The flat ``phases`` field is the sorted deduplicated
        union of every phase id across all packs — the canonical
        list clients use to validate a ``phase`` parameter before
        calling ``POST /v1/mods/generate``. Sorted + deduped is
        load-bearing: a UI dropdown must render in a stable order
        regardless of pack registration order, and a phase shared
        by two packs must appear once (not twice)."""
        from app.api.routes import list_phases

        result = await list_phases()

        # Union of every phase id across every pack, in iteration order.
        union: list[str] = []
        for pack in result.packs:
            for phase_info in pack.phases:
                if phase_info.phase not in union:
                    union.append(phase_info.phase)

        # Handler must dedup (set semantics) AND sort (lexicographic order).
        assert result.phases == sorted(set(union))
        # And the lengths must match — no phantom phases, no missing
        # phases, no duplicates.
        assert len(result.phases) == len(set(union))

    async def test_flat_phases_count_matches_per_pack_union(self):
        """Sanity: the flat ``phases`` length equals the count of
        unique phase ids across all packs. Same invariant the
        ``KnownPhasesResponse`` ``phases`` / ``count`` pair uses."""
        from app.api.routes import list_phases

        result = await list_phases()

        unique_phase_ids = {
            phase_info.phase
            for pack in result.packs
            for phase_info in pack.phases
        }
        assert len(result.phases) == len(unique_phase_ids)

    async def test_phase_info_has_matching_generator_count(self):
        """Every :class:`PhaseInfo` carries a ``generator_count``
        that equals ``len(execution_order)`` — the handler computes
        both from the same ``pg.execution_order`` source, so this
        invariant should always hold for a well-behaved pack."""
        from app.api.routes import list_phases

        result = await list_phases()
        assert len(result.packs) >= 1
        for pack in result.packs:
            for phase_info in pack.phases:
                assert phase_info.generator_count == len(
                    phase_info.execution_order
                )

    async def test_phase_ids_are_non_empty_strings(self):
        """Sanity: every :class:`PhaseInfo.phase` is a non-empty
        ``str``. Guards against a future regression where a pack
        registers with a non-string phase id (e.g. an enum that
        doesn't stringify cleanly)."""
        from app.api.routes import list_phases

        result = await list_phases()
        assert len(result.packs) >= 1
        for pack in result.packs:
            for phase_info in pack.phases:
                assert isinstance(phase_info.phase, str)
                assert phase_info.phase

    async def test_pack_id_listed_but_unresolvable_is_silently_skipped(self):
        """Defensive: a pack id advertised by ``list_game_packs()``
        but not resolvable via ``get_game_pack()`` is silently
        skipped rather than raising.

        Patches the source module (``generators.core``) — the
        handler imports ``list_game_packs`` / ``get_game_pack`` at
        call time inside the function body, so patching the source
        module is the correct target. Same pattern as
        ``test_list_packs.py::test_pack_id_listed_but_unresolvable_is_silently_skipped``.
        """
        from unittest.mock import patch

        from app.api.routes import list_phases

        with patch(
            "generators.core.list_game_packs",
            return_value=["missing_pack"],
        ), patch(
            "generators.core.get_game_pack",
            return_value=None,
        ):
            result = await list_phases()

        # No packs contributed any pack info; the flat phases
        # field collapses to an empty list.
        assert result.packs == []
        assert result.phases == []

    async def test_empty_registry_returns_empty_lists(self):
        """When ``list_game_packs()`` returns an empty list (no
        packs registered), the endpoint returns an empty ``packs``
        list and an empty flat ``phases`` list. The two fields
        collapse together — neither carries a phantom count."""
        from unittest.mock import patch

        from app.api.routes import list_phases

        with patch(
            "generators.core.list_game_packs",
            return_value=[],
        ):
            result = await list_phases()

        assert result.packs == []
        assert result.phases == []

    async def test_pack_that_raises_on_get_generators_is_skipped(self):
        """Defensive: a pack that raises ``ValueError`` on
        ``get_generators(phase)`` (an otherwise-valid pack that
        cannot resolve one of its phases) shows up with
        ``generator_count=0`` and an empty ``execution_order`` for
        that phase rather than producing a 500. Mirrors the
        defensive skip ``list_packs`` uses.

        Builds a fake pack whose ``get_generators`` raises
        ``ValueError`` for the ``texture`` phase and returns a
        normal PhaseGenerators object for ``shop_channel``. The
        flat ``phases`` field still contains both ids — defensive
        skip zeroes out the *generator_count*, not the phase id
        itself (a phase with 0 generators is still a known phase).
        """
        from unittest.mock import MagicMock, patch

        from app.api.routes import list_phases

        # Build a fake pack that:
        #   - exposes get_manifest() with the four manifest fields
        #   - exposes list_phases() returning ["shop_channel", "texture"]
        #   - exposes get_generators("shop_channel") -> PhaseGenerators-like
        #     object with execution_order = ["g1", "g2"]
        #   - exposes get_generators("texture") raising ValueError
        fake_pg_shop = MagicMock()
        fake_pg_shop.execution_order = ["g1", "g2"]

        fake_manifest = MagicMock()
        fake_manifest.game_id = "fake_pack"
        fake_manifest.display_name = "Fake Pack"
        fake_manifest.mod_format = "FakeFormat"

        fake_pack = MagicMock()
        fake_pack.get_manifest.return_value = fake_manifest
        fake_pack.list_phases.return_value = ["shop_channel", "texture"]

        def fake_get_generators(phase):
            if phase == "texture":
                raise ValueError("phase not registered")
            return fake_pg_shop

        fake_pack.get_generators.side_effect = fake_get_generators

        with patch(
            "generators.core.list_game_packs",
            return_value=["fake_pack"],
        ), patch(
            "generators.core.get_game_pack",
            return_value=fake_pack,
        ):
            result = await list_phases()

        # The fake_pack contributed one entry; the texture phase
        # has 0 generators (defensive skip), shop_channel has 2.
        assert len(result.packs) == 1
        phases_by_id = {
            p.phase: p for p in result.packs[0].phases
        }
        assert "shop_channel" in phases_by_id
        assert phases_by_id["shop_channel"].generator_count == 2
        assert phases_by_id["shop_channel"].execution_order == ["g1", "g2"]
        assert "texture" in phases_by_id
        assert phases_by_id["texture"].generator_count == 0
        assert phases_by_id["texture"].execution_order == []

        # Both phases still appear in the flat list (defensive skip
        # zeroes generator_count, not the phase id itself).
        assert result.phases == ["shop_channel", "texture"]

    async def test_dedup_across_two_packs_sharing_a_phase(self):
        """Dedup invariant: two packs registering the same phase
        id produce a flat ``phases`` field with that id appearing
        exactly once. Important for downstream clients that treat
        the flat list as a deduplicated canonical index of known
        phases (used to populate UI dropdowns and validate
        ``POST /v1/mods/generate`` ``phase`` parameters)."""
        from unittest.mock import MagicMock, patch

        from app.api.routes import list_phases

        # Two packs, each with a shared phase (``shop_channel``)
        # and a pack-specific phase. The flat list must collapse
        # the shared phase to a single entry.
        def _make_pack(game_id: str, phases: list[str]):
            fake_manifest = MagicMock()
            fake_manifest.game_id = game_id
            fake_manifest.display_name = game_id.replace("_", " ").title()
            fake_manifest.mod_format = "Content Patcher 1.29"

            pack = MagicMock()
            pack.get_manifest.return_value = fake_manifest
            pack.list_phases.return_value = phases

            fake_pg = MagicMock()
            fake_pg.execution_order = ["g1"]
            pack.get_generators.return_value = fake_pg
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
            result = await list_phases()

        # ``shop_channel`` appears in both packs but only once in
        # the flat list (and that single entry is the union,
        # deduplicated and sorted).
        assert result.phases == ["shop_channel", "texture", "weather_event"]
        # And both packs still appear in the per-pack breakdown —
        # dedup is applied to the flat list only.
        assert len(result.packs) == 2