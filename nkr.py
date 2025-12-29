import datetime
import os
import random
import time
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from openpyxl import load_workbook


# User agents for lightweight rotation (desktop only to reduce variance)
DESKTOP_AGENTS = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/110.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:105.0) Gecko/20100101 Firefox/105.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:15.0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/15.0 Safari/605.1.15',
]


GOOGLE_SEARCH_URL = 'https://www.google.com/search'
MAX_PAGES = 5  # first 5 pages => up to 50 results
MAX_RETRIES = 3
LOCAL_KEYWORDS = ['near me', 'in ', 'nearby', 'close to']  # simple local-intent hints
DEFAULT_GEO_PARAMS: Dict[str, str] = {}
DEFAULT_LOCATION = "Noida, India"  # configurable location for local intent
SEARCH_PROVIDER = "searchapi"  # options: searchapi, serpapi (fallback)
SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"


def load_env_key(key: str, env_path: str = '.env') -> Optional[str]:
    """Lightweight .env reader to fetch a single key without extra deps."""
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


def clean_url(raw: Optional[str]) -> Optional[str]:
    """Return a normalized URL stripped of Google's redirect params."""
    if not raw:
        return None
    start = raw.find('https://')
    if start == -1:
        return None
    end = raw.find('&ved', start)
    end = end if end != -1 else len(raw)
    return raw[start:end]


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
        # strip common prefixes
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


def parse_organic(soup: BeautifulSoup) -> List[str]:
    anchors = soup.select('div.yuRUbf a') or []
    hrefs = [clean_url(a.get('href')) for a in anchors]
    return unique_preserve_order(hrefs)


def parse_places(soup: BeautifulSoup) -> List[str]:
    # Places / Maps results often surface through map/place links
    anchors = soup.find_all('a')
    hrefs: List[str] = []
    for a in anchors:
        href = a.get('href')
        if not href:
            continue
        if 'google.com/maps/place' in href or 'ludocid=' in href or 'maps/place' in href:
            hrefs.append(clean_url(href))
    return unique_preserve_order(hrefs)


def fetch_results(
    keyword: str,
    page: int,
    parser: Callable[[BeautifulSoup], List[str]],
    extra_params: Optional[dict] = None,
    geo_params: Optional[Dict[str, str]] = None,
) -> List[str]:
    params = {'q': keyword, 'num': 10, 'start': page * 10}
    if extra_params:
        params.update(extra_params)
    if geo_params:
        params.update(geo_params)

    headers = {'User-Agent': random.choice(DESKTOP_AGENTS)}
    print(f"Requesting {params} with UA {headers['User-Agent']}")

    try:
        response = requests.get(GOOGLE_SEARCH_URL, params=params, headers=headers, timeout=10)
    except requests.RequestException as exc:
        print(f"Request error for {keyword} page {page + 1}: {exc}")
        return []

    if response.status_code == 429:
        print(f"Non-200 status 429 for {keyword} page {page + 1}")
        return ['__HTTP_429__']  # signal for backoff
    if response.status_code != 200:
        print(f"Non-200 status {response.status_code} for {keyword} page {page + 1}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    return parser(soup)


def find_rank(domain: str, urls: List[str], page: int) -> Optional[int]:
    target = normalize_domain(domain)
    normed = [normalize_domain(u) for u in urls]
    print(f"[Debug] target domain={target} | first result domains={normed[:3]}")
    for idx, dom in enumerate(normed, start=1):
        if dom and target and dom == target:
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
        # prefer SearchAPI.io key when using searchapi provider, otherwise SerpAPI
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
        parser: Callable[[BeautifulSoup], List[str]],
        extra_params=None,
        geo_params=None,
    ) -> List[str]:
        # Determine search mode by parser reference
        search_type = 'places' if parser == parse_places else 'organic'

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
            # SearchAPI format: organic_results -> results -> url
            organic = payload.get('organic_results', {})
            if isinstance(organic, dict):
                for item in organic.get('results', []):
                    link = item.get('url') or item.get('link')
                    if link:
                        urls.append(link)
            # SerpAPI fallback format: organic_results -> list
            if isinstance(organic, list):
                for item in organic:
                    link = item.get('link') or item.get('redirect_link')
                    if link:
                        urls.append(link)

        else:  # places / local
            # Existing SerpAPI-style locals
            for item in payload.get('local_results', []):
                link = item.get('link') or item.get('website')
                if link:
                    urls.append(link)
            local_map = payload.get('local_map', {})
            for item in local_map.get('results', []):
                link = item.get('link') or item.get('website')
                if link:
                    urls.append(link)
            # SearchAPI potential structure (if provided)
            local_dict = payload.get('local_results', {}) if isinstance(payload.get('local_results'), dict) else {}
            for item in local_dict.get('results', []):
                link = item.get('url') or item.get('website')
                if link:
                    urls.append(link)

        ordered = unique_preserve_order(urls)
        print(f"[Debug] first URLs: {ordered[:3]}")
        return ordered


class ParserTool:
    def parse_organic(self, soup: BeautifulSoup) -> List[str]:
        return parse_organic(soup)

    def parse_places(self, soup: BeautifulSoup) -> List[str]:
        return parse_places(soup)


class RankEvaluator:
    def evaluate(self, domain: str, urls: List[str], page: int) -> Optional[int]:
        return find_rank(domain, urls, page)


class ExcelWriterTool:
    def write(self, ws, row_idx: int, places_col: int, links_col: int, places_rank: Optional[int], organic_rank: Optional[int], status: str) -> None:
        ws.cell(row=row_idx, column=places_col).value = places_rank if places_rank else status
        ws.cell(row=row_idx, column=links_col).value = organic_rank if organic_rank else status


def exponential_backoff(retry: int) -> None:
    delay_minutes = 2 ** retry
    delay_seconds = delay_minutes * 60
    print(f"[Agent] Backing off for {delay_minutes} minutes due to 429 (retry {retry + 1}/{MAX_RETRIES})")
    time.sleep(delay_seconds)


def is_429_signal(urls: List[str]) -> bool:
    return len(urls) == 1 and urls[0] == '__HTTP_429__'


def is_quota_signal(urls: List[str]) -> bool:
    return len(urls) == 1 and urls[0] == '__API_QUOTA__'


def process_keyword(keyword: str, target_url: str, geo_params: Optional[Dict[str, str]] = None) -> Tuple[Optional[int], Optional[int], str]:
    domain = extract_and_normalize_domain(target_url) or target_url
    state = AgentState(keyword=keyword, domain=domain)

    planner = PlannerAgent()
    searcher = SearchTool()
    evaluator = RankEvaluator()

    plan = planner.decide(keyword)
    status = 'Not in top 50'

    # local intent location handling
    if plan['local_intent'] and DEFAULT_LOCATION:
        geo_params = {**(geo_params or {}), 'location': DEFAULT_LOCATION}

    # Organic search loop
    if plan['do_organic']:
        for page in range(MAX_PAGES):
            print(f"[Tool] {SEARCH_PROVIDER} search invoked (organic, page {page + 1})")
            urls = searcher.search_page(keyword, page, parse_organic, geo_params=geo_params)
            if is_quota_signal(urls):
                status = 'Blocked – API quota exceeded'
                break
            if is_429_signal(urls):
                if state.retries_used < MAX_RETRIES:
                    exponential_backoff(state.retries_used)
                    state.retries_used += 1
                    continue
                status = 'Blocked – retry later'
                break
            state.organic_rank = state.organic_rank or evaluator.evaluate(domain, urls, page)
            state.pages_checked += 1
            if state.organic_rank:
                print(f"[Agent] Observation: Organic rank {state.organic_rank} (page {page + 1})")
                status = 'Success'
                break
            time.sleep(random.uniform(1.5, 3.0))

    # Places search loop
    if plan['do_places'] and status not in ('Blocked – retry later', 'Blocked – API quota exceeded'):
        for page in range(MAX_PAGES):
            print(f"[Tool] {SEARCH_PROVIDER} search invoked (places, page {page + 1})")
            urls = searcher.search_page(keyword, page, parse_places, extra_params={'tbm': 'lcl'}, geo_params=geo_params)
            if is_quota_signal(urls):
                status = 'Blocked – API quota exceeded'
                break
            if is_429_signal(urls):
                if state.retries_used < MAX_RETRIES:
                    exponential_backoff(state.retries_used)
                    state.retries_used += 1
                    continue
                status = 'Blocked – retry later'
                break
            state.places_rank = state.places_rank or evaluator.evaluate(domain, urls, page)
            state.pages_checked += 1
            if state.places_rank:
                print(f"[Agent] Observation: Places rank {state.places_rank} (page {page + 1})")
                status = 'Success'
                break
            # If Places are visible but no external website URLs are provided by API, mark accordingly
            if page == MAX_PAGES - 1 and not urls:
                status = 'Places visible but website unavailable via API'
                break
            time.sleep(random.uniform(1.5, 3.0))

    # Final status
    if status != 'Blocked – retry later' and not (state.organic_rank or state.places_rank):
        status = 'Not in top 50'

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
    # Fallback to default positions if headers are missing
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
        # Agentic decision-making per keyword
        places_rank, organic_rank, status = process_keyword(str(keyword), str(target_url), geo_params=geo_params)

        writer = ExcelWriterTool()
        writer.write(ws, row_idx, places_col, links_col, places_rank, organic_rank, status)

    wb.save(output_path)
    print(f"Updated workbook saved to {output_path}")


if __name__ == '__main__':
    INPUT_FILE = 'Keyword_Ranking.xlsx'
    OUTPUT_FILE = 'Keyword_Ranking_updated.xlsx'

    print(f"Reading {INPUT_FILE} and writing results to {OUTPUT_FILE}")
    print("Note: This agent is a low-volume diagnostic helper. Google SERP access may be rate-limited; adhere to Google ToS.")
    update_workbook(INPUT_FILE, OUTPUT_FILE)

              

    