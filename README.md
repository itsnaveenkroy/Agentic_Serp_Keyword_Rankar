# Omnie Keyword Ranking

Python utility that ingests an Excel keyword list, queries Google via SearchAPI.io (SerpAPI fallback), and writes back organic and local/Places ranks with lightweight agentic logic and domain normalization.

## What it does
- Reads `Keyword_Ranking.xlsx` → sheet `Keywords` → Column B (keyword), Column C (target URL/domain).
- Detects local intent (e.g., "near me") and, if present, queries both organic and Places.
- Checks up to 5 SERP pages (50 results) per channel; matches ranks using normalized domains (`www`/`m` stripped, paths ignored).
- Writes ranks or status text to `Keyword_Ranking_updated.xlsx` in the same sheet.
- Handles API quota/429 with exponential backoff; surfaces clear status messages when ranks are absent or blocked.

## How it works (workflow)
1) **Plan** — `PlannerAgent` flags if Places search is needed based on keyword hints.
2) **Search** — `SearchTool` calls SearchAPI.io (default) or SerpAPI with paging (10 results/page) for organic and/or Places, logging key params.
3) **Normalize & match** — URLs are normalized; `RankEvaluator` finds the first matching domain and computes rank (page*10+position).
4) **Write back** — `ExcelWriterTool` writes organic/Places ranks or a status string to the workbook.
5) **Status & resilience** — Backoff on quota/429, otherwise conclude with success, not-found, or informative statuses.

## Tooling used
- **HTTP/Search**: SearchAPI.io (`SEARCHAPI_KEY`) or SerpAPI (`SERPAPI_KEY`) for Google results.
- **HTML parsing**: `BeautifulSoup` for compatibility (legacy scraping paths kept minimal).
- **Excel I/O**: `openpyxl` to read/write the `Keywords` sheet.
- **Domain handling**: `urlparse` + custom normalization helpers to align variants.

## Inputs & outputs
- **Input file**: `Keyword_Ranking.xlsx`
   - Column B: keyword text.
   - Column C: target URL or domain (with/without scheme, with/without path).
   - Row 2 optional headers: `Google Places`, `Google Links` (fallback to cols 34/35 if absent).
- **Output file**: `Keyword_Ranking_updated.xlsx` with ranks or statuses in the same sheet.

## Setup
1. Clone/download.
2. (Recommended) create a virtualenv.
3. Install deps:
    ```bash
    pip install -r requirements.txt
    ```
4. Provide keys via env or `.env`:
    ```env
    SEARCHAPI_KEY=your_searchapi_key
    # SERPAPI_KEY=your_serpapi_key   # optional fallback
    ```

## Usage
Run from project root:
```bash
python nkr.py
```
- Reads `Keyword_Ranking.xlsx`, writes `Keyword_Ranking_updated.xlsx`.
- Logs requests/ranks to stdout for traceability.

### Local/Places intent
- Triggered when keyword contains hints like "near me", "nearby", "close to", "in ".
- Default geo is `Noida, India`; change `DEFAULT_LOCATION` or `DEFAULT_GEO_PARAMS` in `nkr.py` if needed.

## Status messages (written to cells when rank absent)
- `Success` (ranks populated)
- `Not in top 50`
- `Blocked – API quota exceeded`
- `Blocked – retry later`
- `Places visible but website unavailable via API`

## Examples
- Keyword: `best cafes near me`, Target: `examplecafe.com`
   - Runs organic + Places with location bias; returns first matching rank (domain-normalized).
- Keyword: `buy widgets online`, Target: `shop.example.com/widgets`
   - Organic only; normalizes target to `example.com` for matching.

## Troubleshooting
- No ranks but no error: likely not in top 50 organic/Places.
- `Blocked – API quota exceeded`: add credits or switch key/provider.
- Frequent 429: reduce volume; the agent backs off exponentially.
- Domain mismatches: ensure target URL/domain in Column C is the intended site; normalization handles schemes, `www/m`, and paths.

## Future scope (ideas)
- Add configurable max pages and delay jitter via CLI flags or env vars.
- Emit a CSV/JSON summary alongside Excel for pipelines.
- Optional proxies or regional endpoints if provider supports.
- Add lightweight unit tests around parsing and normalization.

## Safety and Terms
Uses SearchAPI.io/SerpAPI to fetch Google results. Respect provider limits and Google ToS; keep volume low and compliant.

## Mentor/Evaluator Summary
- “HTML scraping components like BeautifulSoup, direct Google requests, and user-agent rotation were intentionally disabled. The system now operates entirely via SERP APIs to remain compliant and stable.”
- Early versions scraped `google.com/search` with BeautifulSoup and custom user-agents; frequent 429s and ToS risk led to the current SERP API-only design (SearchAPI.io default, SerpAPI fallback).
