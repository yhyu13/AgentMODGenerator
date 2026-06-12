"""Tests for FeedbackRouter."""

import pytest

from orchestrator.feedback_router import FeedbackRouter


class TestFeedbackRouter:
    def test_empty_feedback_returns_empty(self):
        router = FeedbackRouter()
        result = router.route("", ["manifest_generator", "content_json_generator"])
        assert result == {}

    def test_no_matching_keywords_returns_empty(self):
        router = FeedbackRouter()
        feedback = "Everything looks great, no issues found."
        result = router.route(feedback, ["manifest_generator", "content_json_generator"])
        assert result == {}

    def test_technical_compliance_routes_to_manifest_and_content(self):
        router = FeedbackRouter()
        feedback = "The manifest.json is missing required fields and the content.json schema is invalid."
        generators = ["manifest_generator", "content_json_generator", "shop_item_pool_generator"]
        result = router.route(feedback, generators)
        assert "manifest_generator" in result
        assert "content_json_generator" in result
        assert "shop_item_pool_generator" not in result

    def test_game_balance_routes_to_trigger_and_shop(self):
        router = FeedbackRouter()
        feedback = "Prices are too cheap and the damage multiplier is overpowered."
        generators = ["trigger_logic_generator", "shop_item_pool_generator", "manifest_generator"]
        result = router.route(feedback, generators)
        assert "trigger_logic_generator" in result
        assert "shop_item_pool_generator" in result
        assert "manifest_generator" not in result

    def test_content_quality_routes_to_content_and_mail(self):
        router = FeedbackRouter()
        feedback = "The dialogue text is poorly written and the mail letters need better copy."
        generators = ["content_json_generator", "mail_system_generator", "manifest_generator"]
        result = router.route(feedback, generators)
        assert "content_json_generator" in result
        assert "mail_system_generator" in result
        assert "manifest_generator" not in result

    def test_excerpt_extraction_finds_keyword_line(self):
        router = FeedbackRouter()
        feedback = "Line one.\nThe manifest.json is missing required fields.\nLine three."
        result = router.route(feedback, ["manifest_generator"])
        assert "manifest.json" in result["manifest_generator"]

    def test_excerpt_fallback_to_truncated_feedback(self):
        router = FeedbackRouter()
        feedback = "a" * 600
        result = router.route(feedback, ["manifest_generator"])
        # No keywords match, so router returns empty dict
        assert result == {}

    def test_generator_not_in_active_list_is_skipped(self):
        router = FeedbackRouter()
        feedback = "The manifest.json is missing required fields."
        generators = ["content_json_generator"]  # manifest not active
        result = router.route(feedback, generators)
        assert "manifest_generator" not in result
        assert "content_json_generator" in result

    def test_multiple_feedback_types_combined(self):
        router = FeedbackRouter()
        feedback = "Prices are too high. The dialogue is bland. The manifest is broken."
        generators = [
            "trigger_logic_generator",
            "shop_item_pool_generator",
            "content_json_generator",
            "mail_system_generator",
            "manifest_generator",
        ]
        result = router.route(feedback, generators)
        assert "trigger_logic_generator" in result
        assert "shop_item_pool_generator" in result
        assert "content_json_generator" in result
        assert "mail_system_generator" in result
        assert "manifest_generator" in result

    def test_duplicate_generator_avoided(self):
        router = FeedbackRouter()
        feedback = "The content.json schema is invalid and the content.json format is wrong."
        generators = ["content_json_generator"]
        result = router.route(feedback, generators)
        assert list(result.keys()) == ["content_json_generator"]
        assert len(result) == 1
