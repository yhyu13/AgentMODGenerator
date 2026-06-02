"""FeedbackRouter: routes T2 judge feedback to specific generators for self-correction."""
import structlog

logger = structlog.get_logger()

_FEEDBACK_TYPE_KEYWORDS: dict[str, list[str]] = {
    "game_balance": [
        "balance", "pricing", "price", "cost", "expensive", "cheap",
        "overpowered", "underpowered", "difficulty", "damage", "multiplier",
        "stock", "inventory",
    ],
    "content_quality": [
        "quality", "content", "text", "description", "mail", "letter",
        "broadcast", "channel", "message", "dialogue", "copy", "writing",
    ],
    "technical_compliance": [
        "technical", "schema", "manifest", "format", "json", "validation",
        "content.json", "manifest.json", "compliance", "spec", "specification",
    ],
}


class FeedbackRouter:
    _generators_for_feedback: dict[str, list[str]] = {
        "game_balance": ["trigger_logic_generator", "shop_item_pool_generator"],
        "content_quality": ["content_json_generator", "mail_system_generator"],
        "technical_compliance": ["manifest_generator", "content_json_generator"],
    }

    def route(self, t2_feedback: str, generators: list[str]) -> dict[str, str]:
        """Returns {generator_name: specific_feedback} for only the generators that should act.

        Args:
            t2_feedback: Aggregated feedback string from T2 judge.
            generators: List of active generator names in execution order.

        Returns:
            Dict mapping generator names to their specific feedback excerpts.
        """
        if not t2_feedback:
            return {}

        feedback_lower = t2_feedback.lower()
        result: dict[str, str] = {}

        for feedback_type, target_gens in self._generators_for_feedback.items():
            keywords = _FEEDBACK_TYPE_KEYWORDS.get(feedback_type, [])
            if not any(kw in feedback_lower for kw in keywords):
                continue

            for gen in target_gens:
                if gen in generators and gen not in result:
                    excerpt = self._extract_excerpt(t2_feedback, keywords)
                    result[gen] = excerpt
                    logger.debug(
                        "feedback_router.routed",
                        generator=gen,
                        feedback_type=feedback_type,
                        excerpt=excerpt[:100],
                    )

        return result

    def _extract_excerpt(self, feedback: str, keywords: list[str]) -> str:
        """Extract relevant excerpt from feedback containing keywords."""
        feedback_lines = feedback.split("\n")
        for line in feedback_lines:
            line_lower = line.lower()
            if any(kw in line_lower for kw in keywords):
                return line.strip()
        return feedback[:500] if len(feedback) > 500 else feedback