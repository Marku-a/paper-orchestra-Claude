---
name: paper-orchestra
description: Write a paper draft from idea.md, experimental_log.md, template.tex and conference_guidelines.md using Claude Code agents (architect, planner, executor, grunt), either as the full Python pipeline through headless Claude Code or step by step with subagents.
---

# PaperOrchestra with Claude Code

Turns raw materials (`idea.md`, `experimental_log.md`) plus a LaTeX template dir (`template.tex`, `guidelines.md`) into a LaTeX draft and PDF. Run from the repo root.

## Mode A: Pipeline mode
The Python pipeline runs end to end; every LLM call goes through headless Claude Code on your subscription (`PAPER_ORCHESTRA_CLAUDE_BACKEND=claude_code`).

```bash
scripts/run_claude.sh \
  --raw_materials_dir /path_to_your_raw_materials/ \
  --latex_template_dir templates/cvpr2025 \
  --output_dir /path_to_your_output_folder/ \
  --use_plotting true
```
Same args as `paper_writing_cli.py`. Required: `--raw_materials_dir`, `--latex_template_dir`. Optional: `--output_dir`, `--idea_filename` (default `idea_sparse.md`), `--experimental_log_filename` (default `experimental_log.md`), `--writer_model_name`, `--reflection_model_name`, `--research_cutoff` (YYYY-MM), `--use_plotting`, `--plotting_model_name`, `--image_model_name`, `--plotting_max_critic_rounds`.

## Mode B: Agent mode
The main session plays architect and delegates. Load repo prompts verbatim with `python scripts/render_prompt.py <module>:<NAME>` (`--list` shows all). Work in one output dir, with the same file names as the Python pipeline.

1. **architect**: outline. Prompt `methods.prompts.outline_agent:outline_agent_system_prompt`. Save JSON with keys `plotting_plan`, `intro_related_work_plan`, `section_plan` as `literature_agent_output/outline_v1.json` (copy to `outline.json`).
2. **planner**: search plan from `intro_related_work_plan` and `citation_hints` (prompt `methods.prompts.literature_review_agent:literature_review_agent_writter_prompt`). **grunt**: Semantic Scholar / arXiv lookups.
3. **grunt**: build `literature_agent_output/references.bib` only from verified lookups, then run `python -m utils.citation_guard literature_agent_output/references.bib --report citation_report.md`. Write `citation_map.json`. Copy to `latex_writeup/references.bib`; `updated_template.tex` becomes `latex_writeup/template.tex`.
4. **executor**: per-section writing with `methods.prompts.section_writing_agent:section_writing_agent_prompt` into `latex_writeup/raw_draft_paper.tex`.
5. **executor**: figures. Plots as matplotlib code, saved under `latex_writeup/figures/` with `figures/info.json` (plot prompts: `utils.paper_banana_utils:PLOT_*_PROMPT`). Then the LaTeX compile-and-fix loop. If no TeX is installed, stop that step and record it as skipped.
6. **planner**: refinement/reflection with `methods.prompts.content_refinement_agent:content_refinement_agent_system_prompt` (and `methods.prompts.format_agent:format_agent_system_prompt`), working in `content_refinement_workdir/`.
7. **architect**: final consistency check (claims vs `experimental_log.md`, every `\cite` key in the .bib and verified, numbers match). Write `run_report.md`: what was produced, unverified citations, skipped steps. Final PDF: `final_paper.pdf`.

Pipeline output files (both modes): `outline.json`, `plotting_results.json`, `literature_agent_output/`, `latex_writeup/`, `content_refinement_workdir/`, `time_breakdown.json`, `final_paper.pdf`.

## Rules
- Never invent citations or numbers. Unverified citations go in `run_report.md`, never silently kept.
- Stay inside the output dir; no secrets in files.

## Env vars
`PAPER_ORCHESTRA_CLAUDE_BACKEND`, `PAPER_ORCHESTRA_SEARCH_MODEL`, `PAPER_ORCHESTRA_DIAGRAM_BACKEND`, `PAPER_ORCHESTRA_CITATION_GUARD`.

## Notes
- **Cost:** Claude Code usage counts against the subscription limits; no API key is needed.
- **Diagrams:** diagram images need Gemini; otherwise diagrams are drawn as matplotlib code (`PAPER_ORCHESTRA_DIAGRAM_BACKEND=auto`).
