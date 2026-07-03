"""Tests for storage.status_validation."""
import pytest

from storage.status_validation import (
    VALID_MOD_STATUSES,
    is_valid_mod_status,
)

# Note: ``app.api.schemas`` is NOT imported at module top-level — per
# AGENTS.md's test conventions, anything that transitively imports
# ``app.config`` can pull real LLM/discord secrets into the test process
# via the .env file. The cross-module consistency test below scopes its
# import inside the test method body.


class TestValidModStatusesConstant:
    """Tests for the canonical VALID_MOD_STATUSES frozenset."""

    def test_contains_all_five_canonical_statuses(self):
        """The set must hold the runtime state machine's five statuses."""
        assert {"pending", "running", "done", "failed", "cancelled"} == set(
            VALID_MOD_STATUSES
        )

    def test_is_frozenset_not_set(self):
        """Frozen prevents accidental in-place mutation by a future caller."""
        assert isinstance(VALID_MOD_STATUSES, frozenset)

    def test_has_exactly_five_members(self):
        """Guard against silent expansion when adding a new status.

        Adding a sixth status is fine but must be deliberate — this
        test forces the author to update both the constant AND the
        schemas.py Literal together.
        """
        assert len(VALID_MOD_STATUSES) == 5

    def test_members_are_all_lowercase(self):
        """Convention: canonical set is lowercase to match the Pydantic Literal."""
        for status in VALID_MOD_STATUSES:
            assert status == status.lower()


class TestIsValidModStatus:
    """Tests for the pure is_valid_mod_status() helper."""

    @pytest.mark.parametrize(
        "status",
        ["pending", "running", "done", "failed", "cancelled"],
    )
    def test_returns_true_for_canonical_statuses(self, status):
        """Every canonical status must be recognized."""
        assert is_valid_mod_status(status) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "Pending",          # wrong case
            "PENDING",          # all-caps
            "complete",         # synonym — not in the set
            "success",
            "queued",
            "  running",        # leading whitespace
            "running ",         # trailing whitespace
            "paused",           # hypothetical future state, not present today
            "pending,running",  # injection-style
            "running\x00",      # null byte appended
        ],
    )
    def test_returns_false_for_non_canonical_values(self, value):
        """Anything outside the set is rejected, regardless of case/whitespace."""
        assert is_valid_mod_status(value) is False

    @pytest.mark.parametrize(
        "non_string",
        [None, 0, 1, b"running", ["running"], ("running",), {"running"}],
    )
    def test_returns_false_for_non_strings(self, non_string):
        """Defensive: non-string inputs return False rather than raising.

        Lets untrusted callers (cron jobs, admin scripts) invoke this
        without wrapping every call in try/except.
        """
        assert is_valid_mod_status(non_string) is False  # type: ignore[arg-type]

    def test_return_type_is_bool(self):
        """Return must be exactly True/False, not truthy/falsy strings."""
        result = is_valid_mod_status("running")
        assert isinstance(result, bool)
        assert result is True


class TestConsistencyWithSchemas:
    """Cross-module sanity check: the canonical set must match the Pydantic Literal.

    The Literal on ``app.api.schemas.ModStatusResponse.status`` is the
    primary HTTP validation gate; this module is the secondary helper
    for non-HTTP callers. The two declarations must agree — this test
    catches drift by reading the schema's Literal and comparing to
    :data:`VALID_MOD_STATUSES`. The import is scoped inside the test
    method body per AGENTS.md test conventions.
    """

    def test_schemas_literal_lists_match_canonical_set(self):
        """The Pydantic Literal in app/api/schemas.py must agree with the set.

        If this fails, either ``VALID_MOD_STATUSES`` or the
        ``status: Literal[...]`` field on ``ModStatusResponse`` was
        edited without updating the other — the shared set is meant
        to prevent that drift.
        """
        from app.api.schemas import ModStatusResponse  # scoped: see class docstring
        import typing

        try:
            field = ModStatusResponse.model_fields["status"]
            literal_values = list(typing.get_args(field.annotation))
        except (AttributeError, KeyError, TypeError):
            pytest.skip("Pydantic field introspection shape changed.")

        if not literal_values:
            pytest.skip("Could not extract Literal values from schema field.")

        literal_strings = sorted(v for v in literal_values if isinstance(v, str))
        assert literal_strings == sorted(VALID_MOD_STATUSES), (
            "VALID_MOD_STATUSES drifted from the Pydantic Literal on "
            "app.api.schemas.ModStatusResponse.status. Update both in "
            f"the same commit. schema={literal_strings} "
            f"module={sorted(VALID_MOD_STATUSES)}"
        )
