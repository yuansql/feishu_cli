"""Seam: vendored Feishu lark-* skills are present and readable in-repo."""

from __future__ import annotations

import unittest
from pathlib import Path

from partner.paths import REPO_ROOT, SKILLS_DIR

EXPECTED = (
    "lark-approval",
    "lark-apps",
    "lark-attendance",
    "lark-base",
    "lark-calendar",
    "lark-contact",
    "lark-doc",
    "lark-drive",
    "lark-event",
    "lark-im",
    "lark-mail",
    "lark-markdown",
    "lark-minutes",
    "lark-okr",
    "lark-openapi-explorer",
    "lark-shared",
    "lark-sheets",
    "lark-skill-maker",
    "lark-slides",
    "lark-task",
    "lark-vc",
    "lark-vc-agent",
    "lark-whiteboard",
    "lark-wiki",
    "lark-workflow-meeting-summary",
    "lark-workflow-standup-report",
)


class LarkSkillsVendoredTests(unittest.TestCase):
    def test_skills_dir_under_repo(self) -> None:
        self.assertEqual(SKILLS_DIR, REPO_ROOT / "skills")
        self.assertTrue(SKILLS_DIR.is_dir(), SKILLS_DIR)

    def test_each_skill_has_skill_md(self) -> None:
        missing = [n for n in EXPECTED if not (SKILLS_DIR / n / "SKILL.md").is_file()]
        self.assertEqual(missing, [], f"missing SKILL.md: {missing}")

    def test_cursor_project_links_resolve(self) -> None:
        cursor = REPO_ROOT / ".cursor" / "skills"
        self.assertTrue(cursor.is_dir(), cursor)
        broken = []
        for name in EXPECTED:
            link = cursor / name
            target = (SKILLS_DIR / name).resolve()
            if not link.exists():
                broken.append(f"{name}: missing link")
                continue
            resolved = link.resolve()
            if resolved != target:
                broken.append(f"{name}: {resolved} != {target}")
            if not (resolved / "SKILL.md").is_file():
                broken.append(f"{name}: no SKILL.md via link")
        self.assertEqual(broken, [], "\n".join(broken))

    def test_shared_and_im_frontmatter(self) -> None:
        for name in ("lark-shared", "lark-im"):
            text = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---"), name)
            self.assertIn(f"name: {name}", text)


if __name__ == "__main__":
    unittest.main()
