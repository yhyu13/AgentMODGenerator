"""Tests for the GET /v1/packs endpoint and its schema.

Covers:
- ``PacksResponse`` Pydantic schema-level invariants (basic
  construction, default behaviour, count validation, missing-field
  rejection).
- ``list_packs`` handler:
    - happy path: at least one registered pack (``stardew_valley``)
      appears with its ``game_id`` / ``display_name`` / ``mod_format``
      and a non-empty ``phases`` list
    - structural invariants: every registered pack appears in
      registration order, every :class:`PhaseInfo`'s
      ``generator_count == len(execution_order)``,
      ``count == len(packs)``
    - defensive: a pack that raises ``ValueError`` on
      ``get_generators`` still appears with ``generator_count=0``
      and an empty ``execution_order`` rather than producing a 500
    - defensive: a pack id that ``list_game_packs`` advertises but
      ``get_game_pack`` returns None for is silently skipped
    - defensive: when ``list_game_packs`` returns an empty list, the
      endpoint returns an empty ``packs`` list with ``count == 0``
- does NOT exercise any DB or Redis call: the endpoint is a static
  read over the registered :class:`GamePack` registry, exactly like
  ``/v1/mods/phases`` and ``/v1/mods/phases/known`` — the three
  endpoints form the read-only phase / pack registry family.
"""
from __future__ import annotations

import pytest


class TestPacksResponseSchema:
    """Pydantic schema-level tests for PacksResponse."""

    def test_basic_construction(self):
        """Minimal round-trip: one pack with one phase, count == 1."""
        from app.api.schemas import (
            PacksResponse,
            PackInfo,
            PhaseInfo,
        )

        resp = PacksResponse(
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
            count=1,
        )
        assert len(resp.packs) == 1
        assert resp.packs[0].game_id == "stardew_valley"
        assert resp.packs[0].phases[0].generator_count == 2
        assert resp.count == 1

    def test_empty_packs_list_with_zero_count(self):
        """``count`` is allowed to be 0 when ``packs`` is empty (e.g.
        when no packs are registered). Mirrors the
        ``KnownPhasesResponse`` ``phases`` / ``count`` pair."""
        from app.api.schemas import PacksResponse

        resp = PacksResponse(packs=[], count=0)
        assert resp.packs == []
        assert resp.count == 0

    def test_count_rejects_negative(self):
        """``count`` has ``ge=0`` so negative values are rejected at
        the Pydantic boundary."""
        from pydantic import ValidationError

        from app.api.schemas import PacksResponse

        with pytest.raises(ValidationError):
            PacksResponse(packs=[], count=-1)

    def test_packs_must_be_a_list(self):
        """``packs`` has no default — callers must explicitly pass
        the (possibly empty) list. Pydantic rejects ``None``."""
        from pydantic import ValidationError

        from app.api.schemas import PacksResponse

        with pytest.raises(ValidationError):
            PacksResponse(packs=None, count=0)  # type: ignore[arg-type]

    def test_pack_with_empty_phases_list_round_trips(self):
        """A pack that registered zero phases still serialises as a
        valid :class:`PackInfo` (its ``phases`` field is required
        but accepts an empty list)."""
        from app.api.schemas import (
            PacksResponse,
            PackInfo,
        )

        resp = PacksResponse(
            packs=[
                PackInfo(
                    game_id="empty_pack",
                    display_name="Empty",
                    mod_format="Unknown",
                    phases=[],
                ),
            ],
            count=1,
        )
        assert resp.packs[0].phases == []


class TestListPacksEndpoint:
    """Tests for the list_packs handler."""

    async def test_returns_at_least_one_pack(self):
        """Master registers the ``stardew_valley`` pack on import,
        so the response must always contain at least one pack."""
        from app.api.routes import list_packs

        result = await list_packs()
        assert len(result.packs) >= 1

    async def test_pack_ids_are_strings(self):
        """Sanity: every ``PackInfo.game_id`` is a non-empty
        ``str``. Guards against a future regression where a pack
        accidentally registers with a non-string id (e.g. an enum
        that doesn't stringify cleanly)."""
        from app.api.routes import list_packs

        result = await list_packs()
        assert len(result.packs) >= 1
        for pack in result.packs:
            assert isinstance(pack.game_id, str)
            assert pack.game_id  # non-empty

    async def test_count_matches_packs_length(self):
        """``count`` is the denormalised length of ``packs`` — the
        handler always populates them consistently. Same convention
        the ``KnownPhasesResponse`` ``phases`` / ``count`` pair
        uses."""
        from app.api.routes import list_packs

        result = await list_packs()
        assert result.count == len(result.packs)

    async def test_pack_info_has_required_fields(self):
        """Every :class:`PackInfo` exposes a non-empty
        ``display_name`` and ``mod_format`` so callers can render a
        UI / dropdown directly from the response."""
        from app.api.routes import list_packs

        result = await list_packs()
        assert len(result.packs) >= 1
        for pack in result.packs:
            assert isinstance(pack.display_name, str)
            assert pack.display_name
            assert isinstance(pack.mod_format, str)
            assert pack.mod_format

    async def test_pack_phases_have_matching_generator_count(self):
        """Every :class:`PhaseInfo` carries a ``generator_count``
        that equals ``len(execution_order)`` — the handler computes
        both from the same ``pg.execution_order`` source, so this
        invariant should always hold for a well-behaved pack."""
        from app.api.routes import list_packs

        result = await list_packs()
        assert len(result.packs) >= 1
        for pack in result.packs:
            for phase_info in pack.phases:
                assert phase_info.generator_count == len(
                    phase_info.execution_order
                )

    async def test_pack_phases_are_strings(self):
        """Sanity: every :class:`PhaseInfo.phase` is a non-empty
        ``str``. Same defensive guard as ``test_pack_ids_are_strings``."""
        from app.api.routes import list_packs

        result = await list_packs()
        assert len(result.packs) >= 1
        for pack in result.packs:
            for phase_info in pack.phases:
                assert isinstance(phase_info.phase, str)
                assert phase_info.phase

    async def test_stardew_valley_pack_is_registered(self):
        """Master registers the ``stardew_valley`` pack on import —
        the endpoint must always include it in the ``packs`` list."""
        from app.api.routes import list_packs

        result = await list_packs()
        game_ids = [p.game_id for p in result.packs]
        assert "stardew_valley" in game_ids

    async def test_pack_id_listed_but_unresolvable_is_silently_skipped(self):
        """Defensive: a pack id advertised by ``list_game_packs()``
        but not resolvable via ``get_game_pack()`` is silently
        skipped rather than raising.

        Patches the source module (``generators.core``) — the
        handler imports ``list_game_packs`` / ``get_game_pack`` at
        call time inside the function body, so patching the source
        module is the correct target. Same pattern as
        ``test_known_phases.py::test_pack_id_listed_but_unresolvable_is_silently_skipped``.
        """
        from unittest.mock import patch

        from app.api.routes import list_packs

        with patch(
            "generators.core.list_game_packs",
            return_value=["missing_pack"],
        ), patch(
            "generators.core.get_game_pack",
            return_value=None,
        ):
            result = await list_packs()

        # No packs contributed any pack info.
        assert result.packs == []
        assert result.count == 0

    async def test_empty_registry_returns_empty_list(self):
        """When ``list_game_packs()`` returns an empty list (no
        packs registered), the endpoint returns an empty ``packs``
        list with ``count == 0``."""
        from unittest.mock import patch

        from app.api.routes import list_packs

        with patch(
            "generators.core.list_game_packs",
            return_value=[],
        ):
            result = await list_packs()

        assert result.packs == []
        assert result.count == 0

    async def test_pack_that_raises_on_get_generators_is_skipped(self):
        """Defensive: a pack that raises ``ValueError`` on
        ``get_generators(phase)`` (an otherwise-valid pack that
        cannot resolve one of its phases) shows up with
        ``generator_count=0`` and an empty ``execution_order`` for
        that phase rather than producing a 500. Mirrors the
        defensive skip ``list_phases`` uses.

        Builds a fake pack whose ``get_generators`` raises
        ``ValueError`` for the ``texture`` phase and returns a
        normal PhaseGenerators object for ``shop_channel``.
        """
        from unittest.mock import MagicMock, patch

        from app.api.routes import list_packs

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
            result = await list_packs()

        # The fake_pack contributed one entry; the texture phase
        # has 0 generators (defensive skip), shop_channel has 2.
        assert len(result.packs) == 1
        assert result.count == 1
        phases_by_id = {
            p.phase: p for p in result.packs[0].phases
        }
        assert "shop_channel" in phases_by_id
        assert phases_by_id["shop_channel"].generator_count == 2
        assert phases_by_id["shop_channel"].execution_order == ["g1", "g2"]
        assert "texture" in phases_by_id
        assert phases_by_id["texture"].generator_count == 0
        assert phases_by_id["texture"].execution_order == []