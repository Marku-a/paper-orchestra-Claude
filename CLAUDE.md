# CLAUDE.md

PaperOrchestra: a multi-agent pipeline that turns `idea.md` + `experimental_log.md` + a LaTeX template into a paper draft (`paper_writing_cli.py`, `methods/`, `utils/`, `autoraters/`). See `README.md`.

## Fork-only layer
- Branch `claude-code-native` adds Claude Code support on top of upstream work.
- The upstream PR branch `feat/anthropic-provider` must stay free of fork-only files (`.claude/`, `CLAUDE.md`, `scripts/`, `docs/claude-port/`, citation guard, Claude Code backend). Never merge this branch into it.

## Tests
`python -m unittest discover -s tests -t . -v`

## Running
- Skill: `.claude/skills/paper-orchestra/SKILL.md` (pipeline mode `scripts/run_claude.sh <paper_writing_cli.py args>`, or agent mode with `.claude/agents/{architect,planner,executor,grunt}.md`).
- Reuse repo prompts verbatim: `python scripts/render_prompt.py --list`.

## Env vars
- `PAPER_ORCHESTRA_CLAUDE_BACKEND` (`claude_code` routes LLM calls through headless Claude Code)
- `PAPER_ORCHESTRA_SEARCH_MODEL`
- `PAPER_ORCHESTRA_DIAGRAM_BACKEND` (`auto`: Gemini images if available, else matplotlib)
- `PAPER_ORCHESTRA_CITATION_GUARD`

## Rules
- Every citation must pass the citation guard: `python -m utils.citation_guard <bib> [--report path] [--strict]`. Unverified entries are reported, never silently kept; never invent citations or numbers.
- No secrets in git (`.env`, API keys).
