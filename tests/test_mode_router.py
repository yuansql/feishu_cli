from __future__ import annotations

import unittest

from partner.routing.intents import Intent, parse_intent
from partner.routing.mode_router import looks_like_knowledge_qa, route_request


class ModeRouterTests(unittest.TestCase):
    def test_route_workflow(self) -> None:
        decision = route_request("晨间核对", parse_intent("晨间核对"))
        self.assertEqual(decision.mode, "workflow")
        self.assertEqual(decision.workflow_id, "morning_checkin")

    def test_route_task(self) -> None:
        decision = route_request("任务模式 汇总风险", parse_intent("任务模式 汇总风险"))
        self.assertEqual(decision.mode, "task")

    def test_route_knowledge(self) -> None:
        self.assertTrue(looks_like_knowledge_qa("知识问答 报销制度"))
        decision = route_request(
            "知识问答 报销制度",
            Intent(action="unknown", query=""),
        )
        self.assertEqual(decision.mode, "knowledge")
        self.assertIn("报销", decision.query)

    def test_route_model_default(self) -> None:
        decision = route_request("今天", parse_intent("今天"))
        self.assertEqual(decision.mode, "model")


if __name__ == "__main__":
    unittest.main()
