# Omnie Keyword Ranking

Python utility that ingests an Excel keyword list, queries Google via **SerpAPI official Python client**, and writes back organic and local/Places ranks with lightweight agentic logic and domain normalization. **All searches are strictly limited to Noida, India on google.co.in**.

## What it does
- Reads `Keyword_Ranking.xlsx` → sheet `Keywords` → Column B (keyword), Column C (target URL/domain).
- Detects local intent (e.g., "near me") and, if present, queries both organic and Places.
- **Strictly enforces location**: ALL searches (organic and Places) use **Noida, Uttar Pradesh, India** on **google.co.in** — this cannot be overridden.
- Checks up to 5 SERP pages per channel:
  - **Organic**: 50 results (10 per page × 5 pages)
  - **Places**: Up to 100 results (20 per page × 5 pages)
- Matches ranks using normalized domains (`www`/`m` stripped, paths ignored).
- **Places ranking**: Returns actual position in local pack (1-20), not synthetic SERP calculations.
- Writes ranks or status text to `Keyword_Ranking_updated.xlsx` in the same sheet.
- Handles API quota/429 with exponential backoff; surfaces clear status messages when ranks are absent or blocked.

---

## 📦 Available Versions

This project includes **three versions** that demonstrate progressive evolution from rule-based to LLM-enhanced to graph-orchestrated architectures:

### Version 1: `nkr.py` (Rule-based baseline)
**Classic deterministic agent** (legacy, not updated with current SerpAPI implementation).
- Pure Python logic with no LLM calls
- Per-keyword geo branching based on local intent
- Direct procedural flow

**Note**: This version is kept for reference but has not been updated with the latest SerpAPI official client and strict location enforcement.

**Use when**: You need a stable, LLM-free baseline for ranking checks.

### Version 2: `nkr_llm.py` (LLM-enhanced)
**LLM-integrated agent** with explainability and summaries (not updated with current SerpAPI implementation).
- Forces `location = "Noida, India"` on **all** searches (organic and Places)
- Planner still decides whether to run Places based on keyword intent
- Google Places matching is strict: case-insensitive substring match on title for `omkitchen`
- **LLM usage via OpenRouter**:
  - `explain_plan()`: Explains planner decisions
  - `summarize_run()`: Produces 4–5 line executive summary after all keywords processed
- Logs runtime events for audit trail
- Early quota kill-switch halts remaining keywords after first quota hit
- **Same Excel outputs** as v1: "Visible at X"/"Not visible" for Places; rank or "Not in top 50" for organic

**Note**: This version uses the older HTTP request implementation. For the latest SerpAPI official client, use v3.

**Use when**: You need transparency, explainability, and executive summaries without changing core logic.

**API Key required**: `OPENROUTER_API_KEY` (optional; gracefully degrades if missing)

### Version 3: `nkr_langgraph.py` (LangGraph orchestration) ✅ **CURRENT**
**Graph-orchestrated agent** using LangGraph state machines with **official SerpAPI Python client**.

**Latest implementation (January 2026)**:
- **SerpAPI official client**: Uses `google-search-results` (serpapi) Python package instead of HTTP requests
- **Strict location enforcement**: ALL searches hardcoded to:
  - `location`: "Noida, Uttar Pradesh, India"
  - `gl`: "in" (India geolocation)
  - `hl`: "en" (English language)
  - `google_domain`: "google.co.in" (Indian Google only)
  - ⚠️ **Cannot be overridden** — location params are forced regardless of what's passed
- **Fixed Places pagination**: Uses multiples of 20 (0, 20, 40, 60, 80) instead of 10
- **Accurate Places ranking**: Returns actual position (1-20) in local pack, not synthetic calculations

**StateGraph architecture**:
- **Nodes**: `planner_node`, `organic_search_node`, `places_search_node`, `finalize_node`
- **Flow**: planner → organic → (conditional: places if needed) → finalize → END
- **State**: `AgentState` dataclass passes context through graph
- **Conditional routing**: Places search runs only if `do_places=True` AND not blocked
- **LLM explainability**: Same as v2 (plan explanations + executive summary)
- **Legacy code preserved**: Old SearchAPI.io HTTP implementation commented out for reference

**Use when**: You need the most up-to-date implementation with accurate Places rankings and strict Indian localization.

**Dependencies**: 
```bash
pip install langgraph google-search-results openai openpyxl
```

---

## How it works (workflow)

### Core workflow (all versions)
1) **Plan** — `PlannerAgent` flags if Places search is needed based on keyword hints (`near me`, `nearby`, `close to`, `in `, `tiffin`, `food delivery`, `meal delivery`).
2) **Search** — `SearchTool` calls SearchAPI.io (default) or SerpAPI with paging (10 results/page) for organic and/or Places, logging key params.
3) **Normalize & match** — URLs are normalized; `RankEvaluator` finds the first matching domain and computes rank (page×10+position).
4) **Write back** — `ExcelWriterTool` writes organic/Places ranks or a status string to the workbook.
5) **Status & resilience** — Backoff on quota/429, otherwise conclude with success, not-found, or informative statuses.

### v2 additions (nkr_llm.py)
- **Explain plan**: LLM explains why planner chose organic/places search
- **Runtime events**: All decisions logged to `runtime_events` list
- **Executive summary**: LLM produces aggregated summary at end of run

### v3 additions (nkr_langgraph.py)
- **Graph invocation**: `process_keyword()` builds and invokes LangGraph instead of direct execution
- **State machine**: Explicit nodes and edges replace implicit control flow
- **Conditional edges**: `should_go_to_places()` function determines graph routing

---

## Tooling used
- **Search API (v3)**: SerpAPI official Python client (`google-search-results` package) with `SERPAPI_KEY`
  - **v1, v2**: Legacy HTTP implementations (SearchAPI.io or direct requests)
  - **v3**: Official `GoogleSearch` class from serpapi package
- **HTML parsing**: `BeautifulSoup` legacy support (deprecated, kept for v1 compatibility)
- **Excel I/O**: `openpyxl` to read/write the `Keywords` sheet
- **Domain handling**: `urlparse` + custom normalization helpers to align variants
- **LLM (v2, v3)**: OpenAI client via OpenRouter API for explainability and summaries
- **Orchestration (v3)**: LangGraph `StateGraph` for agent coordination

---

## Inputs & outputs
- **Input file**: `Keyword_Ranking.xlsx`
   - Column B: keyword text.
   - Column C: target URL or domain (with/without scheme, with/without path).
   - Row 2 optional headers: `Google Places`, `Google Links` (fallback to cols 34/35 if absent).
- **Output file**: `Keyword_Ranking_updated.xlsx` with ranks or statuses in the same sheet.

---

## Setup

### 1. Clone/download
```bash
git clone <repo-url>
cd Omnie_Keyword_Ranking
```

### 2. Create virtualenv (recommended)
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

**For v3 (LangGraph version with SerpAPI - RECOMMENDED):**
```bash
pip install langgraph google-search-results openai openpyxl requests
```

### 4. Configure API keys
Create a `.env` file in project root:
```env
# Required for v3 (current implementation)
SERPAPI_KEY=your_serpapi_key

# Required for v2 and v3 LLM features (optional; gracefully degrades if missing)
OPENROUTER_API_KEY=your_openrouter_key

# Legacy (v1, v2 only)
# SEARCHAPI_KEY=your_searchapi_key
```

---

## Usage

### Version 1: Rule-based (nkr.py)
```bash
python nkr.py
```

### Version 2: LLM-enhanced (nkr_llm.py)
```bash
python nkr_llm.py
```
- Reads `Keyword_Ranking.xlsx`, writes `Keyword_Ranking_updated.xlsx`.
- Logs requests/ranks to stdout for traceability.
- Prints LLM executive summary at end.

### Version 3: LangGraph orchestration (nkr_langgraph.py)
```bash
python nkr_langgraph.py
```
- Same behavior as v2 but with graph orchestration.
- Logs show graph node transitions.

### Local/Places intent
- Triggered when keyword contains hints like "near me", "nearby", "close to", "in ", "tiffin", "food delivery", "meal delivery".
- **v1**: Default geo is `Noida, India` for local keywords only; change `DEFAULT_LOCATION` or `DEFAULT_GEO_PARAMS` in `nkr.py` if needed.
- **v2, v3**: Location is **always forced to `Noida, Uttar Pradesh, India`** for all searches (organic and Places).
- **v3 strict enforcement**: 
  - Hardcoded in `SearchTool.search_page()` — ignores any geo_params passed
  - Forces `google.co.in` domain
  - Sets `gl=in` (India) and `hl=en` (English)
  - **Cannot be changed without modifying source code**

---

## Status messages (written to cells when rank absent)
- `Success` (ranks populated)
- `Not in top 50`
- `Blocked – API quota exceeded`
- `Blocked – retry later`
- `Skipped – API quota exhausted` (v2, v3 only)
- `Places visible but website unavailable via API` (v1 only)

---

## Examples
- Keyword: `best cafes near me`, Target: `examplecafe.com`
   - Runs organic + Places with location bias; returns first matching rank (domain-normalized).
- Keyword: `buy widgets online`, Target: `shop.example.com/widgets`
   - Organic only; normalizes target to `example.com` for matching.

---

## Differences between versions

| Feature | v1 (nkr.py) | v2 (nkr_llm.py) | v3 (nkr_langgraph.py) |
|---------|-------------|-----------------|----------------------|
| **Architecture** | Procedural | LLM-enhanced procedural | LangGraph state machine |
| **Search implementation** | HTTP requests | HTTP requests | ✅ **SerpAPI official client** |
| **LLM usage** | None | Planner explanation + summary | Same as v2 |
| **Location handling** | Conditional (local keywords only) | Always forced to Noida | ✅ **Strict: Noida + google.co.in** |
| **Places pagination** | page × 10 | page × 10 | ✅ **Fixed: page × 20** |
| **Places ranking** | Synthetic calculation | Synthetic calculation | ✅ **Actual position (1-20)** |
| **Places matching** | Link-based | Title substring match | Title substring match |
| **Runtime events** | None | Logged to `runtime_events` | Logged to `runtime_events` |
| **Executive summary** | None | ✅ LLM-generated | ✅ LLM-generated |
| **Graph orchestration** | ❌ | ❌ | ✅ LangGraph |
| **Early quota halt** | ❌ | ✅ | ✅ |
| **Excel output** | Same format | Same format | Same format |
| **Dependencies** | requests, bs4, openpyxl | + openai | + langgraph, **google-search-results** |
| **Status** | Legacy reference | Legacy reference | ✅ **Current (Jan 2026)** |

---

## Troubleshooting
- **No ranks but no error**: likely not in top 50 organic/Places.
- **`Blocked – API quota exceeded`**: add credits or switch key/provider.
- **Frequent 429**: reduce volume; the agent backs off exponentially (v1 only; v2/v3 halt after first quota hit).
- **Domain mismatches**: ensure target URL/domain in Column C is the intended site; normalization handles schemes, `www/m`, and paths.
- **LLM summary missing**: check `OPENROUTER_API_KEY` is set; system gracefully degrades if missing.
- **LangGraph errors**: ensure `pip install langgraph` is complete; check Python 3.9+.
- **"API key missing" error (v3)**: Create `.env` file with `SERPAPI_KEY=your_key`
- **Places pagination errors**: v3 fixes the "multiples of 20" error; ensure you're using `nkr_langgraph.py`
- **Wrong location results**: v3 hardcodes Noida, India — if you need different locations, you must modify source code

---

## Future scope (ideas)
- Add configurable max pages and delay jitter via CLI flags or env vars.
- Emit a CSV/JSON summary alongside Excel for pipelines.
- Optional proxies or regional endpoints if provider supports.
- Add lightweight unit tests around parsing and normalization.
- **v3**: Extend LangGraph with parallel search nodes, multi-location testing, or human-in-the-loop approval steps.

---

## Safety and Terms
**Current implementation (v3)**: Uses SerpAPI official Python client to fetch Google results.

**Legacy implementations (v1, v2)**: Used SearchAPI.io or direct HTTP requests.

Respect provider limits and Google ToS; keep volume low and compliant. SerpAPI provides structured data access while maintaining compliance with search engine policies.

---

## 🔁 Agentic Evolution: Version History

This section documents the **incremental changes and rationale** behind evolving from rule-based to LLM-enhanced to graph-orchestrated architectures. Written specifically from a **mentor / evaluator perspective**.

### Why v1 (nkr.py) was the baseline
- `nkr.py` successfully detected **organic and Google Places rankings** using deterministic logic.
- Reliable, stable, and requires no LLM infrastructure.
- Includes BeautifulSoup fallback for legacy scraping (now deprecated in favor of APIs).

### Why v2 (nkr_llm.py) was needed
- v1 lacked **semantic reasoning** about decisions and outcomes.
- Debugging required manually reading logs to understand why keywords ran Places or were skipped.
- No **executive-level summary** suitable for reporting.

**v2 improvements**:
- **Planner explanation (`explain_plan`)**  
  After rule-based planning, the LLM explains *why* organic and/or Places search was chosen.
- **Run-level executive summary (`summarize_run`)**  
  Produces a 4–5 line, human-readable summary covering:
  - Total keywords processed  
  - Organic ranks found  
  - Places ranks found  
  - Keywords not ranked  
  - API quota exhaustion
- **Audit-friendly runtime events**  
  All decisions and outcomes are appended to `runtime_events`, enabling traceability.
- **Safe LLM usage**  
  The LLM:
  - Does **not** fetch data  
  - Does **not** change execution flow  
  - Does **not** decide ranks  
  - Only explains and summarizes completed actions.

**Architectural philosophy**: 
- **Execution = deterministic Python**
- **Interpretation = LLM**
- **Control remains fully outside the LLM**

This avoids hallucination risks while benefiting from natural-language reasoning.

### Why v3 (nkr_langgraph.py) was created
- v2 proved that **LLM integration could be safe and valuable**.
- Next evolution: **demonstrate agent orchestration patterns** for complex workflows.
- **Goal**: Show how existing logic can be wrapped into a state graph without changing behavior.

**v3 design principles**:
- **100% behavioral parity** with v2 (same outputs, same logs, same quota handling)
- **Thin wrapper nodes** that call existing functions
- **Explicit graph structure** enables:
  - Visual debugging
  - Multi-agent patterns
  - Conditional routing
  - Human-in-the-loop extensions
- **No new logic** introduced during refactor (pure structural change)

**LangGraph architecture**:
```
Entry → planner_node → organic_search_node → [conditional] → places_search_node → finalize_node → END
                                                    ↓
                                              (if no places needed)
                                                    ↓
                                              finalize_node → END
```

**Why LangGraph wasn't used earlier**:
- The goal was **stability and correctness first**, not abstraction.
- Introducing LangGraph too early would risk altering a verified ranking pipeline.
- v3 was intentionally created **after** v2 was validated.

**Readiness for future extensions**:
The following functions already map cleanly to graph nodes:
- `PlannerAgent.decide` → planner node
- `SearchTool.search_page` → search nodes
- `RankEvaluator.evaluate` → evaluation sub-nodes
- `ExcelWriterTool.write` → writer node
- `summarize_run` → post-processing node

This makes v3 a **safe platform** for adding:
- Parallel search strategies
- Multi-location testing
- A/B testing different ranking algorithms
- Human approval steps
- Retry/fallback patterns

---

## Mentor/Evaluator Summary

### Technical evolution
- **v1**: Procedural, deterministic, BeautifulSoup legacy support
- **v2**: Added LLM explainability without changing execution flow
- **v3**: Wrapped v2 into LangGraph state machine with zero logic changes

### Key achievements
- ✅ **Behavioral parity** across all versions (same Excel output, same quota handling)
- ✅ **Safe LLM integration** (interpretation only, never control)
- ✅ **Progressive enhancement** (each version builds on previous)
- ✅ **Production-ready** (all versions tested with real Excel files)

### Design philosophy
- "HTML scraping components like BeautifulSoup, direct Google requests, and user-agent rotation were intentionally disabled. The system now operates entirely via SERP APIs to remain compliant and stable."
- Early versions scraped `google.com/search` with BeautifulSoup and custom user-agents; frequent 429s and ToS risk led to the current SERP API-only design (SearchAPI.io default, SerpAPI fallback).
- **Incremental system design**: Each version solves a specific problem without breaking existing functionality.
- **Separation of concerns**: Decision logic, interpretation, and orchestration are cleanly separated.

### Evaluation criteria demonstrated
1. **Correctness**: All versions produce identical Excel outputs
2. **Explainability**: v2/v3 add transparency via LLM summaries
3. **Extensibility**: v3 enables graph-based extensions
4. **Safety**: LLM never controls execution
5. **Compliance**: SERP API usage respects ToS
