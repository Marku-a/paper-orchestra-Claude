#!/usr/bin/env bash
# Fork default config: run Paper Orchestra on Claude (via Claude Code), no Gemini needed.
set -euo pipefail
cd "$(dirname "$0")/.."

export PAPER_ORCHESTRA_CLAUDE_BACKEND="${PAPER_ORCHESTRA_CLAUDE_BACKEND:-claude_code}"
export PAPER_ORCHESTRA_SEARCH_MODEL="${PAPER_ORCHESTRA_SEARCH_MODEL:-claude-sonnet-5-5}"
export PAPER_ORCHESTRA_DIAGRAM_BACKEND="${PAPER_ORCHESTRA_DIAGRAM_BACKEND:-auto}"
export PAPER_ORCHESTRA_CITATION_GUARD="${PAPER_ORCHESTRA_CITATION_GUARD:-1}"

exec python paper_writing_cli.py \
  --writer_model_name "${WRITER_MODEL:-claude-sonnet-5-5}" \
  --reflection_model_name "${REFLECTION_MODEL:-claude-opus-5-5}" \
  --plotting_model_name "${WRITER_MODEL:-claude-sonnet-5-5}" \
  "$@"
