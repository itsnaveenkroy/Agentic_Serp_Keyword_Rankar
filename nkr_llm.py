import os
import random
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from openai import OpenAI
from openpyxl import load_workbook


BUSINESS_NAME = "omkitchen"

MAX_PAGES = 5  # first 5 pages => up to 50 results
MAX_RETRIES = 3
LOCAL_KEYWORDS = [
    'near me',
    'in ',
    'nearby',
    'close to',
    'tiffin',
    'food delivery',
    'meal delivery'
]  # simple local-intent hints
DEFAULT_LOCATION = "Noida, India"  # configurable location for local intent
DEFAULT_GEO_PARAMS: Dict[str, str] = {'location': DEFAULT_LOCATION}
SEARCH_PROVIDER = "searchapi"  # options: searchapi, serpapi (fallback)
SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"
MODEL = "meta-llama/llama-3-8b-instruct"
API_QUOTA_EXHAUSTED = False
# Runtime event log for agentic reporting only
runtime_events: List[str] = []


# Lightweight .env reader to fetch a single key without extra deps
def load_env_key(key: str, env_path: str = '.env') -> Optional[str]:
    current = os.getenv(key)
    if current:
        return current
    if not os.path.exists(env_path):
        return None
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' not in line:
                    continue
                k, v = line.split('=', 1)
                if k.strip() == key:
                    os.environ[key] = v.strip()
                    return v.strip()
    except OSError:
        return None
    return None


# De-dupe while preserving order
def unique_preserve_order(items: Iterable[Optional[str]]) -> List[str]:
    seen = set()
    ordered: List[str] = []
    for item in items:
        if not item:
            continue
        if item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def normalize_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        parsed = urlparse(url if '://' in url else 'https://' + url)
        host = parsed.hostname or ''
        for prefix in ('www.', 'm.'):
            if host.startswith(prefix):
                host = host[len(prefix):]
        return host.lower()
    except Exception:
        return None


def extract_and_normalize_domain(target_url: str) -> Optional[str]:
    if not target_url:
        return None
    candidate = target_url.strip()
    parsed = urlparse(candidate if '://' in candidate else 'https://' + candidate)
    host = parsed.hostname
    if not host and parsed.path:
        host = parsed.path.split('/', 1)[0]
    return normalize_domain(host)


def find_rank(domain: str, urls: List[str], page: int) -> Optional[int]:
    target = normalize_domain(domain)
    normed = [normalize_domain(u) for u in urls]
    print(f"[Debug] target domain={target} | first result domains={normed[:3]}")
    for idx, dom in enumerate(normed, start=1):
        if dom and target and dom == target:
            return page * 10 + idx
    return None


def find_places_rank(business_name: str, payload: dict, page: int) -> Optional[int]:
    target = business_name.lower()
    results = payload.get("local_results", []) if isinstance(payload, dict) else []
    for idx, item in enumerate(results, start=1):
        title_raw = item.get("title") or ""
        title = title_raw.lower()
        print(f"[Debug] places title check: '{title_raw}'")
        if target in title:
            return page * 10 + idx
    return None


@dataclass
class AgentState:
    keyword: str
    domain: str
    organic_rank: Optional[int] = None
    places_rank: Optional[int] = None
    pages_checked: int = 0
    retries_used: int = 0
    status: str = 'In progress'


class PlannerAgent:
    def decide(self, keyword: str) -> Dict[str, bool]:
        lowered = keyword.lower()
        local_intent = any(hint in lowered for hint in LOCAL_KEYWORDS)
        plan = {
            'do_places': local_intent,
            'do_organic': True,
            'local_intent': local_intent,
        }
        print(f"[Agent] Planning search for keyword: '{keyword}' | local_intent={plan['local_intent']}")
        return plan


class SearchTool:
    """
    Search tool backed by SearchAPI.io (default) or SerpAPI fallback. Replaces direct scraping.
    Returns ordered URL list; preserves page offset (10 results per page).
    """

    def __init__(self) -> None:
        if SEARCH_PROVIDER == "searchapi":
            self.api_key = load_env_key("SEARCHAPI_KEY") or load_env_key("SERPAPI_KEY")
            self.endpoint = SEARCHAPI_ENDPOINT
        else:
            self.api_key = load_env_key("SERPAPI_KEY")
            self.endpoint = 'https://serpapi.com/search'

        if not self.api_key:
            raise RuntimeError("API key missing (SEARCHAPI_KEY / SERPAPI_KEY). Please set it before running.")

    def search_page(
        self,
        keyword: str,
        page: int,
        search_type: str,
        extra_params=None,
        geo_params=None,
    ) -> Any:

        params = {
            'engine': 'google',
            'q': keyword,
            'api_key': self.api_key,
            'start': page * 10,
            'num': 10,
        }

        if extra_params:
            params.update(extra_params)
        if geo_params:
            params.update(geo_params)

        log_keys = ['q', 'start', 'location']
        log_view = {k: params[k] for k in log_keys if k in params}
        print(f"[Tool] {SEARCH_PROVIDER} search invoked ({search_type}, page {page + 1}) with params={log_view}")

        try:
            response = requests.get(self.endpoint, params=params, timeout=15)
        except requests.RequestException as exc:
            print(f"Request error via {SEARCH_PROVIDER} for {keyword} page {page + 1}: {exc}")
            return []

        if response.status_code == 429:
            print(f"{SEARCH_PROVIDER} quota/429 for {keyword} page {page + 1}")
            return ['__API_QUOTA__']

        try:
            payload = response.json()
        except ValueError:
            print(f"Non-JSON response from {SEARCH_PROVIDER} for {keyword} page {page + 1}")
            return []

        if 'error' in payload:
            msg = str(payload['error']).lower()
            print(f"{SEARCH_PROVIDER} error: {payload['error']}")
            if 'over your plan' in msg or 'insufficient credits' in msg or 'limit reached' in msg or 'invalid api key' in msg:
                return ['__API_QUOTA__']
            return []

        urls: List[str] = []

        if search_type == 'organic':
            organic = payload.get('organic_results', {})
            if isinstance(organic, dict):
                for item in organic.get('results', []):
                    link = item.get('url') or item.get('link')
                    if link:
                        urls.append(link)
            if isinstance(organic, list):
                for item in organic:
                    link = item.get('link') or item.get('redirect_link')
                    if link:
                        urls.append(link)

            ordered = unique_preserve_order(urls)
            print(f"[Debug] first URLs: {ordered[:3]}")
            return ordered

        print(f"[Debug] returning places payload keys: {list(payload.keys()) if isinstance(payload, dict) else 'non-dict'}")
        return payload


class RankEvaluator:
    def evaluate(self, domain: str, urls: List[str], page: int) -> Optional[int]:
        return find_rank(domain, urls, page)


class ExcelWriterTool:
    def write(self, ws, row_idx: int, places_col: int, links_col: int, places_rank: Optional[int], organic_rank: Optional[int]) -> None:
        ws.cell(row=row_idx, column=places_col).value = (
            f"Visible at {places_rank}" if places_rank else "Not visible"
        )

        ws.cell(row=row_idx, column=links_col).value = (
            organic_rank if organic_rank else "Not in top 50"
        )


def exponential_backoff(retry: int) -> None:
    delay_minutes = 2 ** retry
    delay_seconds = delay_minutes * 60
    print(f"[Agent] Backing off for {delay_minutes} minutes due to 429 (retry {retry + 1}/{MAX_RETRIES})")
    time.sleep(delay_seconds)


def is_quota_signal(urls: List[str]) -> bool:
    return len(urls) == 1 and urls[0] == '__API_QUOTA__'


def process_keyword(keyword: str, target_url: str, geo_params: Optional[Dict[str, str]] = None) -> Tuple[Optional[int], Optional[int], str]:
    global API_QUOTA_EXHAUSTED

    if API_QUOTA_EXHAUSTED:
        runtime_events.append(
            f"SKIPPED keyword='{keyword}' reason='API quota exhausted'"
        )
        return None, None, 'Skipped – API quota exhausted'

    domain = extract_and_normalize_domain(target_url) or target_url
    state = AgentState(keyword=keyword, domain=domain)
    runtime_events.append(f"START keyword='{keyword}' domain='{domain}'")

    planner = PlannerAgent()
    searcher = SearchTool()
    evaluator = RankEvaluator()

    plan = planner.decide(keyword)
    runtime_events.append(
        f"PLAN keyword='{keyword}' do_places={plan['do_places']} do_organic={plan['do_organic']} local_intent={plan['local_intent']}"
    )
    explain_plan(keyword, plan)
    status = 'Not in top 50'
    geo_params = {'location': DEFAULT_LOCATION}

    if plan['do_organic']:
        for page in range(MAX_PAGES):
            print(f"[Tool] {SEARCH_PROVIDER} search invoked (organic, page {page + 1})")
            urls = searcher.search_page(keyword, page, 'organic', geo_params=geo_params)
            if is_quota_signal(urls):
                runtime_events.append(f"API_QUOTA_HIT keyword='{keyword}' stage='organic' page={page + 1}")
                API_QUOTA_EXHAUSTED = True
                status = 'Blocked – API quota exhausted'
                runtime_events.append(f"GLOBAL_QUOTA_EXHAUSTED keyword='{keyword}'")
                break
            state.organic_rank = state.organic_rank or evaluator.evaluate(domain, urls, page)
            state.pages_checked += 1
            if state.organic_rank:
                runtime_events.append(f"ORGANIC_FOUND keyword='{keyword}' rank={state.organic_rank} page={page + 1}")
                print(f"[Agent] Observation: Organic rank {state.organic_rank} (page {page + 1})")
                status = 'Completed'
                break
            time.sleep(random.uniform(1.5, 3.0))

    if plan['do_places'] and status not in ('Blocked – retry later', 'Blocked – API quota exceeded'):
        for page in range(MAX_PAGES):
            print(f"[Tool] {SEARCH_PROVIDER} search invoked (places, page {page + 1})")
            payload = searcher.search_page(keyword, page, 'places', extra_params={'tbm': 'lcl'}, geo_params=geo_params)
            if payload == ['__API_QUOTA__']:
                runtime_events.append(f"API_QUOTA_HIT keyword='{keyword}' stage='places' page={page + 1}")
                API_QUOTA_EXHAUSTED = True
                status = 'Blocked – API quota exhausted'
                runtime_events.append(f"GLOBAL_QUOTA_EXHAUSTED keyword='{keyword}'")
                break
            places_rank = find_places_rank(BUSINESS_NAME, payload, page)
            state.pages_checked += 1
            if places_rank:
                state.places_rank = places_rank
                runtime_events.append(f"PLACES_FOUND keyword='{keyword}' rank={state.places_rank} page={page + 1}")
                print(f"[Agent] Observation: Places rank {state.places_rank} (page {page + 1})")
                status = 'Completed'
                break
            time.sleep(random.uniform(1.5, 3.0))

    if status != 'Blocked – retry later' and not (state.organic_rank or state.places_rank):
        status = 'Not in top 50'
        runtime_events.append(f"NOT_FOUND keyword='{state.keyword}'")

    runtime_events.append(
        f"COMPLETE keyword='{state.keyword}' organic={state.organic_rank} places={state.places_rank} status='{status}'"
    )

    print(
        "[Agent] Summary | "
        f"Keyword: {state.keyword} | Organic: {state.organic_rank} | Places: {state.places_rank} | "
        f"Pages checked: {state.pages_checked} | Retries: {state.retries_used} | Status: {status}"
    )

    return state.places_rank, state.organic_rank, status


def locate_columns(ws) -> Tuple[int, int]:
    places_col = None
    links_col = None
    for cell in ws[2]:
        if isinstance(cell.value, str):
            value = cell.value.strip().lower()
            if value == 'google places':
                places_col = cell.col_idx
            elif value == 'google links':
                links_col = cell.col_idx
    places_col = places_col or 34
    links_col = links_col or 35
    return places_col, links_col


def update_workbook(input_path: str, output_path: str) -> None:
    wb = load_workbook(input_path)
    if 'Keywords' not in wb.sheetnames:
        raise ValueError("Sheet 'Keywords' not found in workbook")

    ws = wb['Keywords']
    places_col, links_col = locate_columns(ws)

    print(f"Writing Places to column {places_col} and Links to column {links_col}")

    for row_idx in range(3, ws.max_row + 1):
        keyword = ws.cell(row=row_idx, column=2).value
        target_url = ws.cell(row=row_idx, column=3).value

        if not keyword or not target_url:
            continue

        geo_params = DEFAULT_GEO_PARAMS
        places_rank, organic_rank, status = process_keyword(str(keyword), str(target_url), geo_params=geo_params)

        writer = ExcelWriterTool()
        writer.write(ws, row_idx, places_col, links_col, places_rank, organic_rank)

    wb.save(output_path)
    print(f"Updated workbook saved to {output_path}")


def summarize_run(events: List[str]) -> str:
    load_env_key("OPENROUTER_API_KEY")
    api_key = os.getenv("OPENROUTER_API_KEY")

    processed = sum(1 for e in events if e.startswith("START "))
    organic_found = sum(1 for e in events if e.startswith("ORGANIC_FOUND"))
    places_found = sum(1 for e in events if e.startswith("PLACES_FOUND"))
    not_found = sum(1 for e in events if e.startswith("NOT_FOUND"))
    skipped = sum(1 for e in events if e.startswith("SKIPPED"))
    quota_exhausted = any(e.startswith("GLOBAL_QUOTA_EXHAUSTED") for e in events)

    data_lines = [
        f"Total keywords processed: {processed}",
        f"Organic ranks found: {organic_found}",
        f"Places ranks found: {places_found}",
        f"Not in top 50: {not_found}",
        f"Skipped due to quota: {skipped}",
        f"API quota exhausted: {quota_exhausted}",
    ]

    if not api_key:
        return "LLM Summary: OPENROUTER_API_KEY not set. Execution completed without LLM summary."

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    prompt = (
    "Write a concise executive summary (4–5 lines) of the run using ONLY the aggregated metrics below. "
    "Focus on overall visibility outcomes and coverage quality. "
    "State how many keywords achieved organic visibility, how many appeared in Google Places, "
    "and how many did not rank within the top 50. "
    "If API quota exhaustion materially affected completion, "
    "explicitly name the keyword where the first 429 or quota exhaustion occurred, in one short factual line. "
    "Do not include recommendations, explanations, keyword lists, or speculation."
    )


    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": "You summarize aggregated metrics only; you do not control execution."},
                {"role": "user", "content": prompt + "\n\nAggregated metrics:\n" + "\n".join(data_lines)},
            ],
            max_tokens=250,
            temperature=0,
        )

        text = response.choices[0].message.content
        return text.strip() if text else "LLM Summary: Model returned empty response."

    except Exception as exc:
        return f"LLM Summary failed explicitly: {repr(exc)}"


def explain_plan(keyword: str, plan: Dict[str, bool]) -> None:
    load_env_key("OPENROUTER_API_KEY")
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        runtime_events.append(f"LLM_REASON keyword='{keyword}' explanation='LLM unavailable: missing OPENROUTER_API_KEY.'")
        return

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=api_key,
    )

    prompt = (
        "Explain in one short sentence why the planner chose organic search and/or places search. "
        "Base this only on the provided plan flags. Do not propose actions or modify execution."
    )

    plan_text = (
        f"do_organic={plan.get('do_organic')} "
        f"do_places={plan.get('do_places')} "
        f"local_intent={plan.get('local_intent')}"
    )

    messages = [
        {"role": "system", "content": "You only explain planner intent; you do not control tools or execution."},
        {"role": "user", "content": prompt + "\nPlan: " + plan_text},
    ]

    try:
        response = client.chat.completions.create(model=MODEL, messages=messages, max_tokens=80, temperature=0)
        explanation = (response.choices[0].message.content or "").strip()
    except Exception as exc:
        explanation = f"LLM explanation failed: {exc}"

    runtime_events.append(f"LLM_REASON keyword='{keyword}' explanation='{explanation}'")


if __name__ == '__main__':
    INPUT_FILE = 'Keyword_Ranking.xlsx'
    OUTPUT_FILE = 'Keyword_Ranking_updated.xlsx'

    print(f"Reading {INPUT_FILE} and writing results to {OUTPUT_FILE}")
    print("Note: This agent is a low-volume diagnostic helper. Google SERP access may be rate-limited; adhere to Google ToS.")
    update_workbook(INPUT_FILE, OUTPUT_FILE)

    summary_text = summarize_run(runtime_events)
    print("===== LLM EXECUTION SUMMARY =====")
    print(summary_text)
