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
import shutil
import subprocess
import tempfile
from typing import Optional

_EXT_BY_MEDIA_TYPE = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/jpg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


class ClaudeCodeError(Exception):
    """A failed headless Claude Code invocation (retryable)."""


def _build_prompt(content_blocks: list[dict], workdir: str) -> tuple[str, bool]:
    """Flattens content blocks into one prompt string. Attachments are written
    to files in workdir. Returns (prompt, has_attachments)."""
    parts: list[str] = []
    has_attachments = False
    for i, block in enumerate(content_blocks):
        btype = block.get("type")
        if btype == "text":
            parts.append(block["text"])
        elif btype in ("image", "document"):
            source = block.get("source", {})
            media_type = source.get("media_type", "")
            ext = _EXT_BY_MEDIA_TYPE.get(media_type)
            if source.get("type") != "base64" or ext is None:
                raise ClaudeCodeError(
                    f"Unsupported attachment for Claude Code: {btype} '{media_type}'"
                )
            path = os.path.abspath(os.path.join(workdir, f"attachment_{i}.{ext}"))
            with open(path, "wb") as f:
                f.write(base64.b64decode(source["data"]))
            has_attachments = True
            parts.append(
                f"[Attached file: {path} — open it with the Read tool before answering]"
            )
        else:
            raise ClaudeCodeError(f"Unsupported content block type: {btype}")
    return "\n\n".join(parts), has_attachments


def call_claude_code(
    content_blocks: list[dict],
    model_name: str,
    system: Optional[str] = None,
    allow_web_search: bool = False,
    timeout_sec: Optional[int] = None,
) -> str:
    """Runs one headless Claude Code turn (`claude -p`) and returns its text result."""
    binary = os.environ.get("CLAUDE_CODE_BIN", "claude")
    resolved = shutil.which(binary)
    if resolved is None:
        raise ClaudeCodeError(
            f"Claude Code binary '{binary}' not found on PATH. Install Claude Code "
            "or set CLAUDE_CODE_BIN to its path."
        )
    if timeout_sec is None:
        timeout_sec = int(os.environ.get("PAPER_ORCHESTRA_CLAUDE_CODE_TIMEOUT", "1800"))

    with tempfile.TemporaryDirectory() as workdir:
        prompt, has_attachments = _build_prompt(content_blocks, workdir)

        tools: list[str] = []
        if has_attachments:
            tools.append("Read")
        if allow_web_search:
            tools.extend(["WebSearch", "WebFetch"])

        argv = [
            resolved,
            "-p",
            "--output-format",
            "json",
            "--model",
            model_name,
            # dontAsk never prompts: anything not explicitly allowed is denied.
            "--permission-mode",
            "dontAsk",
            # Restrict the available built-in tools ("" disables all of them).
            "--tools",
            ",".join(tools),
        ]
        if tools:
            argv += ["--allowedTools", ",".join(tools)]
        if has_attachments:
            argv += ["--add-dir", workdir]
        if system:
            argv += ["--system-prompt", system]

        try:
            proc = subprocess.run(
                argv,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                cwd=workdir,
            )
        except subprocess.TimeoutExpired:
            raise ClaudeCodeError(f"Claude Code timed out after {timeout_sec}s")

    if proc.returncode != 0:
        raise ClaudeCodeError(
            f"Claude Code exited with code {proc.returncode}: "
            f"{(proc.stderr or '')[-2000:]}"
        )
    try:
        data = json.loads(proc.stdout)
    except (json.JSONDecodeError, TypeError):
        raise ClaudeCodeError(
            f"Claude Code returned non-JSON output: {(proc.stdout or '')[:500]}"
        )
    if not isinstance(data, dict):
        raise ClaudeCodeError(
            f"Claude Code returned unexpected JSON: {str(proc.stdout)[:500]}"
        )
    if data.get("is_error") or data.get("subtype", "success") != "success":
        raise ClaudeCodeError(
            f"Claude Code failed (subtype={data.get('subtype')}): {data.get('result')}"
        )
    result = data.get("result")
    if not isinstance(result, str):
        raise ClaudeCodeError(f"Claude Code JSON has no string 'result': {str(data)[:500]}")
    return result
