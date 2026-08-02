"""Free-form Discord intake tests — the on_message extraction rules.

Pins the qwen/claude UX fix: ``on_message`` only greeted; now any
non-trivial chat message is treated as a mod request. The extraction rules
live in ``_extract_prompt_from_message`` so they are testable without a
live Discord gateway.
"""
from __future__ import annotations

from app.discord.bot import _extract_prompt_from_message


class TestIntakeRules:
    def test_plain_description_triggers(self):
        prompt = _extract_prompt_from_message("make a tv shopping channel with weekly deals")
        assert prompt == "make a tv shopping channel with weekly deals"

    def test_whitespace_padding_stripped(self):
        prompt = _extract_prompt_from_message("   make a farm expansion with new buildings   ")
        assert prompt == "make a farm expansion with new buildings"

    def test_greeting_does_not_trigger(self):
        for greeting in ("hi", "hello", "hey", "你好", "嗨", "Hi", "HELLO"):
            assert _extract_prompt_from_message(greeting) is None

    def test_slash_command_does_not_trigger(self):
        assert _extract_prompt_from_message("/generate make a mod") is None

    def test_bang_command_does_not_trigger(self):
        assert _extract_prompt_from_message("!generate make a mod") is None

    def test_empty_message_does_not_trigger(self):
        assert _extract_prompt_from_message("") is None
        assert _extract_prompt_from_message("   ") is None

    def test_short_message_does_not_trigger(self):
        assert _extract_prompt_from_message("make a mod") is None

    def test_whitespace_only_content_no_trigger(self):
        assert _extract_prompt_from_message("\t\n") is None
