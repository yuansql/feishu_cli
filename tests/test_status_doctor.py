from __future__ import annotations

import unittest
from unittest import mock

from partner.office.status_doctor import status_text


class StatusTextTests(unittest.TestCase):
    """Smoke tests for status_doctor imports and status_text."""

    def test_probe_ok_is_importable(self) -> None:
        """_probe_ok was previously called without being imported."""
        from partner.office.status_doctor import _probe_ok
        from partner.office.messaging import _probe_ok as msg_probe_ok

        self.assertIs(_probe_ok, msg_probe_ok)
        self.assertTrue(_probe_ok({"ok": True})[0])
        self.assertFalse(_probe_ok({"ok": False, "error": {"message": "nope"}})[0])

    def test_status_text_does_not_raise(self) -> None:
        with mock.patch("partner.office.status_doctor.run_lark", return_value={"ok": True}), \
             mock.patch("partner.office.status_doctor.identity_ready", return_value=True), \
             mock.patch("partner.office.status_doctor.display_name", return_value="测试"), \
             mock.patch("partner.office.status_doctor.config_path", return_value="/path/to/config"):
            text = status_text()
        self.assertIn("飞书工作伙伴状态", text)


if __name__ == "__main__":
    unittest.main()
