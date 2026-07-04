"""Tests for ``app.api.routes._compute_progress`` (v74).

Internal helper at ``app/api/routes.py:3104-3132``. Called from
``_build_timeline`` (routes.py:2660-2662) and indirectly from
``ModStatusResponse.current_stage``. Pure transformation — no I/O,
no async — so the test surface is feed a Redis-shaped dict, assert
the ``{stage, percent}`` envelope that comes out.

Three layers of branching: (1) the 9-entry ``stage_map`` table for
known statuses, (2) ``("unknown", 0)`` fallback for unrecognized
statuses (case-sensitive), (3) the ``generating`` refinement that
bumps percent from the bare 20 toward 55 using the
``generators_succeeded`` / ``generators_failed`` / ``generators``
counts. Follows the v67+v68+v72+v73 convention — direct helper
invocation, no TestClient.
"""
from __future__ import annotations

import pytest

from app.api.routes import _compute_progress


class TestKnownStatusMapping:
    """Branch 1 — the 9 status codes in the ``stage_map`` table.

    Each row pins one ``(status) -> (stage, percent)`` mapping. The
    helper's table is the canonical source of progress values for
    every non-``generating`` status, so any drift here propagates to
    both the timeline endpoint and the mod-status endpoint.
    """

    @pytest.mark.parametrize(
        ("status", "expected_stage", "expected_percent"),
        [
            ("pending", "pending", 0),
            ("routing", "routing", 5),
            ("t1_gating", "validating", 60),
            ("t2_gating", "reviewing", 75),
            ("packaging", "packaging", 90),
            ("done", "completed", 100),
            ("failed", "failed", 100),
            ("cancelled", "cancelled", 100),
        ],
    )
    def test_known_status_maps_to_table_entry(
        self, status: str, expected_stage: str, expected_percent: int,
    ) -> None:
        """Each known status returns its canonical ``(stage, percent)``.

        ``generating`` is excluded here because it has its own
        refinement branch (covered in
        :class:`TestGeneratingRefinement`) that overrides the bare
        20 from the table.
        """
        result = _compute_progress({"status": status})
        assert result == {"stage": expected_stage, "percent": expected_percent}


class TestUnknownStatusFallback:
    """Branch 2 — any unrecognized status falls through to ``("unknown", 0)``."""

    @pytest.mark.parametrize(
        "status",
        [
            "",
            "weird_unknown",
            "in_progress",
            "aborted",
            "DONE",  # case-sensitive — uppercase doesn't match the table
            "Pending",
        ],
    )
    def test_unknown_status_returns_unknown_zero(self, status: str) -> None:
        """An unrecognized status returns ``("unknown", 0)``.

        Pins the case-sensitivity (the table uses lowercase keys, so
        ``"DONE"`` and ``"Pending"`` are NOT recognized). Pins the
        empty-string and arbitrary-string behaviour. The helper must
        never raise on an unrecognized value — the ``status`` field
        in Redis is set by the orchestrator, and a future pipeline
        change that adds a new status must not crash the timeline /
        status endpoints.
        """
        result = _compute_progress({"status": status})
        assert result == {"stage": "unknown", "percent": 0}

    def test_missing_status_field_defaults_to_pending(self) -> None:
        """A redis_state dict with no ``status`` key defaults to ``"pending"``.

        Mirrors the ``redis_state.get("status", "pending")`` guard.
        This is the cold-start shape — Redis has the request but the
        orchestrator hasn't written a status yet.
        """
        result = _compute_progress({})
        assert result == {"stage": "pending", "percent": 0}


class TestGeneratingRefinement:
    """Branch 3 — ``status="generating"`` bumps percent from 20 toward 55.

    The bare table entry is ``(generating, 20)``. The refinement
    branch reads ``generators_succeeded`` + ``generators_failed`` +
    ``generators`` to compute a finer-grained percentage in the
    ``[20, 55]`` range (20 = just started, 55 = all generators
    done, NOT 100 — there's still 60/75/90/100 stages ahead).

    Denominator rules (priority order): non-empty ``generators``
    list wins → ``total = len(generators)``. Else →
    ``total = total_gens + 1`` (assumes one in flight, even when
    ``total_gens == 0`` — so the ``else: percent = 20`` branch at
    routes.py:3129 is effectively dead code).
    """

    def test_no_generators_field_no_completion_returns_bare_20(self) -> None:
        """No ``generators`` field, both completion lists empty →
        percent = ``20 + int(0/1 * 35) = 20``. Even though the
        ``else: percent = 20`` branch looks like it would fire, the
        denominator rule (``total_gens + 1``) sets ``total = 1``,
        so the ``if total > 0`` branch runs and returns 20
        arithmetically.
        """
        result = _compute_progress({"status": "generating"})
        assert result == {"stage": "generating", "percent": 20}

    def test_generators_field_with_all_completed_returns_55(self) -> None:
        """All generators done (``succeeded + failed == len(generators)``)
        bumps percent to exactly 55 — pins the
        ``20 + int(total_gens / total * 35)`` math. With
        ``total_gens == total`` the multiplier is 1, so
        ``20 + 35 = 55``.
        """
        result = _compute_progress({
            "status": "generating",
            "generators": ["shop_channel", "weather_event", "event_mod"],
            "generators_succeeded": ["shop_channel", "weather_event"],
            "generators_failed": ["event_mod"],
        })
        assert result == {"stage": "generating", "percent": 55}

    def test_generators_field_with_half_completed_returns_37(self) -> None:
        """Half the generators done → ``20 + int(0.5 * 35) = 37``
        (integer floor of 17.5). Pins the integer floor — a refactor
        to ``round`` would change the value for fractional cases.
        """
        result = _compute_progress({
            "status": "generating",
            "generators": ["g1", "g2", "g3", "g4"],
            "generators_succeeded": ["g1", "g2"],
            "generators_failed": [],
        })
        assert result == {"stage": "generating", "percent": 37}

    def test_no_generators_field_with_completion_uses_total_plus_one(self) -> None:
        """Missing ``generators`` but at least one finished →
        ``total = total_gens + 1`` (assumes one in flight). With
        ``total_gens=1, total=2``: ``20 + int(0.5 * 35) = 37``.
        """
        result = _compute_progress({
            "status": "generating",
            "generators_succeeded": ["shop_channel"],
            "generators_failed": [],
        })
        assert result == {"stage": "generating", "percent": 37}


class TestReturnShape:
    """Shape contract — always returns ``{"stage", "percent"}``."""

    def test_return_contains_stage_and_percent_keys(self) -> None:
        """Dict has exactly two keys: ``stage`` (str) and ``percent``
        (int). No extras, no missing.
        """
        result = _compute_progress({"status": "routing"})
        assert set(result.keys()) == {"stage", "percent"}
        assert isinstance(result["stage"], str)
        assert isinstance(result["percent"], int)

    def test_extra_keys_in_redis_state_are_ignored(self) -> None:
        """Helper reads only ``status`` + ``generators`` +
        ``generators_succeeded`` + ``generators_failed``. Other keys
        are ignored — pins that a future pipeline change adding
        fields doesn't break the helper.
        """
        result = _compute_progress({
            "status": "routing",
            "t2_score": 8,
            "t2_passed": True,
            "user_id": "u-123",
            "created_at": "2026-07-05T00:00:00+00:00",
        })
        assert result == {"stage": "routing", "percent": 5}