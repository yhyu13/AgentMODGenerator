"""Tests for the GET /v1/mods/phases/known endpoint and its schema.

Covers:
- ``KnownPhasesResponse`` Pydantic schema-level invariants (basic
  construction, default behavior, count validation).
- ``list_known_phases`` handler:
    - happy path: at least one registered pack (``stardew_valley``)
      contributes phases to the flat list
    - structural invariants: sorted ascending, deduplicated,
      ``count == len(phases)``
    - cross-validation: the flat list matches the ``phases`` field
      returned by ``GET /v1/mods/phases`` (round-trip contract —
      the new endpoint is a thin alias)
    - defensive: a pack id that ``list_game_packs`` advertises but
      ``get_game_pack`` returns None for is silently skipped
    - defensive: when ``list_game_packs`` returns an empty list,
      the endpoint returns an empty (sorted) flat list with
      ``count == 0``
- does NOT exercise any DB or Redis call: the endpoint is a static
  read over the registered :class:`GamePack` registry.
"""
import pytest


class TestKnownPhasesResponseSchema:
    """Pydantic schema-level tests for KnownPhasesResponse."""

    def test_basic_construction(self):
        from app.api.schemas import KnownPhasesResponse

        resp = KnownPhasesResponse(phases=["shop_channel", "texture"], count=2)
        assert resp.phases == ["shop_channel", "texture"]
        assert resp.count == 2

    def test_phases_must_be_a_list(self):
        """``phases`` has no default — callers must explicitly pass
        the (possibly empty) list. Pydantic rejects ``None``."""
        from pydantic import ValidationError

        from app.api.schemas import KnownPhasesResponse

        with pytest.raises(ValidationError):
            KnownPhasesResponse(phases=None, count=0)  # type: ignore[arg-type]

    def test_count_rejects_negative(self):
        """``count`` has ``ge=0`` so negative values are rejected at
        the Pydantic boundary."""
        from pydantic import ValidationError

        from app.api.schemas import KnownPhasesResponse

        with pytest.raises(ValidationError):
            KnownPhasesResponse(phases=["x"], count=-1)

    def test_count_can_be_zero_for_empty_list(self):
        """The ``count`` field is allowed to be 0 when ``phases``
        is empty (e.g. when no packs are registered)."""
        from app.api.schemas import KnownPhasesResponse

        resp = KnownPhasesResponse(phases=[], count=0)
        assert resp.phases == []
        assert resp.count == 0


class TestListKnownPhasesEndpoint:
    """Tests for the list_known_phases handler."""

    async def test_returns_at_least_one_phase(self):
        """Master registers the ``stardew_valley`` pack on import,
        so the flat list must always contain at least one phase."""
        from app.api.routes import list_known_phases

        result = await list_known_phases()
        assert len(result.phases) >= 1

    async def test_phases_is_sorted_ascending(self):
        """The flat list is the sorted union of every phase across
        all registered packs — same convention the
        ``list_phases`` handler uses."""
        from app.api.routes import list_known_phases

        result = await list_known_phases()
        assert result.phases == sorted(result.phases)

    async def test_phases_is_deduplicated(self):
        """If two packs registered the same phase id, the flat list
        would still contain it exactly once (set union)."""
        from app.api.routes import list_known_phases

        result = await list_known_phases()
        assert len(result.phases) == len(set(result.phases))

    async def test_count_matches_phases_length(self):
        """``count`` is the denormalized length of ``phases`` — the
        handler always populates them consistently. Same convention
        the ``CancellationReasonsListResponse`` ``reasons`` /
        ``count`` pair uses."""
        from app.api.routes import list_known_phases

        result = await list_known_phases()
        assert result.count == len(result.phases)

    async def test_matches_phases_endpoint_flat_field(self):
        """The ``/v1/mods/phases/known`` endpoint is documented as
        a thin alias for the ``phases`` field of
        ``/v1/mods/phases``. Verify the round-trip contract: the
        flat list returned by both endpoints is identical."""
        from app.api.routes import list_known_phases, list_phases

        known = await list_known_phases()
        full = await list_phases()
        assert known.phases == full.phases

    async def test_pack_id_listed_but_unresolvable_is_silently_skipped(self):
        """Defensive: a pack id advertised by ``list_game_packs()``
        but not resolvable via ``get_game_pack()`` is silently
        skipped rather than raising.

        Patches the source module (``generators.core``) — the
        handler imports ``list_game_packs`` / ``get_game_pack`` at
        call time inside the function body, so patching the source
        module is the correct target.
        """
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

        # No packs contributed any phases.
        assert result.phases == []
        assert result.count == 0

    async def test_empty_registry_returns_empty_list(self):
        """When ``list_game_packs()`` returns an empty list (no
        packs registered), the endpoint returns an empty sorted
        flat list with ``count == 0``."""
        from unittest.mock import patch

        from app.api.routes import list_known_phases

        with patch(
            "generators.core.list_game_packs",
            return_value=[],
        ):
            result = await list_known_phases()

        assert result.phases == []
        assert result.count == 0

    async def test_phases_are_strings(self):
        """Sanity: every entry in the flat list is a non-empty
        ``str``. Guards against a future regression where a pack
        accidentally registers a non-string phase id (e.g. a phase
        enum that doesn't stringify cleanly)."""
        from app.api.routes import list_known_phases

        result = await list_known_phases()
        for phase in result.phases:
            assert isinstance(phase, str)
            assert phase  # non-empty