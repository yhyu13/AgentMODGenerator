"""Tests for app/discord/webhook.verify_signature (Ed25519 path).

The verify_signature function is a pure function (no FastAPI deps,
no env reads inside the call), so it is the natural seam to test the
Ed25519 verification logic. The full HTTP handler (handle_interaction)
is integration territory and would need a mock Request object — see
tests/test_discord_flow.py for the integration test pattern.

Background: the cron-loop's discord-ops-hardening branch ported the
real PyNaCl-based Ed25519 verification, but it imported the wrong
exception name (nacl.exceptions.BadSignature doesn't exist; the real
name is BadSignatureError). The patch aliases
``BadSignatureError as BadSignature`` so the existing ``except``
clause still works. These tests pin that contract:

* valid signature → True
* tampered signature → False (BadSignatureError caught)
* wrong-key signature → False (BadSignatureError caught)
* missing timestamp header → False (no verification attempted)
* missing signature header → False (no verification attempted)
* missing DISCORD_PUBLIC_KEY env → False (with skip log)

If the alias is removed, the import itself fails and the test
session reports an ImportError, which is the exact regression the
alias was added to prevent.
"""
from __future__ import annotations

import os
import secrets

import pytest
from nacl.signing import SigningKey

from app import discord as app_discord_pkg  # noqa: F401  (import side effects)
from app.discord import webhook


@pytest.fixture
def signing_keypair() -> SigningKey:
    """Generate a fresh Ed25519 keypair for one test."""
    return SigningKey.generate()


@pytest.fixture
def set_public_key(monkeypatch: pytest.MonkeyPatch, signing_keypair: SigningKey) -> str:
    """Install the matching public key on the webhook module and return its hex.

    The webhook module captures ``_DISCORD_PUBLIC_KEY`` at import time
    via ``os.getenv``. Tests must monkeypatch the module-level name
    directly (NOT the env var) so the change is observed by the
    closure-bound function.
    """
    pk_hex = signing_keypair.verify_key.encode().hex()
    monkeypatch.setattr(webhook, "_DISCORD_PUBLIC_KEY", pk_hex)
    return pk_hex


class TestVerifySignatureAcceptsValid:
    """The happy path: a correctly-signed request verifies."""

    def test_valid_signature_returns_true(
        self, set_public_key: str, signing_keypair: SigningKey
    ) -> None:
        body = b'{"type":1}'
        timestamp = "1234567890"
        signature = signing_keypair.sign(
            timestamp.encode() + body
        ).signature.hex()
        assert webhook.verify_signature(body, signature, timestamp) is True

    def test_valid_signature_with_empty_body(
        self, set_public_key: str, signing_keypair: SigningKey
    ) -> None:
        body = b""
        timestamp = "1234567890"
        signature = signing_keypair.sign(
            timestamp.encode() + body
        ).signature.hex()
        assert webhook.verify_signature(body, signature, timestamp) is True


class TestVerifySignatureRejectsInvalid:
    """Tampered or wrong-key requests must NOT verify."""

    def test_tampered_body_returns_false(
        self, set_public_key: str, signing_keypair: SigningKey
    ) -> None:
        timestamp = "1234567890"
        original_body = b'{"type":1}'
        # Sign the ORIGINAL body, then send a tampered body.
        signature = signing_keypair.sign(
            timestamp.encode() + original_body
        ).signature.hex()
        tampered_body = b'{"type":2}'
        assert webhook.verify_signature(tampered_body, signature, timestamp) is False

    def test_wrong_signing_key_returns_false(
        self, set_public_key: str
    ) -> None:
        # Set the public key to one keypair's pubkey, sign with a
        # different keypair. Discord's verify will raise
        # BadSignatureError, which the except clause must catch.
        body = b'{"type":1}'
        timestamp = "1234567890"
        other_key = SigningKey.generate()
        signature = other_key.sign(timestamp.encode() + body).signature.hex()
        assert webhook.verify_signature(body, signature, timestamp) is False

    def test_random_signature_returns_false(
        self, set_public_key: str
    ) -> None:
        body = b'{"type":1}'
        timestamp = "1234567890"
        # 128 hex chars = 64 bytes = Ed25519 signature size, but
        # random bytes (not from any signing key).
        random_sig = secrets.token_hex(64)
        assert webhook.verify_signature(body, random_sig, timestamp) is False

    def test_malformed_hex_signature_returns_false(
        self, set_public_key: str
    ) -> None:
        # Hex decoding fails → ValueError caught by the except clause.
        body = b'{"type":1}'
        timestamp = "1234567890"
        assert webhook.verify_signature(body, "not-hex-at-all", timestamp) is False


class TestVerifySignatureMissingInputs:
    """The function must short-circuit on missing inputs without crashing."""

    def test_missing_timestamp_returns_false(
        self, set_public_key: str, signing_keypair: SigningKey
    ) -> None:
        body = b'{"type":1}'
        signature = signing_keypair.sign(b"" + body).signature.hex()
        assert webhook.verify_signature(body, signature, "") is False

    def test_missing_signature_returns_false(
        self, set_public_key: str
    ) -> None:
        body = b'{"type":1}'
        assert webhook.verify_signature(body, "", "1234567890") is False

    def test_missing_public_key_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If DISCORD_PUBLIC_KEY is unset, the webhook is unconfigured
        and the function MUST return False (never True). This is the
        fail-closed path: better to reject all requests than to skip
        verification on a misconfigured deployment."""
        monkeypatch.setattr(webhook, "_DISCORD_PUBLIC_KEY", "")
        body = b'{"type":1}'
        timestamp = "1234567890"
        # Even a valid signature is rejected because there's no key to
        # verify against.
        sk = SigningKey.generate()
        signature = sk.sign(timestamp.encode() + body).signature.hex()
        assert webhook.verify_signature(body, signature, timestamp) is False


class TestBadSignatureAlias:
    """Pin the BadSignatureError-as-BadSignature alias contract.

    nacl.exceptions.BadSignature does not exist; the real class is
    BadSignatureError. The webhook module aliases the real name to
    the expected one so the ``except (BadSignature, ...)`` clause
    matches. If the alias is removed, the import fails and no
    webhook test can even collect. This test exercises the import
    path AND confirms the alias is what the except clause sees.
    """

    def test_bad_signature_alias_resolves(self) -> None:
        # The alias must be importable from nacl.exceptions through
        # the webhook module's namespace.
        from nacl.exceptions import BadSignatureError

        # And the webhook module's name must point at the same class
        # object (proves the alias is in place, not a separate copy).
        assert webhook.BadSignature is BadSignatureError
