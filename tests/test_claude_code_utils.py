# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import base64
import json
import os
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

from utils import claude_code_utils as cc


def _proc(stdout='{"is_error": false, "subtype": "success", "result": "hi"}', returncode=0, stderr=""):
    return SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)


def _patches(run):
    return (
        mock.patch.object(cc.shutil, "which", return_value="/usr/bin/claude"),
        mock.patch.object(cc.subprocess, "run", run),
    )


class CallClaudeCodeTest(unittest.TestCase):
    def _call(self, run, *args, **kwargs):
        p1, p2 = _patches(run)
        with p1, p2:
            return cc.call_claude_code(*args, **kwargs)

    def test_success_argv_and_stdin(self):
        run = mock.Mock(return_value=_proc())
        out = self._call(
            run, [{"type": "text", "text": "hello"}], "claude-haiku-5-5", system="sys"
        )
        self.assertEqual(out, "hi")
        argv = run.call_args.args[0]
        self.assertIn("-p", argv)
        self.assertEqual(argv[argv.index("--output-format") + 1], "json")
        self.assertEqual(argv[argv.index("--model") + 1], "claude-haiku-5-5")
        self.assertEqual(argv[argv.index("--system-prompt") + 1], "sys")
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        self.assertEqual(argv[argv.index("--tools") + 1], "")
        self.assertNotIn("--allowedTools", argv)
        self.assertEqual(run.call_args.kwargs["input"], "hello")
        self.assertTrue(run.call_args.kwargs["capture_output"])

    def test_attachments_exist_during_call(self):
        seen = {}

        def run(argv, **kw):
            path = [l for l in kw["input"].splitlines() if l.startswith("[Attached file:")]
            seen["lines"] = path
            fp = path[0].split("[Attached file: ")[1].split(" — ")[0]
            seen["exists"] = os.path.exists(fp)
            seen["data"] = open(fp, "rb").read()
            seen["argv"] = argv
            seen["cwd"] = kw["cwd"]
            return _proc()

        blocks = [
            {"type": "text", "text": "look"},
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(b"PNGDATA").decode(),
                },
            },
        ]
        self._call(run, blocks, "claude-haiku-5-5")
        self.assertTrue(seen["exists"])
        self.assertEqual(seen["data"], b"PNGDATA")
        self.assertIn("attachment_1.png", seen["lines"][0])
        argv = seen["argv"]
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "Read")
        self.assertEqual(argv[argv.index("--add-dir") + 1], seen["cwd"])

    def test_web_search_adds_tools(self):
        run = mock.Mock(return_value=_proc())
        self._call(run, [{"type": "text", "text": "x"}], "m", allow_web_search=True)
        argv = run.call_args.args[0]
        allowed = argv[argv.index("--allowedTools") + 1]
        self.assertIn("WebSearch", allowed)
        self.assertNotIn("Read", allowed)
        self.assertNotIn("--add-dir", argv)

    def test_nonzero_exit(self):
        run = mock.Mock(return_value=_proc(returncode=2, stderr="boom"))
        with self.assertRaisesRegex(cc.ClaudeCodeError, "2.*boom"):
            self._call(run, [{"type": "text", "text": "x"}], "m")

    def test_timeout(self):
        run = mock.Mock(side_effect=subprocess.TimeoutExpired("claude", 5))
        with self.assertRaisesRegex(cc.ClaudeCodeError, "timed out after 5s"):
            self._call(run, [{"type": "text", "text": "x"}], "m", timeout_sec=5)

    def test_invalid_json(self):
        run = mock.Mock(return_value=_proc(stdout="not json"))
        with self.assertRaisesRegex(cc.ClaudeCodeError, "not json"):
            self._call(run, [{"type": "text", "text": "x"}], "m")

    def test_is_error(self):
        run = mock.Mock(
            return_value=_proc(
                stdout='{"is_error": true, "subtype": "error_max_turns", "result": "bad"}'
            )
        )
        with self.assertRaisesRegex(cc.ClaudeCodeError, "error_max_turns.*bad"):
            self._call(run, [{"type": "text", "text": "x"}], "m")

    def test_missing_binary(self):
        with mock.patch.object(cc.shutil, "which", return_value=None):
            with self.assertRaisesRegex(cc.ClaudeCodeError, "not found"):
                cc.call_claude_code([{"type": "text", "text": "x"}], "m")


if __name__ == "__main__":
    unittest.main()
