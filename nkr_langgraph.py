import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from serpapi import GoogleSearch
from openai import OpenAI
from openpyxl import load_workbook
from openpyxl.styles import Font, Alignment
from langgraph.graph import StateGraph, END


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
DEFAULT_LOCATION = "Noida, India"  # STRICT: All searches limited to Noida, India only
DEFAULT_GEO_PARAMS: Dict[str, str] = {
    'location': 'Noida, Uttar Pradesh, India',
    'gl': 'in',  # Geolocation: India
    'hl': 'en',  # Language: English
    'google_domain': 'google.co.in'  # STRICT: Indian Google domain only
}
SEARCH_PROVIDER = "serpapi"  # options: searchapi, serpapi
# SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"  # Commented out - using serpapi instead
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
            # FIX: Places rank is position in local pack (1-20), not fake SERP math
            # OLD (WRONG): return page * 10 + idx  # This gave misleading ranks like 21, 31, etc.
            return idx  # CORRECT: Just the position in the local results list
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
    # LangGraph orchestration fields
    plan: Dict[str, bool] = field(default_factory=dict)
    geo_params: Optional[Dict[str, str]] = None
    searcher: Optional[Any] = None
    evaluator: Optional[Any] = None


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
    Search tool backed by SerpAPI. Uses official serpapi Python client.
    Returns ordered URL list; preserves page offset (10 results per page).
    """

    def __init__(self) -> None:
        # SearchAPI implementation (commented out)
        # if SEARCH_PROVIDER == "searchapi":
        #     self.api_key = load_env_key("SEARCHAPI_KEY") or load_env_key("SERPAPI_KEY")
        #     self.endpoint = SEARCHAPI_ENDPOINT
        # else:
        #     self.api_key = load_env_key("SERPAPI_KEY")
        #     self.endpoint = 'https://serpapi.com/search'
        
        # SerpAPI implementation (active)
        self.api_key = load_env_key("SERPAPI_KEY")
        if not self.api_key:
            raise RuntimeError("API key missing (SERPAPI_KEY). Please set it in your .env file before running.")

    def search_page(
        self,
        keyword: str,
        page: int,
        search_type: str,
        extra_params=None,
        geo_params=None,
    ) -> Any:

        # FIX: Places pagination uses multiples of 20, organic uses multiples of 10
        if search_type == 'places':
            start_value = page * 20  # Places: 0, 20, 40, 60...
        else:
            start_value = page * 10  # Organic: 0, 10, 20, 30...

        params = {
            'engine': 'google',
            'q': keyword,
            'api_key': self.api_key,
            'start': start_value,  # Corrected pagination
            'num': 10,
        }

        # STRICT RULE: Always use Noida, India location - ignore any passed geo_params
        strict_geo_params = {
            'location': 'Noida, Uttar Pradesh, India',
            'gl': 'in',  # Geolocation: India only
            'hl': 'en',  # Language: English
            'google_domain': 'google.co.in'  # STRICT: Indian Google domain only
        }
        params.update(strict_geo_params)

        if extra_params:
            params.update(extra_params)

        log_keys = ['q', 'start', 'location', 'gl', 'google_domain']
        log_view = {k: params[k] for k in log_keys if k in params}
        print(f"[Tool] SerpAPI search invoked ({search_type}, page {page + 1}) [STRICT: Noida, India on google.co.in] with params={log_view}")

        # SearchAPI HTTP request implementation (commented out)
        # try:
        #     response = requests.get(self.endpoint, params=params, timeout=15)
        # except requests.RequestException as exc:
        #     print(f"Request error via {SEARCH_PROVIDER} for {keyword} page {page + 1}: {exc}")
        #     return []
        #
        # if response.status_code == 429:
        #     print(f"{SEARCH_PROVIDER} quota/429 for {keyword} page {page + 1}")
        #     return ['__API_QUOTA__']
        #
        # try:
        #     payload = response.json()
        # except ValueError:
        #     print(f"Non-JSON response from {SEARCH_PROVIDER} for {keyword} page {page + 1}")
        #     return []
        
        # SerpAPI client implementation (active)
        try:
            search = GoogleSearch(params)
            payload = search.get_dict()
        except Exception as exc:
            error_msg = str(exc).lower()
            print(f"Request error via SerpAPI for {keyword} page {page + 1}: {exc}")
            
            # Check for quota/rate limit errors
            if 'rate limit' in error_msg or '429' in error_msg or 'quota' in error_msg:
                print(f"SerpAPI quota/429 for {keyword} page {page + 1}")
                return ['__API_QUOTA__']
            return []

        if 'error' in payload:
            msg = str(payload['error']).lower()
            print(f"SerpAPI error: {payload['error']}")
            if 'over your plan' in msg or 'insufficient credits' in msg or 'limit reached' in msg or 'invalid api key' in msg:
                return ['__API_QUOTA__']
            return []

        urls: List[str] = []

        if search_type == 'organic':
            organic = payload.get('organic_results', [])
            if isinstance(organic, list):
                for item in organic:
                    link = item.get('link') or item.get('url')
                    if link:
                        urls.append(link)
            elif isinstance(organic, dict):
                for item in organic.get('results', []):
                    link = item.get('url') or item.get('link')
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
    def write(
            self, 
            ws, 
            row_idx: int,
            places_col: int,
            links_col: int,
            places_rank: Optional[int],
            organic_rank: Optional[int]
            ) -> None:
        #Google Places cell
        places_cell = ws.cell(row=row_idx, column=places_col)
        places_cell.value = (
            f"Visible at {places_rank}" if places_rank else "Not visible"
        )
        places_cell.font = Font(name="Century Gothic", size =10)
        places_cell.alignment = Alignment(horizontal="center", vertical="center")

        #Google Links cell
        links_cell = ws.cell(row=row_idx, column=links_col)
        links_cell.value = (
            organic_rank if organic_rank else "Not in top 50"
        )
        links_cell.font = Font(name="Century Gothic", size =10)
        links_cell.alignment = Alignment(horizontal="center", vertical="center")
        
def exponential_backoff(retry: int) -> None:
    delay_minutes = 2 ** retry
    delay_seconds = delay_minutes * 60
    print(f"[Agent] Backing off for {delay_minutes} minutes due to 429 (retry {retry + 1}/{MAX_RETRIES})")
    time.sleep(delay_seconds)


def is_quota_signal(urls: List[str]) -> bool:
    return len(urls) == 1 and urls[0] == '__API_QUOTA__'


# LangGraph Node Functions
def planner_node(state: AgentState) -> AgentState:
    """Node: calls PlannerAgent.decide and explain_plan"""
    planner = PlannerAgent()
    plan = planner.decide(state.keyword)
    state.plan = plan
    runtime_events.append(
        f"PLAN keyword='{state.keyword}' do_places={plan['do_places']} do_organic={plan['do_organic']} local_intent={plan['local_intent']}"
    )
    explain_plan(state.keyword, plan)
    return state


def organic_search_node(state: AgentState) -> AgentState:
    """Node: executes the existing organic search loop"""
    global API_QUOTA_EXHAUSTED
    
    plan = state.plan
    if not plan.get('do_organic'):
        return state
    
    searcher = state.searcher
    evaluator = state.evaluator
    geo_params = state.geo_params
    
    for page in range(MAX_PAGES):
        urls = searcher.search_page(state.keyword, page, 'organic', geo_params=geo_params)
        if is_quota_signal(urls):
            runtime_events.append(f"API_QUOTA_HIT keyword='{state.keyword}' stage='organic' page={page + 1}")
            API_QUOTA_EXHAUSTED = True
            state.status = 'Blocked – API quota exhausted'
            runtime_events.append(f"GLOBAL_QUOTA_EXHAUSTED keyword='{state.keyword}'")
            break
        state.organic_rank = state.organic_rank or evaluator.evaluate(state.domain, urls, page)
        state.pages_checked += 1
        if state.organic_rank:
            runtime_events.append(f"ORGANIC_FOUND keyword='{state.keyword}' rank={state.organic_rank} page={page + 1}")
            print(f"[Agent] Observation: Organic rank {state.organic_rank} (page {page + 1})")
            state.status = 'Completed'
            break
        time.sleep(random.uniform(1.5, 3.0))
    
    return state


def places_search_node(state: AgentState) -> AgentState:
    """Node: executes the existing places search loop"""
    global API_QUOTA_EXHAUSTED
    
    plan = state.plan
    if not plan.get('do_places'):
        return state
    
    if state.status in ('Blocked – retry later', 'Blocked – API quota exceeded'):
        return state
    
    searcher = state.searcher
    geo_params = state.geo_params
    
    for page in range(MAX_PAGES):
        payload = searcher.search_page(state.keyword, page, 'places', extra_params={'tbm': 'lcl'}, geo_params=geo_params)
        if payload == ['__API_QUOTA__']:
            runtime_events.append(f"API_QUOTA_HIT keyword='{state.keyword}' stage='places' page={page + 1}")
            API_QUOTA_EXHAUSTED = True
            state.status = 'Blocked – API quota exhausted'
            runtime_events.append(f"GLOBAL_QUOTA_EXHAUSTED keyword='{state.keyword}'")
            break
        places_rank = find_places_rank(BUSINESS_NAME, payload, page)
        state.pages_checked += 1
        if places_rank:
            state.places_rank = places_rank
            runtime_events.append(f"PLACES_FOUND keyword='{state.keyword}' rank={state.places_rank} page={page + 1}")
            print(f"[Agent] Observation: Places rank {state.places_rank} (page {page + 1})")
            state.status = 'Completed'
            break
        time.sleep(random.uniform(1.5, 3.0))
    
    return state


def finalize_node(state: AgentState) -> AgentState:
    """Node: sets final status and logs completion"""
    if state.status != 'Blocked – retry later' and not (state.organic_rank or state.places_rank):
        state.status = 'Not in top 50'
        runtime_events.append(f"NOT_FOUND keyword='{state.keyword}'")
    
    runtime_events.append(
        f"COMPLETE keyword='{state.keyword}' organic={state.organic_rank} places={state.places_rank} status='{state.status}'"
    )
    
    print(
        "[Agent] Summary | "
        f"Keyword: {state.keyword} | Organic: {state.organic_rank} | Places: {state.places_rank} | "
        f"Pages checked: {state.pages_checked} | Retries: {state.retries_used} | Status: {state.status}"
    )
    
    return state


def should_go_to_places(state: AgentState) -> str:
    """Conditional edge: determine if we should search places or finalize"""
    # If plan includes places search and not blocked, go to places
    if state.plan.get('do_places') and state.status not in ('Blocked – retry later', 'Blocked – API quota exceeded'):
        return "places"
    # Otherwise finalize
    return "finalize"


def build_keyword_graph() -> StateGraph:
    """Build the LangGraph StateGraph for keyword ranking orchestration"""
    workflow = StateGraph(AgentState)
    
    # Add nodes
    workflow.add_node("planner", planner_node)
    workflow.add_node("organic", organic_search_node)
    workflow.add_node("places", places_search_node)
    workflow.add_node("finalize", finalize_node)
    
    # Add edges
    workflow.set_entry_point("planner")
    workflow.add_edge("planner", "organic")
    workflow.add_conditional_edges(
        "organic",
        should_go_to_places,
        {
            "places": "places",
            "finalize": "finalize"
        }
    )
    workflow.add_edge("places", "finalize")
    workflow.add_edge("finalize", END)
    
    return workflow.compile()


def process_keyword(keyword: str, target_url: str, geo_params: Optional[Dict[str, str]] = None) -> Tuple[Optional[int], Optional[int], str]:
    global API_QUOTA_EXHAUSTED

    if API_QUOTA_EXHAUSTED:
        runtime_events.append(
            f"SKIPPED keyword='{keyword}' reason='API quota exhausted'"
        )
        return None, None, 'Skipped – API quota exhausted'

    domain = extract_and_normalize_domain(target_url) or target_url
    runtime_events.append(f"START keyword='{keyword}' domain='{domain}'")

    # Initialize tools
    searcher = SearchTool()
    evaluator = RankEvaluator()
    # STRICT: Always use Noida, India geo params (ignore any passed params)
    geo_params = DEFAULT_GEO_PARAMS.copy()

    # Create initial state
    state = AgentState(
        keyword=keyword,
        domain=domain,
        geo_params=geo_params,
        searcher=searcher,
        evaluator=evaluator,
        status='In progress'
    )

    # Build and invoke LangGraph
    graph = build_keyword_graph()
    final_state = graph.invoke(state)

    return (
    final_state.get("places_rank"),
    final_state.get("organic_rank"),
    final_state.get("status"),
    )



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

        # STRICT: All keywords search in Noida, India on google.co.in only
        geo_params = DEFAULT_GEO_PARAMS.copy()
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
