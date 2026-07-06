"""Tests for the POST /v1/feature_flags/{name}/rollback endpoint (handler-direct).

Companion to the v132 ``test_get_feature_flags`` (read-the-now),
v133 ``test_get_feature_flags_history`` (read-the-audit-log),
and v134 ``test_api_feature_flag_toggle`` (toggle) rounds: v132 +
v133 + v134 pinned the read-only pair + the first mutation of
Session 5; this round pins the SECOND mutation (``rollback``),
which closes the read-toggle-audit-undo loop on a single flag.
The remaining four handler-level orphans (``pin``, ``unpin``,
``pin_state``, ``pins``) all reuse the same handler-direct
pattern (``unittest.mock.patch`` on a single
``orchestrator.feature_flags`` function + a handler call + an
assertion on the response).

Mirrors the v134 toggle pattern: import the route handler, patch
the source module it reaches into (``orchestrator.feature_flags``)
— both ``rollback_flag`` (the helper that does the work) AND
``_DEFAULT_FLAGS`` / ``_overrides`` (the registry the handler
inspects to distinguish 404 from 409) — call the handler,
assert the response. No TestClient — the handler is short and
exercising it directly gives the test a single seam that
survives any future APIRouter / dep-injection reshuffle.

What is pinned here:

  1. Happy path — ``rollback_flag`` returns a valid 5-key dict
     ``{name, rolled_back_from, rolled_back_to,
     restored_entry_index, history_size_at_rollback}``. The
     handler builds a ``FeatureFlagRollbackResponse`` with
     exactly those five fields populated from the dict (NOT
     from the registry's current state — the snapshot in the
     dict is the source of truth, which is why a rollback to a
     value that is no longer current still succeeds). This
     pins the contract that the response reflects the audit
     log's view of "what was undone", not the live registry's
     view of "what is now".
  2. Unknown flag — ``rollback_flag`` returns ``None`` AND the
     flag is NOT in ``_DEFAULT_FLAGS`` or ``_overrides``. The
     handler raises ``HTTPException(status_code=404)`` with
     ``detail`` mentioning the bad flag name. This mirrors the
     toggle endpoint's deny-by-default contract.
  3. Known flag, no rollbackable history — ``rollback_flag``
     returns ``None`` AND the flag IS in ``_DEFAULT_FLAGS``
     (or ``_overrides``). The handler raises
     ``HTTPException(status_code=409)`` with ``detail``
     mentioning the flag name. The 409 is intentional: the
     request was well-formed and the flag exists, so 404
     would be a lie; 422 (validation) would also be wrong
     because the request has no body to validate. 409 is the
     standard "the resource state prevents the operation"
     code.
  4. Schema integration — the handler's return value satisfies
     the :class:`FeatureFlagRollbackResponse` Pydantic
     contract. Guards against a future handler refactor that
     drops a required field (``name``, ``rolled_back_from``,
     ``rolled_back_to``, ``restored_entry_index``, or
     ``history_size_at_rollback``).
  5. Field pass-through fidelity — every dict field round-trips
     verbatim into the response, including the ``int``
     sentinel ``restored_entry_index=-1`` and the unsigned
     ``history_size_at_rollback=0``. The field-by-field copy
     in the handler is the wire contract; this test catches a
     future refactor that introduces a coercion error (e.g.
     bool↔int confusion on the audit-index field).

Not pinned (intentional, deferred):

  - HTTP-level tests (200/404/409 status codes, JSON content
    type, FastAPI's automatic 422 on malformed path) — those
    belong in a TestClient round. The handler-direct round is
    sufficient for the v135 seam; a TestClient round (if
    desired) is a small follow-up.
  - Logger info events (``api.feature_flag.rolled_back``,
    ``api.feature_flag.rollback_unknown``,
    ``api.feature_flag.rollback_no_history``) — structlog's
    own test suite pins that, and re-asserting here would
    couple the test to a specific log handler.
  - The exact ``detail`` strings for 404 and 409 — pinned
    only loosely (the tests check for the flag name;
    substring match) because the exact wording is an
    implementation detail.
  - ``FlagPinnedError`` propagation. The handler does NOT
    catch ``FlagPinnedError`` from the ``set_flag`` call
    inside ``rollback_flag`` — the exception propagates to
    the framework's default 500-handling. This is intentional
    per the v40 design decision documented in
    ``docs/CRON_RUN_ARCHIVE_2026-07-04.md``: a rollback to a
    pinned flag is almost always an operator mistake (they
    forgot to ``unpin_flag`` before the rollback) and a 500
    with a traceback surfaces the mistake more loudly than a
    silent 4xx that looks like a normal failure. If the
    design ever flips to catch the exception and return a
    423, the test below's "no propagation" implicit
    assumption breaks — at that point, add a 423-test
    mirroring v134's ``test_pinned_flag_returns_423``.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.api.schemas import FeatureFlagRollbackResponse


def _rollback_dict(
    name: str,
    rolled_back_from: bool = False,
    rolled_back_to: bool = True,
    restored_entry_index: int = 0,
    history_size_at_rollback: int = 1,
) -> dict[str, object]:
    """Build a 5-key rollback result dict mirroring what
    ``rollback_flag`` returns on a successful rollback.

    Defaults match the most common case: a known flag with a
    single prior change (so the rollback restores the default,
    history size grows by 1 to include the rollback's own
    audit entry, and the restored entry index is the prior
    entry at index 0).
    """
    return {
        "name": name,
        "rolled_back_from": rolled_back_from,
        "rolled_back_to": rolled_back_to,
        "restored_entry_index": restored_entry_index,
        "history_size_at_rollback": history_size_at_rollback,
    }


class TestRollbackFeatureFlagHandler:
    """``rollback_feature_flag`` handler-direct contract tests."""

    async def test_happy_path_returns_rollback_response(self) -> None:
        """``rollback_flag`` returns a valid 5-key dict. The
        handler builds a ``FeatureFlagRollbackResponse`` with
        exactly those five fields populated. Pins the contract
        that the response reflects the audit log's view of
        "what was undone", not the live registry's view of
        "what is now"."""
        from app.api.routes import rollback_feature_flag

        with patch(
            "orchestrator.feature_flags.rollback_flag",
            return_value=_rollback_dict(
                name="flag_a",
                rolled_back_from=False,
                rolled_back_to=True,
                restored_entry_index=2,
                history_size_at_rollback=3,
            ),
        ) as mock_rollback:
            result = await rollback_feature_flag(name="flag_a")

        # The handler must pass the path ``name`` (not the
        # body's ``name`` — there is no body) through to
        # ``rollback_flag``.
        mock_rollback.assert_called_once_with("flag_a")
        assert isinstance(result, FeatureFlagRollbackResponse)
        assert result.name == "flag_a"
        assert result.rolled_back_from is False
        assert result.rolled_back_to is True
        assert result.restored_entry_index == 2
        assert result.history_size_at_rollback == 3

    async def test_unknown_flag_returns_404(self) -> None:
        """``rollback_flag`` returns ``None`` AND the flag is
        NOT in ``_DEFAULT_FLAGS`` or ``_overrides``. The
        handler raises ``HTTPException(status_code=404)`` with
        ``detail`` mentioning the bad flag name. Mirrors the
        toggle endpoint's deny-by-default contract."""
        from fastapi import HTTPException

        from app.api.routes import rollback_feature_flag

        # Patch BOTH the helper (to return None, simulating
        # "unknown flag") AND the registry (to be empty for
        # this name, so the handler's 404 check fires).
        with patch(
            "orchestrator.feature_flags.rollback_flag",
            return_value=None,
        ), patch(
            "orchestrator.feature_flags._DEFAULT_FLAGS",
            {},
        ), patch(
            "orchestrator.feature_flags._overrides",
            {},
        ):
            with pytest.raises(HTTPException) as exc_info:
                await rollback_feature_flag(name="not_a_real_flag")

        assert exc_info.value.status_code == 404
        assert "not_a_real_flag" in str(exc_info.value.detail)

    async def test_known_flag_no_history_returns_409(self) -> None:
        """``rollback_flag`` returns ``None`` AND the flag IS
        in ``_DEFAULT_FLAGS`` (so the registry knows about it
        but the audit log has no real changes to undo). The
        handler raises ``HTTPException(status_code=409)`` with
        ``detail`` mentioning the flag name. The 409 is
        intentional: the request was well-formed and the flag
        exists, so 404 would be a lie."""
        from fastapi import HTTPException

        from app.api.routes import rollback_feature_flag

        # Patch the helper to return None (no rollbackable
        # history) AND seed ``_DEFAULT_FLAGS`` with the flag
        # so the handler's "is known?" check sees it as
        # registered. The 409 path fires.
        with patch(
            "orchestrator.feature_flags.rollback_flag",
            return_value=None,
        ), patch(
            "orchestrator.feature_flags._DEFAULT_FLAGS",
            {"flag_a": True},
        ), patch(
            "orchestrator.feature_flags._overrides",
            {},
        ):
            with pytest.raises(HTTPException) as exc_info:
                await rollback_feature_flag(name="flag_a")

        assert exc_info.value.status_code == 409
        assert "flag_a" in str(exc_info.value.detail)

    async def test_known_flag_in_overrides_no_history_returns_409(
        self,
    ) -> None:
        """Same as ``test_known_flag_no_history_returns_409``
        but the flag is in ``_overrides`` (not
        ``_DEFAULT_FLAGS``) — for example, a flag that was
        toggled away from its default and then the default was
        removed from the registry. Mirrors the v40 design
        decision that ``name in _DEFAULT_FLAGS or name in
        _overrides`` is the "is known?" check, not just
        ``name in _DEFAULT_FLAGS``."""
        from fastapi import HTTPException

        from app.api.routes import rollback_feature_flag

        with patch(
            "orchestrator.feature_flags.rollback_flag",
            return_value=None,
        ), patch(
            "orchestrator.feature_flags._DEFAULT_FLAGS",
            {},
        ), patch(
            "orchestrator.feature_flags._overrides",
            {"flag_a": False},
        ):
            with pytest.raises(HTTPException) as exc_info:
                await rollback_feature_flag(name="flag_a")

        assert exc_info.value.status_code == 409
        assert "flag_a" in str(exc_info.value.detail)

    async def test_sentinel_restored_entry_index_minus_one(
        self,
    ) -> None:
        """``rollback_flag`` returns ``restored_entry_index=-1``
        (the helper's documented sentinel for "no real change
        was found to roll back to", per the schema docstring's
        ``ge=-1`` field). The handler must propagate the -1
        verbatim, NOT coerce it to 0 or drop it. This pins the
        field-by-field copy's fidelity on the sentinel
        boundary — the schema's ``ge=-1`` validator would
        accept it, but a future refactor that does
        ``abs(restored_entry_index)`` or ``max(0, ...)`` would
        silently swallow the sentinel's meaning."""
        from app.api.routes import rollback_feature_flag

        with patch(
            "orchestrator.feature_flags.rollback_flag",
            return_value=_rollback_dict(
                name="flag_a",
                restored_entry_index=-1,
                history_size_at_rollback=0,
            ),
        ):
            result = await rollback_feature_flag(name="flag_a")

        assert result.restored_entry_index == -1
        assert result.history_size_at_rollback == 0


class TestRollbackFeatureFlagSchemaIntegration:
    """Schema-vs-handler integration test for the response shape."""

    def test_response_model_validates_handler_output(self) -> None:
        """The handler's return value satisfies the
        :class:`FeatureFlagRollbackResponse` Pydantic contract
        — guards against a future handler refactor that drops
        a required field (``name``, ``rolled_back_from``,
        ``rolled_back_to``, ``restored_entry_index``, or
        ``history_size_at_rollback``)."""
        from app.api.routes import rollback_feature_flag

        # Sync call site via ``asyncio.run`` to keep the file
        # dependency-free for this one method (same pattern
        # as v132 / v133 / v134's ``Test*SchemaIntegration``).
        with patch(
            "orchestrator.feature_flags.rollback_flag",
            return_value=_rollback_dict(
                name="flag_x",
                rolled_back_from=True,
                rolled_back_to=False,
                restored_entry_index=4,
                history_size_at_rollback=5,
            ),
        ):
            result = asyncio.run(
                rollback_feature_flag(name="flag_x")
            )

        # Pydantic v2 round-trip — re-validate the already-
        # built model instance to confirm it conforms to the
        # schema.
        revalidated = FeatureFlagRollbackResponse.model_validate(
            result.model_dump()
        )
        assert revalidated == result
        assert revalidated.name == "flag_x"
        assert revalidated.rolled_back_from is True
        assert revalidated.rolled_back_to is False
        assert revalidated.restored_entry_index == 4
        assert revalidated.history_size_at_rollback == 5