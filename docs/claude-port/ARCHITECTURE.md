# PaperOrchestra: provider map for the Claude port

Read-only survey of upstream `main` @ d9bae99, made before any code changed. Paths are relative to the repo root.

## 1. Existing provider layer

`utils/llm_backend_utils.py` is a thin router with three entry points:

| Function | OpenAI branch | Gemini branch (the default) |
|---|---|---|
| `call_llm_with_text_prompt` | `openai_utils.call_openai_models_with_text_prompt` | `gemini_utils.call_gemini_with_text_prompt` |
| `call_llm_with_images` | content parts with base64 `image_url` | `gemini_utils.call_gemini_with_images` |
| `call_llm_with_pdf` | text extracted with `pdf_utils.load_paper` | raw PDF bytes as `types.Part` |

- **Routing:** the model name is checked for the substrings `gpt`, `o1` or `o3`. Anything else goes to Gemini.
- **Return shape:** every call returns `{"raw_response": str, "parsed_response": Any}`.
- **Retries:** each provider util retries 5 times with exponential backoff (base 5 s) and asserts `parsed_response is not None`. Gemini stops retrying when the input exceeds its token limit.
- **Structured output:** JSON is requested in the prompt and parsed with a regex plus repair (`parse_*_json_results`). Nothing uses `response_schema`, `response_mime_type` or the thinking config.
- **`generation_configs` keys in use:** `system_instruction`, `temperature`, and `tools` (Google Search, one call site only).

**Key finding:** most pipeline stages don't use the router. They call `gemini_utils` directly, so `--writer_model_name gpt-…` already fails for outline, literature and refinement upstream. Model names currently reach only a subset of the code.

## 2. Model flags and how they flow

- **Defaults:** `paper_writing_cli.py:53-82` sets `--writer_model_name` and `--reflection_model_name` to `gemini-3.1-pro-preview`, and `--image_model_name` to `gemini-3-pro-image-preview`.
- **writer:** used by `OutlineAgent`, `LiteratureReviewAgent` and `SectionWritingAgent` (`methods/paper_writer.py:94,114`).
- **reflection:** used by `ContentRefinementAgent` (`methods/paper_writer.py:171`).
- **`--use_plotting`:** switches to `methods/paper_writer_with_plotting.py` and the PaperBanana utilities.
- **`paper_writing_cli.sh`:** passes no model flags.
- **`frontend/app.py`:** offers a hard-coded Gemini-only list (`:145`) and shells out to the CLI.

## 3. Environment variables

| Var | Where it is read |
|---|---|
| `GEMINI_API_KEY`, `VERTEX_AI_PROJECT`, `VERTEX_AI_LOCATION` | `utils/gemini_utils.py:41-49` (Vertex wins), via dotenv `../.env` |
| `OPENAI_API_KEY` | `utils/openai_utils.py:45`, lazy client |
| `SEMANTIC_SCHOLAR_API_KEY` | `utils/scholar_utils.py:27`, `autoraters/citation_f1.py:40` |
| `SMTP_EMAIL`, `SMTP_PASSWORD` | `frontend/frontend_utils.py` (not LLM) |

## 4. Call sites

Backend key: **G** = `gemini_utils` direct, **R** = `llm_backend_utils` router, **O** = `openai_utils` direct.

| # | File:line | Function | Backend | Needs |
|---|---|---|---|---|
| 1 | methods/agents/outline_agent.py:51 | OutlineAgent.run | G contents | text → JSON |
| 2 | methods/agents/literature_review_agent.py:464 | _discover_candidates | G contents, **hard-coded `gemini-3-flash-preview`** | **Google Search grounding**, temp, JSON |
| 3 | methods/agents/literature_review_agent.py:499 | _synthesize_content | G contents | system prompt, long context → LaTeX |
| 4 | methods/agents/section_writing_agent.py:201 | SectionWritingAgent.run | R images | figure images, temp 0.7 |
| 5 | methods/agents/content_refinement_agent.py:165 | _get_formatting_review | G images | PDF page-grid PNGs → JSON |
| 6 | methods/agents/content_refinement_agent.py:293 | run | G contents | **inline PDF bytes**, system prompt → JSON |
| 7 | methods/agents/content_refinement_agent.py:520 | run | G contents | text |
| 8–13 | utils/paper_banana_utils.py:378–662 | few-shot, caption, figure plan/style/critique, plot code | G contents | text plus optional JPEGs; plot code is `exec()`'d (:308) |
| 14 | utils/paper_banana_utils.py:674 | generate_figure_visuals | `generate_image_with_gemini` | **image generation, no Claude equivalent** |
| 15 | utils/content_parsing_utils.py:38 | extract_paper_title_from_citation | G text, **hard-coded flash** | text |
| 16 | utils/pdf_utils.py:138 | extract_reference_from_pdf_text | R text | text |
| 17–19 | autoraters/agent_review.py:137,172,200 | reviews / meta-review | G text | system prompt, temp 0.75 |
| 20 | autoraters/citation_f1.py:185 | _get_citation_priorities | R text | JSON |
| 21 | autoraters/lit_review_quality.py:77 | rate_single_paper | R pdf | PDF → JSON |
| 22 | autoraters/sxs_lit_review_quality.py:40,55 | rate_sxs_lit_review | R text | branches on `gemini`/`gpt` |
| 23–24 | autoraters/sxs_paper_quality.py:100,121 | rate_sxs_paper_quality | O content / G contents | page JPEGs / PDF bytes |

- `methods/paper_writer*.py` and `frontend/` make no direct LLM calls.
- **No tool or function calling** beyond Google Search grounding (#2).
- **Semantic Scholar:** `/graph/v1/paper/search` with an `X-API-KEY` header (`utils/scholar_utils.py:63`), used by the literature agent and `citation_f1`.

## 5. Repo conventions

- **License header:** Apache-2.0 block, `# Copyright 2026 Google LLC`, on lines 1–13 of every `.py` file.
- **`requirements.txt`:** unpinned (plan assumed pinned; the code wins).
- **Tests and CI:** none.
- **Upstream issue (out of scope):** `utils/scholar_utils.py:28` and `autoraters/citation_f1.py:41` print the Semantic Scholar key to stdout.

## 6. Gaps a Claude port must handle

1. **Direct Gemini calls:** these bypass the router (sites 1–3, 5–15, 17–19), so routing only in `llm_backend_utils` reaches about four sites.
2. **Gemini-native content:** `call_gemini_with_contents` receives `google.genai` content (`str`, `types.Part` with `inline_data` bytes for PDF/JPEG/PNG). A Claude adapter must convert these parts to `text` / `image` / `document` blocks.
3. **Google Search grounding:** the nearest Claude equivalent is the `web_search` server tool. The call site is pinned to Gemini Flash.
4. **Image generation:** Claude has no image-generation API, so `--image_model_name` stays Gemini.
5. **`temperature`:** current Claude models reject sampling parameters, so it must be dropped. The OpenAI branch already drops it.
