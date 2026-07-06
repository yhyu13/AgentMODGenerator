"""Schema-level tests for the Session 7 purge schemas.

Companion to the v104 ``PurgeRequest`` + ``PurgeResponse`` Pydantic
models (port landed in v104 Red). Pins the wire-shape contract that
the ``POST /v1/mods/purge`` admin route will emit. Schema-only (no
TestClient) because the handler depends on
``storage.queries.delete_old_mod_requests`` and the three
``storage.redis.delete_*`` helpers, which are not all on master yet
— see ``docs/PENDING_SOURCE_BUNDLE.md``. Mirrors the v33 (schema) →
v34 (handler + handler tests) split used for Session 5 endpoint 3/4
(``/v1/feature_flags/history``).

The operator-facing semantics these tests pin:

- ``days`` is bounded ``1..365`` so an operator cannot nuke the
  table by sending ``0`` (would erase ``>= today`` everything older
  than zero days = everything) or ``100000`` (foot-gun). The
  upper bound is intentionally generous — a year is a plausible
  sweep — but stops short of "drop the whole table".
- ``deleted_count`` is bounded ``>= 0`` so a healthy zero-result
  purge cannot leak a negative count (which would imply the SQL
  helper regressed into a deletion-then-rollback bug).
- ``deleted_request_ids`` defaults to ``[]`` so a freshly-typed
  response (e.g. from a future SDK that fills only ``days``) round-
  trips cleanly without the caller having to specify the sample.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.api.schemas import PurgeRequest, PurgeResponse


class TestPurgeRequest:
    """``PurgeRequest`` is the request body for ``POST /v1/mods/purge``."""

    def test_minimal_round_trip(self) -> None:
        # Boundary-valid: ``days=1`` is the smallest legal value.
        r = PurgeRequest(days=1)
        assert r.days == 1

    def test_upper_boundary_round_trip(self) -> None:
        # Boundary-valid: ``days=365`` is the largest legal value
        # — a year is the documented upper bound for a single sweep.
        r = PurgeRequest(days=365)
        assert r.days == 365

    def test_days_must_be_ge_1(self) -> None:
        # ``days=0`` would purge everything (the SQL helper's
        # ``days < 1`` short-circuit is defence-in-depth, the
        # primary guard is the Pydantic ``ge=1`` validator that
        # turns this into a 422 BEFORE the handler runs).
        with pytest.raises(ValidationError):
            PurgeRequest(days=0)

    def test_days_must_be_le_365(self) -> None:
        # ``days=366`` is one over the documented upper bound.
        # The cap is intentionally generous (a year of history is
        # a plausible operator sweep) but stops short of
        # "drop the whole table" foot-gun.
        with pytest.raises(ValidationError):
            PurgeRequest(days=366)

    def test_negative_days_rejected(self) -> None:
        # Negative ``days`` is meaningless (the SQL helper would
        # treat it as "everything newer than now", which is
        # nothing — but the contract should reject it loudly).
        with pytest.raises(ValidationError):
            PurgeRequest(days=-5)

    def test_missing_days_raises(self) -> None:
        # ``days`` is the only field and has no default —
        # omitting it must yield a 422 so the operator knows
        # they forgot the window.
        with pytest.raises(ValidationError):
            PurgeRequest()  # type: ignore[call-arg]


class TestPurgeResponse:
    """``PurgeResponse`` is the ``POST /v1/mods/purge`` envelope."""

    def test_zero_result_round_trip(self) -> None:
        # Healthy no-op: nothing matched the window, so the
        # response reports ``deleted_count=0`` and an empty
        # sample list. ``deleted_count=0`` MUST round-trip
        # cleanly (a healthy empty purge is a valid response,
        # not an error).
        r = PurgeResponse(days=7, deleted_count=0, deleted_request_ids=[])
        assert r.days == 7
        assert r.deleted_count == 0
        assert r.deleted_request_ids == []

    def test_with_sample_round_trip(self) -> None:
        # Typical non-empty result: 42 rows deleted, sample of
        # 3 ids surfaced for audit. The full list is intentionally
        # NOT in the response — see ``PurgeResponse.deleted_
        # request_ids`` docstring on master.
        sample = ["req_aaa", "req_bbb", "req_ccc"]
        r = PurgeResponse(days=30, deleted_count=42, deleted_request_ids=sample)
        assert r.days == 30
        assert r.deleted_count == 42
        assert r.deleted_request_ids == sample

    def test_default_sample_is_empty_list(self) -> None:
        # ``deleted_request_ids`` has ``default_factory=list`` so
        # the field defaults to ``[]`` when omitted. This is
        # useful for SDKs that fill only ``days`` + ``deleted_
        # count`` and want the empty sample to serialize as
        # ``"deleted_request_ids": []`` rather than raising.
        r = PurgeResponse(days=1, deleted_count=0)
        assert r.deleted_request_ids == []

    def test_deleted_count_must_be_ge_0(self) -> None:
        # ``deleted_count=-1`` would imply the SQL helper
        # regressed into a delete-then-rollback bug (the row
        # was deleted but the count went negative). Pin the
        # boundary so the regression would surface as a
        # ValidationError instead of silently going negative.
        with pytest.raises(ValidationError):
            PurgeResponse(days=7, deleted_count=-1, deleted_request_ids=[])

    def test_days_must_be_ge_1(self) -> None:
        # Response ``days`` mirrors the request's ``ge=1``
        # constraint — even on a response, the echoed-back
        # window must be a legal value (otherwise the caller
        # would learn the server processed an illegal input).
        with pytest.raises(ValidationError):
            PurgeResponse(days=0, deleted_count=0, deleted_request_ids=[])