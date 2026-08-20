"""High-risk regression fixtures (Aily eval baseline)."""

from __future__ import annotations

import unittest

from partner.intents import parse_intent
from partner.resolved import looks_like_resolve, resolve_text


class EvalIntentFixtures(unittest.TestCase):
    def test_done_phrases_not_unknown(self) -> None:
        for text in ("已经完成了", "这个我已经解决了", "搞定了"):
            intent = parse_intent(text)
            self.assertNotEqual(intent.action, "unknown", text)

    def test_today_tasks_not_search(self) -> None:
        self.assertEqual(parse_intent("今天的任务？").action, "today")

    def test_today_recap_not_unknown(self) -> None:
        self.assertEqual(parse_intent("我今天干了什么?").action, "today_recap")

    def test_person_not_search(self) -> None:
        self.assertEqual(parse_intent("张三的回复如何？").action, "person")

    def test_task_confirm_not_resolve(self) -> None:
        self.assertEqual(parse_intent("确认写入").action, "task_confirm")
        self.assertFalse(looks_like_resolve("确认写入"))


class EvalResolveFixtures(unittest.TestCase):
    def test_section_header_done(self) -> None:
        body = "待处理 / 待回复（2项） 已经完成"
        self.assertTrue(looks_like_resolve(body))


class EvalBadReplyFixtures(unittest.TestCase):
    def test_litellm_provider_error_is_bad_reply(self) -> None:
        from partner.serve import looks_like_bad_reply

        err = (
            "LLM provider internal error (no retry): litellm.InternalServerError: "
            "InternalServerError: OpenAIException - Database error, please contact "
            "the administrator (request id: 20260819060155129324441cZ6hHUkCyTvECsVS)"
        )
        self.assertTrue(looks_like_bad_reply(err))
        self.assertFalse(looks_like_resolve(err))


if __name__ == "__main__":
    unittest.main()
