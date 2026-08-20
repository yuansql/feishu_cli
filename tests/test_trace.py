from __future__ import annotations

import os
import tempfile
import unittest

from partner.trace import emit_trace, read_traces


class TraceTests(unittest.TestCase):
    def test_trace_redacts_sensitive_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["FEISHU_PARTNER_TRACES_DIR"] = tmp
            emit_trace(
                "t1",
                "tool.called",
                args={"query": "A6", "authorization": "Bearer secret"},
            )
            rows = read_traces("t1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["args"]["query"], "A6")
        self.assertEqual(rows[0]["args"]["authorization"], "[REDACTED]")


if __name__ == "__main__":
    unittest.main()
