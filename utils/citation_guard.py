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

"""Checks that the entries of a .bib file correspond to real papers.

Each entry title is looked up in Semantic Scholar, then arXiv. Entries that
cannot be verified are kept in the file but flagged with a note field.

Usage: python -m utils.citation_guard <bib> [--report path] [--strict]
"""

import argparse
import os
import re
import time
import xml.etree.ElementTree as ET

import requests
from thefuzz import fuzz  # type: ignore

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
ARXIV_URL = "https://export.arxiv.org/api/query"
REQUEST_TIMEOUT_SEC = 20
S2_SLEEP_SEC = 1  # pause between Semantic Scholar calls (patched in tests)
BACKOFF_SEC = (2, 4, 8)  # retry delays on 429/5xx (patched in tests)
MATCH_THRESHOLD = 90
NOTE_PREFIX = "CITATION-GUARD: UNVERIFIED"

_ATOM = "{http://www.w3.org/2005/Atom}"


# ----------------------------- BibTeX parsing -----------------------------


def _read_delimited(text: str, i: int) -> tuple[str, int]:
    """Reads a {..nested..} or "..." value starting at text[i]; returns (value, next index)."""
    if text[i] == "{":
        depth = 0
        for j in range(i, len(text)):
            c = text[j]
            if c == "\\":
                continue
            if c == "{" and text[j - 1] != "\\":
                depth += 1
            elif c == "}" and text[j - 1] != "\\":
                depth -= 1
                if depth == 0:
                    return text[i + 1 : j], j + 1
        return text[i + 1 :], len(text)
    if text[i] == '"':
        depth = 0
        j = i + 1
        while j < len(text):
            c = text[j]
            if c == "\\":
                j += 2
                continue
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            elif c == '"' and depth == 0:
                return text[i + 1 : j], j + 1
            j += 1
        return text[i + 1 :], len(text)
    # bare value (number or macro)
    m = re.compile(r"[^,}\s]+").match(text, i)
    if m:
        return m.group(0), m.end()
    return "", i


def _entry_end(text: str, start: int) -> int:
    """Index just after the closing brace of the entry whose opening brace is at start."""
    depth = 0
    for j in range(start, len(text)):
        c = text[j]
        if c == "{" and (j == 0 or text[j - 1] != "\\"):
            depth += 1
        elif c == "}" and (j == 0 or text[j - 1] != "\\"):
            depth -= 1
            if depth == 0:
                return j + 1
    return len(text)


def parse_bibtex(text: str) -> list[dict]:
    """Parses BibTeX into [{"type", "key", "fields": {lower-name: value}, "raw"}]."""
    return _parse(text)[0]


def _parse(text: str) -> tuple[list[dict], list[str]]:
    """Returns (entries, unparsed raw snippets). @comment/@string/@preamble are skipped."""
    entries = []
    unparsed: list[str] = []
    header = re.compile(r"@(\w+)\s*\{\s*([^,\s]*)\s*,", re.S)
    pos = 0
    while True:
        m = re.compile(r"@(\w+)\s*([{(])").search(text, pos)
        if not m:
            break
        etype = m.group(1).lower()
        if m.group(2) == "(":
            nxt = re.compile(r"^\s*@", re.M).search(text, m.end())
            end = nxt.start() if nxt else len(text)
            pos = end
            if etype not in ("comment", "string", "preamble"):
                unparsed.append(text[m.start() : end].strip())
            continue
        end = _entry_end(text, m.end() - 1)
        raw = text[m.start() : end]
        pos = end
        if etype in ("comment", "string", "preamble"):
            continue
        h = header.match(raw)
        if not h:
            unparsed.append(raw)
            continue
        fields: dict[str, str] = {}
        i = h.end()
        body_end = len(raw) - 1 if raw.endswith("}") else len(raw)
        field_re = re.compile(r"\s*([\w\-]+)\s*=\s*")
        while i < body_end:
            fm = field_re.match(raw, i)
            if not fm:
                i += 1
                continue
            vstart = fm.end()
            if vstart >= body_end:
                break
            value, i = _read_delimited(raw, vstart)
            # concatenation with '#'
            while True:
                cm = re.compile(r"\s*#\s*").match(raw, i)
                if not cm or cm.end() >= body_end:
                    break
                more, i = _read_delimited(raw, cm.end())
                value += more
            fields[fm.group(1).lower()] = value.strip()
            cm = re.compile(r"\s*,").match(raw, i)
            if cm:
                i = cm.end()
        entries.append({"type": etype, "key": h.group(2), "fields": fields, "raw": raw})
    return entries, unparsed


# ------------------------------ Verification ------------------------------


def _normalize_title(title: str) -> str:
    title = re.sub(r"\\[a-zA-Z]+\s*", " ", title)  # latex commands
    title = re.sub(r"[{}\\$]", "", title)
    title = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    return re.sub(r"\s+", " ", title).strip()


def _clean_query_title(title: str) -> str:
    """Strips LaTeX commands, braces and quotes so the title is a plain query."""
    title = re.sub(r"\\[a-zA-Z]+\*?", " ", title)  # \emph{X} -> {X}
    title = re.sub(r"\\(.)", r"\1", title)  # \& -> &
    title = re.sub(r'[{}"]', "", title)
    return re.sub(r"\s+", " ", title).strip()


def _year_of(value) -> int | None:
    m = re.search(r"(1[5-9]|20)\d{2}", str(value or ""))
    return int(m.group(0)) if m else None


def _is_match(title: str, year: int | None, cand_title: str, cand_year: int | None) -> tuple[bool, int]:
    score = fuzz.token_sort_ratio(_normalize_title(title), _normalize_title(cand_title))
    if score < MATCH_THRESHOLD:
        return False, score
    if year is not None and cand_year is not None and abs(year - cand_year) > 1:
        return False, score
    return True, score


def _get_with_backoff(url: str, **kwargs) -> requests.Response:
    """GET with 2s/4s/8s backoff on 429/5xx (max 3 retries)."""
    for attempt in range(len(BACKOFF_SEC) + 1):
        resp = requests.get(url, timeout=REQUEST_TIMEOUT_SEC, **kwargs)
        if resp.status_code != 429 and resp.status_code < 500:
            return resp
        if attempt < len(BACKOFF_SEC):
            time.sleep(BACKOFF_SEC[attempt])
    return resp


def _search_s2(title: str, year: int | None):
    headers = {}
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        headers["x-api-key"] = key
    resp = _get_with_backoff(
        S2_SEARCH_URL,
        params={"query": title, "limit": 5, "fields": "title,year,externalIds,url"},
        headers=headers,
    )
    time.sleep(S2_SLEEP_SEC)
    resp.raise_for_status()
    for cand in resp.json().get("data") or []:
        ok, score = _is_match(title, year, cand.get("title") or "", cand.get("year"))
        if ok:
            return {"match_title": cand.get("title"), "url": cand.get("url"), "score": score}
    return None


def _search_arxiv(title: str, year: int | None):
    resp = _get_with_backoff(
        ARXIV_URL,
        params={"search_query": f'ti:"{title}"', "max_results": 5},
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    for e in root.findall(f"{_ATOM}entry"):
        cand_title = " ".join((e.findtext(f"{_ATOM}title") or "").split())
        published = e.findtext(f"{_ATOM}published") or ""
        ok, score = _is_match(title, year, cand_title, _year_of(published[:4]))
        if ok:
            return {
                "match_title": cand_title,
                "url": (e.findtext(f"{_ATOM}id") or "").strip(),
                "score": score,
            }
    return None


def verify_entry(entry: dict) -> dict:
    """Returns {"status": verified|not_found|lookup_failed, "source", "match_title", "url", "score"}."""
    result = {"status": "not_found", "source": None, "match_title": None, "url": None, "score": None}
    fields = entry.get("fields", {})
    title = re.sub(r"\s+", " ", fields.get("title", "")).strip()
    if not title:
        return result
    query_title = _clean_query_title(title)
    year = _year_of(fields.get("year"))
    errors = []
    for source, search in (("semantic_scholar", _search_s2), ("arxiv", _search_arxiv)):
        try:
            hit = search(query_title, year)
        except (requests.RequestException, ET.ParseError, ValueError) as e:
            errors.append(f"{source}: {e}")
            continue
        if hit:
            result.update(hit, status="verified", source=source)
            return result
    if errors:
        # Not verified, and at least one source could not be queried.
        result.update(status="lookup_failed", error="; ".join(errors))
    return result


# ------------------------------- File guard -------------------------------


def _add_note(raw: str, status: str) -> str:
    """Adds the guard note to a raw entry, merging into an existing note field."""
    text = f"{NOTE_PREFIX} ({status})"
    m = re.search(r"(?<![\w-])note\s*=\s*", raw, re.I)
    if m:
        i = m.end()
        if raw[i] in '{"':
            return raw[: i + 1] + text + "; " + raw[i + 1 :]
        return raw[:i] + "{" + text + "; } # " + raw[i:]
    stripped = raw.rstrip()
    body = stripped[:-1].rstrip()
    if not body.endswith(","):
        body += ","
    return f"{body}\n  note = {{{text}}}\n}}"


def guard_bib_file(bib_path: str, report_path: str | None = None, strict: bool = False) -> dict:
    with open(bib_path, "r", encoding="utf-8") as f:
        text = f.read()
    entries, unparsed = _parse(text)
    results = [(e, verify_entry(e)) for e in entries]

    counts = {"total": len(results), "verified": 0, "not_found": 0, "lookup_failed": 0, "unparsed": len(unparsed)}
    for _, r in results:
        counts[r["status"]] += 1
    unverified = [e["key"] for e, r in results if r["status"] != "verified"]

    report_path = report_path or os.path.join(
        os.path.dirname(os.path.abspath(bib_path)), "citation_report.md"
    )
    lines = [
        "# Citation report",
        "",
        f"- Total: {counts['total']}",
        f"- Verified: {counts['verified']}",
        f"- Not found: {counts['not_found']}",
        f"- Lookup failed: {counts['lookup_failed']}",
        f"- Unparsed: {counts['unparsed']}",
        "",
        "| key | title | status | source | matched title / url |",
        "| --- | --- | --- | --- | --- |",
    ]
    for e, r in results:
        cells = [
            e["key"],
            e["fields"].get("title", ""),
            r["status"],
            r["source"] or "",
            " ".join(x for x in (r["match_title"], r["url"]) if x),
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    if unparsed:
        lines += ["", "## Unparsed entries (not checked)", ""]
        for raw in unparsed:
            lines.append("- `" + " ".join(raw.split())[:120].replace("`", "'") + "`")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    # Flag unverified entries in place (entries are kept).
    new_text = text
    for e, r in results:
        if r["status"] != "verified" and NOTE_PREFIX not in e["raw"]:
            new_text = new_text.replace(e["raw"], _add_note(e["raw"], r["status"]), 1)
    if new_text != text:
        with open(bib_path, "w", encoding="utf-8") as f:
            f.write(new_text)

    print(
        f"Citation guard: {counts['verified']}/{counts['total']} verified, "
        f"{counts['not_found']} not found, {counts['lookup_failed']} lookup failed, "
        f"{counts['unparsed']} unparsed "
        f"(report: {report_path})"
    )
    for key in unverified:
        print(f"Warning: citation '{key}' could not be verified.")
    for raw in unparsed:
        print(f"Warning: could not parse bib entry (unchecked): {' '.join(raw.split())[:80]}")

    if strict and unverified:
        raise ValueError(f"Unverified citations: {', '.join(unverified)}")
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description="Verify .bib entries against Semantic Scholar and arXiv.")
    parser.add_argument("bib")
    parser.add_argument("--report", default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)
    try:
        guard_bib_file(args.bib, report_path=args.report, strict=args.strict)
    except ValueError as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
