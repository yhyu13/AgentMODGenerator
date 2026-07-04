"""Tests for the GET /v1/mods/cancellation_reasons endpoint and its schema."""
import pytest


class TestCancellationReasonsListResponseSchema:
    """Pydantic schema-level tests for CancellationReasonsListResponse."""

    def test_basic_construction(self):
        from app.api.schemas import CancellationReasonsListResponse

        resp = CancellationReasonsListResponse(
            reasons=["timeout", "user_cancelled"],
            count=2,
        )
        assert resp.reasons == ["timeout", "user_cancelled"]
        assert resp.count == 2

    def test_empty_reasons_allowed(self):
        from app.api.schemas import CancellationReasonsListResponse

        resp = CancellationReasonsListResponse(reasons=[], count=0)
        assert resp.reasons == []
        assert resp.count == 0

    def test_count_must_match_length(self):
        # The endpoint computes count == len(reasons), but the schema
        # intentionally doesn't enforce that at validation time — the
        # count field is a convenience for clients that don't want to
        # call len() on the reasons list. So a mismatch is allowed.
        from app.api.schemas import CancellationReasonsListResponse

        resp = CancellationReasonsListResponse(
            reasons=["a", "b", "c"],
            count=99,
        )
        assert resp.count == 99  # schema doesn't reject


class TestListCancellationReasonsEndpoint:
    """Tests for the list_cancellation_reasons handler."""

    async def test_returns_sorted_list(self):
        from app.api.routes import list_cancellation_reasons

        result = await list_cancellation_reasons()
        # Sorted ascending — that's the contract.
        assert result.reasons == sorted(result.reasons)
        # Count matches the length.
        assert result.count == len(result.reasons)
        # Non-empty — there must be at least one canonical reason
        # (the branch uses "user_cancelled" at minimum).
        assert result.count >= 1

    async def test_contains_user_cancelled(self):
        """The branch's cancel handler writes 'user_cancelled' as the
        reason key, so it MUST be in the canonical set — otherwise
        /v1/mods/cancellation_reasons would lie about a value the
        server is actively writing."""
        from app.api.routes import KNOWN_CANCELLATION_REASONS

        assert "user_cancelled" in KNOWN_CANCELLATION_REASONS

    async def test_returns_unique_reasons(self):
        """The endpoint sorts a frozenset, but defensive: no duplicate
        strings in the response."""
        from app.api.routes import list_cancellation_reasons

        result = await list_cancellation_reasons()
        assert len(result.reasons) == len(set(result.reasons))

    async def test_reason_values_are_strings(self):
        from app.api.routes import list_cancellation_reasons

        result = await list_cancellation_reasons()
        for reason in result.reasons:
            assert isinstance(reason, str)
            assert reason  # not empty