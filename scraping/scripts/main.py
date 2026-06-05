from __future__ import annotations

import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse

import requests
from rdflib import BNode, Graph, Literal, Namespace, RDF, RDFS, OWL, URIRef, XSD
from rdflib.namespace import FOAF, SKOS

try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv() -> bool:
        return False

"""
Macedonian History Knowledge Graph - Data Collector
===================================================
Collects structured data from Wikidata for Macedonian history in a
configurable time window (default 1885-1910), then serializes RDF/Turtle.

Output files (./output):
  persons.ttl, events.ttl, places.ttl, organizations.ttl, documents.ttl
  mk_history_full.ttl

Usage:
  pip install rdflib requests
  python main.py

Optional environment variables:
  WIKIDATA_CONTACT_EMAIL=you@example.com
  HISTORY_START_YEAR=1885
  HISTORY_END_YEAR=1910
  PERSON_LIMIT=500
  EVENT_LIMIT=350
  PLACE_LIMIT=400
  ORG_LIMIT=250
  DOC_LIMIT=250
  WIKIPEDIA_ENRICH_LIMIT=20
  ENABLE_WIKIPEDIA_ENRICH=1
  SPARQL_TIMEOUT_SECONDS=90
  SPARQL_RETRIES=6
  YEAR_CHUNK_SIZE=1
  VALUES_CHUNK_SIZE=40
  EVENT_LIMIT_PER_WINDOW=120
  SCOPE_LOCATION_LIMIT=300
  SCOPE_LOCATION_CHUNK_SIZE=30
  EVENT_MIN_YEAR_WINDOW=1
  EVENT_REQUIRE_OCCURRENCE=0
  EVENT_DETAILS_CHUNK_SIZE=40
  WIKIDATA_SPARQL_ENDPOINTS=https://query.wikidata.org/sparql,https://query.wikidata.org/bigdata/namespace/wdq/sparql
  WIKIDATA_MIN_DELAY_SECONDS=3.0
  WIKIPEDIA_MIN_DELAY_SECONDS=1.5
"""

# Namespaces
MK = Namespace("http://macedonian-kg.mk/resource/")
MKO = Namespace("http://macedonian-kg.mk/ontology#")
WD = Namespace("http://www.wikidata.org/entity/")
TIME = Namespace("http://www.w3.org/2006/time#")
GEO = Namespace("http://www.opengis.net/ont/geosparql#")

load_dotenv()

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
WIKIDATA_SPARQL_ENDPOINTS = [
    endpoint.strip()
    for endpoint in os.getenv(
        "WIKIDATA_SPARQL_ENDPOINTS",
        "https://query.wikidata.org/sparql,https://query.wikidata.org/bigdata/namespace/wdq/sparql",
    ).split(",")
    if endpoint.strip()
]
CONTACT_EMAIL = os.getenv("WIKIDATA_CONTACT_EMAIL", "set-your-email@example.com").strip()
USER_AGENT = f"MacedonianHistoryKG/2.0 (university project; contact: {CONTACT_EMAIL})"

SPARQL_TIMEOUT_SECONDS = int(os.getenv("SPARQL_TIMEOUT_SECONDS", "90"))
SPARQL_RETRIES = int(os.getenv("SPARQL_RETRIES", "6"))
DEFAULT_TIMEOUT_SECONDS = int(os.getenv("DEFAULT_TIMEOUT_SECONDS", "30"))
WIKIDATA_MIN_DELAY_SECONDS = float(os.getenv("WIKIDATA_MIN_DELAY_SECONDS", "3.0"))
WIKIPEDIA_MIN_DELAY_SECONDS = float(os.getenv("WIKIPEDIA_MIN_DELAY_SECONDS", "1.5"))
MAX_BACKOFF_SECONDS = 120.0

YEAR_CHUNK_SIZE = int(os.getenv("YEAR_CHUNK_SIZE", "1"))
VALUES_CHUNK_SIZE = int(os.getenv("VALUES_CHUNK_SIZE", "40"))
EVENT_LIMIT_PER_WINDOW = int(os.getenv("EVENT_LIMIT_PER_WINDOW", "120"))
PERSON_LIMIT_PER_EVENT_CHUNK = int(os.getenv("PERSON_LIMIT_PER_EVENT_CHUNK", "300"))
ORG_LIMIT_PER_CHUNK = int(os.getenv("ORG_LIMIT_PER_CHUNK", "200"))
DOC_LIMIT_PER_CHUNK = int(os.getenv("DOC_LIMIT_PER_CHUNK", "200"))
SCOPE_LOCATION_LIMIT = int(os.getenv("SCOPE_LOCATION_LIMIT", "300"))
SCOPE_LOCATION_CHUNK_SIZE = int(os.getenv("SCOPE_LOCATION_CHUNK_SIZE", "30"))
EVENT_MIN_YEAR_WINDOW = int(os.getenv("EVENT_MIN_YEAR_WINDOW", "1"))
EVENT_REQUIRE_OCCURRENCE = os.getenv("EVENT_REQUIRE_OCCURRENCE", "0") == "1"
EVENT_DETAILS_CHUNK_SIZE = int(
    os.getenv("EVENT_DETAILS_CHUNK_SIZE", os.getenv("VALUES_CHUNK_SIZE", "40"))
)
EVENT_DETAILS_ENABLED = os.getenv("EVENT_DETAILS_ENABLED", "0") == "1"
EVENT_DETAILS_TIMEOUT_SECONDS = int(os.getenv("EVENT_DETAILS_TIMEOUT_SECONDS", "25"))
EVENT_DETAILS_RETRIES = int(os.getenv("EVENT_DETAILS_RETRIES", "1"))
PERSON_SCOPE_ENABLED = os.getenv("PERSON_SCOPE_ENABLED", "1") == "1"
PERSON_SCOPE_LIMIT_PER_CHUNK = int(os.getenv("PERSON_SCOPE_LIMIT_PER_CHUNK", "120"))
PERSON_SCOPE_INCLUDE_DEATH = os.getenv("PERSON_SCOPE_INCLUDE_DEATH", "0") == "1"
PERSON_SCOPE_ID_RETRIES = int(os.getenv("PERSON_SCOPE_ID_RETRIES", "4"))
PERSON_SCOPE_ID_TIMEOUT_SECONDS = int(os.getenv("PERSON_SCOPE_ID_TIMEOUT_SECONDS", "45"))
PERSON_SCOPE_DETAILS_RETRIES = int(os.getenv("PERSON_SCOPE_DETAILS_RETRIES", "2"))
PERSON_SCOPE_DETAILS_TIMEOUT_SECONDS = int(os.getenv("PERSON_SCOPE_DETAILS_TIMEOUT_SECONDS", "60"))
PERSON_SCOPE_DETAILS_CHUNK_SIZE = int(
    os.getenv("PERSON_SCOPE_DETAILS_CHUNK_SIZE", os.getenv("VALUES_CHUNK_SIZE", "40"))
)
EVENTS_FROM_PERSONS_ENABLED = os.getenv("EVENTS_FROM_PERSONS_ENABLED", "1") == "1"
EVENT_FROM_PERSON_LIMIT_PER_CHUNK = int(os.getenv("EVENT_FROM_PERSON_LIMIT_PER_CHUNK", "120"))

HISTORY_START_YEAR = int(os.getenv("HISTORY_START_YEAR", "1885"))
HISTORY_END_YEAR = int(os.getenv("HISTORY_END_YEAR", "1910"))
PERSON_SCOPE_BIRTH_FROM_YEAR = int(
    os.getenv("PERSON_SCOPE_BIRTH_FROM_YEAR", str(HISTORY_START_YEAR - 70))
)
PERSON_SCOPE_BIRTH_TO_YEAR = int(
    os.getenv("PERSON_SCOPE_BIRTH_TO_YEAR", str(HISTORY_END_YEAR + 5))
)
PERSON_SCOPE_DEATH_FROM_YEAR = int(
    os.getenv("PERSON_SCOPE_DEATH_FROM_YEAR", str(HISTORY_START_YEAR))
)
PERSON_SCOPE_DEATH_TO_YEAR = int(
    os.getenv("PERSON_SCOPE_DEATH_TO_YEAR", str(HISTORY_END_YEAR + 40))
)

MACEDONIA = "wd:Q221"
MACEDONIA_REGION = "wd:Q103251"
MACEDONIA_REGION_ANCIENT = "wd:Q83958"
SEED_ILINDEN_UPRISING = "wd:Q1145682"
SEED_KRUSEVO_REPUBLIC = "wd:Q1771831"

PERSON_LIMIT = int(os.getenv("PERSON_LIMIT", "500"))
EVENT_LIMIT = int(os.getenv("EVENT_LIMIT", "350"))
PLACE_LIMIT = int(os.getenv("PLACE_LIMIT", "400"))
ORG_LIMIT = int(os.getenv("ORG_LIMIT", "250"))
DOC_LIMIT = int(os.getenv("DOC_LIMIT", "250"))

WIKIPEDIA_ENRICH_LIMIT = int(os.getenv("WIKIPEDIA_ENRICH_LIMIT", "20"))
ENABLE_WIKIPEDIA_ENRICH = os.getenv("ENABLE_WIKIPEDIA_ENRICH", "1") == "1"

OUTPUT_DIR = "../output"
PERIOD_RESOURCE_ID = f"MacedonianHistory{HISTORY_START_YEAR}_{HISTORY_END_YEAR}"


class WikidataClient:
    """HTTP client with throttling and retry handling for 429/5xx responses."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/sparql-results+json, application/json;q=0.9",
                "Accept-Language": "mk,en;q=0.9",
            }
        )
        self.last_request_started_at = 0.0
        self.last_query_succeeded = True

    def _throttle(self, min_delay_seconds: float) -> None:
        if self.last_request_started_at <= 0:
            return
        elapsed = time.monotonic() - self.last_request_started_at
        if elapsed < min_delay_seconds:
            time.sleep(min_delay_seconds - elapsed)

    @staticmethod
    def _parse_retry_after_seconds(value: str | None) -> float:
        if not value:
            return 0.0

        raw = value.strip()
        if raw.isdigit():
            return max(0.0, float(raw))

        try:
            dt = parsedate_to_datetime(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0.0

    def _compute_backoff_seconds(self, attempt: int, retry_after: str | None) -> float:
        retry_after_seconds = self._parse_retry_after_seconds(retry_after)
        if retry_after_seconds > 0:
            return min(retry_after_seconds, MAX_BACKOFF_SECONDS)

        base_wait = min((2**attempt), MAX_BACKOFF_SECONDS)
        jitter = random.uniform(0.0, 1.0)
        return min(base_wait + jitter, MAX_BACKOFF_SECONDS)

    def get_json(
        self,
        url: str,
        *,
        method: str = "GET",
        params: dict | None = None,
        data: dict | None = None,
        retries: int = 6,
        min_delay_seconds: float = 1.0,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        context: str = "request",
    ) -> dict | None:
        saw_429 = False
        for attempt in range(1, retries + 1):
            self._throttle(min_delay_seconds)
            self.last_request_started_at = time.monotonic()

            try:
                if method.upper() == "POST":
                    response = self.session.post(url, params=params, data=data, timeout=timeout_seconds)
                else:
                    response = self.session.get(url, params=params, timeout=timeout_seconds)
            except requests.RequestException as exc:
                wait = self._compute_backoff_seconds(attempt, None)
                print(f"  x {context} network error ({exc}) - retry {attempt}/{retries} in {wait:.1f}s")
                time.sleep(wait)
                continue

            if response.status_code in (429, 502, 503, 504):
                if response.status_code == 429:
                    saw_429 = True
                wait = self._compute_backoff_seconds(attempt, response.headers.get("Retry-After"))
                print(f"  x {context} HTTP {response.status_code} - retry {attempt}/{retries} in {wait:.1f}s")
                time.sleep(wait)
                continue

            try:
                response.raise_for_status()
            except requests.HTTPError as exc:
                print(f"  x {context} failed with HTTP {response.status_code}: {exc}")
                return None

            try:
                return response.json()
            except ValueError:
                print(f"  x {context} returned non-JSON payload")
                return None

        print(f"  x {context} failed after {retries} attempts")
        if saw_429:
            print("  x Hint: endpoint is rate-limiting this IP/session (HTTP 429). Wait and retry later.")
        return None

    def query(
        self,
        sparql: str,
        retries: int = SPARQL_RETRIES,
        timeout_seconds: int | None = None,
    ) -> list[dict]:
        endpoints = WIKIDATA_SPARQL_ENDPOINTS or [WIKIDATA_SPARQL]
        payload: dict | None = None
        per_endpoint_base = max(1, retries // len(endpoints))
        per_endpoint_extra = max(0, retries % len(endpoints))
        query_timeout = timeout_seconds if timeout_seconds is not None else SPARQL_TIMEOUT_SECONDS

        for idx, endpoint in enumerate(endpoints):
            endpoint_name = urlparse(endpoint).path or endpoint
            endpoint_retries = per_endpoint_base + (1 if idx < per_endpoint_extra else 0)
            payload = self.get_json(
                endpoint,
                method="POST",
                data={"query": sparql, "format": "json"},
                retries=endpoint_retries,
                min_delay_seconds=WIKIDATA_MIN_DELAY_SECONDS,
                timeout_seconds=query_timeout,
                context=f"Wikidata SPARQL query {endpoint_name}",
            )
            if payload:
                break

        if not payload:
            self.last_query_succeeded = False
            return []

        bindings = payload.get("results", {}).get("bindings", [])
        self.last_query_succeeded = True
        print(f"  ok  {len(bindings)} results")
        return bindings


LOCATION_SCOPE_FILTER = f"""
  {{
    ?location wdt:P17 {MACEDONIA} .
  }} UNION {{
    ?location wdt:P131 {MACEDONIA} .
  }} UNION {{
    ?location wdt:P131/wdt:P131 {MACEDONIA} .
  }} UNION {{
    ?location wdt:P131/wdt:P131/wdt:P131 {MACEDONIA} .
  }} UNION {{
    ?location wdt:P131 {MACEDONIA_REGION} .
  }} UNION {{
    ?location wdt:P131/wdt:P131 {MACEDONIA_REGION} .
  }} UNION {{
    ?location wdt:P131/wdt:P131/wdt:P131 {MACEDONIA_REGION} .
  }} UNION {{
    ?location wdt:P131 {MACEDONIA_REGION_ANCIENT} .
  }} UNION {{
    ?location wdt:P131/wdt:P131 {MACEDONIA_REGION_ANCIENT} .
  }} UNION {{
    ?location wdt:P131/wdt:P131/wdt:P131 {MACEDONIA_REGION_ANCIENT} .
  }}
"""

QUERY_PERSONS = f"""
SELECT DISTINCT
  ?person ?personLabel ?personDescription
  ?birthDate ?deathDate
  ?birthPlace ?birthPlaceLabel
  ?nationality ?nationalityLabel
  ?occupation ?occupationLabel
WHERE {{
  ?person wdt:P31 wd:Q5 .

  OPTIONAL {{ ?person wdt:P569 ?birthDate }}
  OPTIONAL {{ ?person wdt:P570 ?deathDate }}
  OPTIONAL {{ ?person wdt:P19  ?birthPlace }}
  OPTIONAL {{ ?person wdt:P27  ?nationality }}
  OPTIONAL {{ ?person wdt:P106 ?occupation }}

  {{
    ?person wdt:P1344 ?event .
    ?event wdt:P31/wdt:P279* wd:Q13418847 .
    OPTIONAL {{ ?event wdt:P580 ?eventStartDate }}
    OPTIONAL {{ ?event wdt:P585 ?eventPointInTime }}
    BIND(COALESCE(?eventStartDate, ?eventPointInTime) AS ?eventDate)
    FILTER(BOUND(?eventDate))
    FILTER(YEAR(?eventDate) >= {HISTORY_START_YEAR} && YEAR(?eventDate) <= {HISTORY_END_YEAR})
    ?event wdt:P276 ?location .
    {LOCATION_SCOPE_FILTER}
  }}
  UNION
  {{
    ?person wdt:P19 ?birthPlace .
    OPTIONAL {{ ?person wdt:P569 ?birthDateForFilter }}
    FILTER(BOUND(?birthDateForFilter))
    FILTER(YEAR(?birthDateForFilter) >= {HISTORY_START_YEAR - 40} && YEAR(?birthDateForFilter) <= {HISTORY_END_YEAR})
    BIND(?birthPlace AS ?location)
    {LOCATION_SCOPE_FILTER}
  }}
  UNION
  {{
    ?person wdt:P20 ?deathPlace .
    OPTIONAL {{ ?person wdt:P570 ?deathDateForFilter }}
    FILTER(BOUND(?deathDateForFilter))
    FILTER(YEAR(?deathDateForFilter) >= {HISTORY_START_YEAR} && YEAR(?deathDateForFilter) <= {HISTORY_END_YEAR})
    BIND(?deathPlace AS ?location)
    {LOCATION_SCOPE_FILTER}
  }}

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
ORDER BY ?personLabel
LIMIT {PERSON_LIMIT}
"""

QUERY_EVENTS = f"""
SELECT DISTINCT
  ?event ?eventLabel ?eventDescription
  ?startDate ?endDate
  ?location ?locationLabel
  ?coords
WHERE {{
  ?event wdt:P31/wdt:P279* wd:Q13418847 .
  OPTIONAL {{ ?event wdt:P580 ?startDate }}
  OPTIONAL {{ ?event wdt:P582 ?endDate }}
  OPTIONAL {{ ?event wdt:P585 ?pointInTime }}
  BIND(COALESCE(?startDate, ?pointInTime) AS ?eventDate)
  FILTER(BOUND(?eventDate))
  FILTER(YEAR(?eventDate) >= {HISTORY_START_YEAR} && YEAR(?eventDate) <= {HISTORY_END_YEAR})

  ?event wdt:P276 ?location .
  {LOCATION_SCOPE_FILTER}

  OPTIONAL {{ ?location wdt:P625 ?coords }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
ORDER BY ?eventDate
LIMIT {EVENT_LIMIT}
"""

QUERY_PLACES = f"""
SELECT DISTINCT
  ?place ?placeLabel ?placeDescription
  ?coords
  ?placeType ?placeTypeLabel
  ?country ?countryLabel
WHERE {{
  {{
    ?event wdt:P31/wdt:P279* wd:Q13418847 .
    OPTIONAL {{ ?event wdt:P580 ?eventStartDate }}
    OPTIONAL {{ ?event wdt:P585 ?eventPointInTime }}
    BIND(COALESCE(?eventStartDate, ?eventPointInTime) AS ?eventDate)
    FILTER(BOUND(?eventDate))
    FILTER(YEAR(?eventDate) >= {HISTORY_START_YEAR} && YEAR(?eventDate) <= {HISTORY_END_YEAR})
    ?event wdt:P276 ?place .
    BIND(?place AS ?location)
    {LOCATION_SCOPE_FILTER}
  }} UNION {{
    ?person wdt:P19 ?place .
    OPTIONAL {{ ?person wdt:P569 ?birthDate }}
    FILTER(BOUND(?birthDate))
    FILTER(YEAR(?birthDate) >= {HISTORY_START_YEAR - 40} && YEAR(?birthDate) <= {HISTORY_END_YEAR})
    BIND(?place AS ?location)
    {LOCATION_SCOPE_FILTER}
  }} UNION {{
    VALUES ?place {{
      {MACEDONIA}
      {MACEDONIA_REGION}
      {SEED_ILINDEN_UPRISING}
      {SEED_KRUSEVO_REPUBLIC}
    }}
  }}

  ?place wdt:P31 ?placeType .
  OPTIONAL {{ ?place wdt:P625 ?coords }}
  OPTIONAL {{ ?place wdt:P17  ?country }}

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
LIMIT {PLACE_LIMIT}
"""

QUERY_ORGANIZATIONS = f"""
SELECT DISTINCT
  ?org ?orgLabel ?orgDescription
  ?foundingDate ?dissolutionDate
  ?headquarters ?headquartersLabel
  ?orgType ?orgTypeLabel
WHERE {{
  ?org wdt:P31/wdt:P279* wd:Q43229 .

  OPTIONAL {{ ?org wdt:P571 ?foundingDate }}
  OPTIONAL {{ ?org wdt:P576 ?dissolutionDate }}
  OPTIONAL {{ ?org wdt:P159 ?headquarters }}
  OPTIONAL {{ ?org wdt:P740 ?formationPlace }}
  OPTIONAL {{ ?org wdt:P31  ?orgType }}

  FILTER(
    (BOUND(?foundingDate) && YEAR(?foundingDate) <= {HISTORY_END_YEAR} && YEAR(?foundingDate) >= {HISTORY_START_YEAR - 40})
    || (BOUND(?dissolutionDate) && YEAR(?dissolutionDate) >= {HISTORY_START_YEAR} && YEAR(?dissolutionDate) <= {HISTORY_END_YEAR})
    || EXISTS {{
      ?org wdt:P1344 ?event .
      ?event wdt:P31/wdt:P279* wd:Q13418847 .
      OPTIONAL {{ ?event wdt:P580 ?eventStartDate }}
      OPTIONAL {{ ?event wdt:P585 ?eventPointInTime }}
      BIND(COALESCE(?eventStartDate, ?eventPointInTime) AS ?eventDate)
      FILTER(BOUND(?eventDate))
      FILTER(YEAR(?eventDate) >= {HISTORY_START_YEAR} && YEAR(?eventDate) <= {HISTORY_END_YEAR})
      ?event wdt:P276 ?location .
      {LOCATION_SCOPE_FILTER}
    }}
  )

  FILTER(
    (BOUND(?headquarters) && EXISTS {{
      BIND(?headquarters AS ?location)
      {LOCATION_SCOPE_FILTER}
    }})
    || (BOUND(?formationPlace) && EXISTS {{
      BIND(?formationPlace AS ?location)
      {LOCATION_SCOPE_FILTER}
    }})
    || EXISTS {{
      ?org wdt:P1344 ?event .
      ?event wdt:P276 ?location .
      {LOCATION_SCOPE_FILTER}
    }}
  )

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
LIMIT {ORG_LIMIT}
"""

QUERY_DOCUMENTS = f"""
SELECT DISTINCT
  ?doc ?docLabel ?docDescription
  ?date
  ?author ?authorLabel
  ?language ?languageLabel
WHERE {{
  ?doc wdt:P31/wdt:P279* wd:Q49848 .
  OPTIONAL {{ ?doc wdt:P577 ?date }}
  OPTIONAL {{ ?doc wdt:P50  ?author }}
  OPTIONAL {{ ?doc wdt:P407 ?language }}

  FILTER(
    (BOUND(?date) && YEAR(?date) >= {HISTORY_START_YEAR} && YEAR(?date) <= {HISTORY_END_YEAR})
    || EXISTS {{
      ?doc wdt:P361 ?event .
      ?event wdt:P31/wdt:P279* wd:Q13418847 .
      OPTIONAL {{ ?event wdt:P580 ?eventStartDate }}
      OPTIONAL {{ ?event wdt:P585 ?eventPointInTime }}
      BIND(COALESCE(?eventStartDate, ?eventPointInTime) AS ?eventDate)
      FILTER(BOUND(?eventDate))
      FILTER(YEAR(?eventDate) >= {HISTORY_START_YEAR} && YEAR(?eventDate) <= {HISTORY_END_YEAR})
      ?event wdt:P276 ?location .
      {LOCATION_SCOPE_FILTER}
    }}
    || EXISTS {{
      ?doc wdt:P921 ?topicEvent .
      ?topicEvent wdt:P31/wdt:P279* wd:Q13418847 .
      OPTIONAL {{ ?topicEvent wdt:P580 ?topicStartDate }}
      OPTIONAL {{ ?topicEvent wdt:P585 ?topicPointInTime }}
      BIND(COALESCE(?topicStartDate, ?topicPointInTime) AS ?topicDate)
      FILTER(BOUND(?topicDate))
      FILTER(YEAR(?topicDate) >= {HISTORY_START_YEAR} && YEAR(?topicDate) <= {HISTORY_END_YEAR})
      ?topicEvent wdt:P276 ?location .
      {LOCATION_SCOPE_FILTER}
    }}
    || EXISTS {{
      ?doc wdt:P50 ?authorCandidate .
      ?authorCandidate wdt:P31 wd:Q5 .
      OPTIONAL {{ ?authorCandidate wdt:P569 ?authorBirthDate }}
      FILTER(BOUND(?authorBirthDate))
      FILTER(YEAR(?authorBirthDate) >= {HISTORY_START_YEAR - 60} && YEAR(?authorBirthDate) <= {HISTORY_END_YEAR})
      ?authorCandidate wdt:P19 ?authorBirthPlace .
      BIND(?authorBirthPlace AS ?location)
      {LOCATION_SCOPE_FILTER}
    }}
  )

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
LIMIT {DOC_LIMIT}
"""


def iter_year_windows(start_year: int, end_year: int, window_size: int) -> list[tuple[int, int]]:
    size = max(1, window_size)
    windows: list[tuple[int, int]] = []
    year = start_year
    while year <= end_year:
        windows.append((year, min(end_year, year + size - 1)))
        year += size
    return windows


def chunked_list(values: list[str], size: int) -> list[list[str]]:
    step = max(1, size)
    return [values[i:i + step] for i in range(0, len(values), step)]


def qid_from_wd_uri(uri: str | None) -> str | None:
    if not uri or "wikidata.org/entity/" not in uri:
        return None
    return uri.rstrip("/").split("/")[-1]


def values_clause_from_qids(qids: list[str]) -> str:
    return " ".join(f"wd:{qid}" for qid in qids)


def unique_bindings_by_key(rows: list[dict], key: str) -> list[dict]:
    seen: set[str] = set()
    unique: list[dict] = []
    for row in rows:
        val = safe_str(row, key)
        if not val or val in seen:
            continue
        seen.add(val)
        unique.append(row)
    return unique


def collect_scope_location_qids(client: WikidataClient) -> list[str]:
    roots = f"{MACEDONIA} {MACEDONIA_REGION} {MACEDONIA_REGION_ANCIENT}"
    sparql = f"""
SELECT DISTINCT ?location WHERE {{
  VALUES ?root {{ {roots} }}
  {{
    BIND(?root AS ?location)
  }} UNION {{
    ?location wdt:P131 ?root .
  }} UNION {{
    ?location wdt:P131/wdt:P131 ?root .
  }} UNION {{
    ?location wdt:P131/wdt:P131/wdt:P131 ?root .
  }} UNION {{
    ?location wdt:P17 ?root .
  }}
}}
LIMIT {SCOPE_LOCATION_LIMIT}
"""
    rows = client.query(sparql)
    qids = {
        qid for qid in (
            qid_from_wd_uri(safe_str(row, "location")) for row in rows
        ) if qid
    }

    # Keep core region anchors in scope even if the discovery query returns no rows.
    qids.update({"Q221", "Q103251", "Q83958"})
    return sorted(qids)


def build_events_query_for_window(year_start: int, year_end: int, location_qids: list[str]) -> str:
    values_clause = values_clause_from_qids(location_qids)
    event_type_filter = ""
    if EVENT_REQUIRE_OCCURRENCE:
        event_type_filter = """
  FILTER EXISTS {
    ?event wdt:P31 ?eventType .
    ?eventType wdt:P279* wd:Q1190554 .
  }
"""
    return f"""
SELECT DISTINCT
  ?event
  ?startDate ?endDate
  ?location
WHERE {{
  VALUES ?location {{ {values_clause} }}
  {{
    ?event wdt:P276 ?location .
  }} UNION {{
    ?event wdt:P17 ?location .
  }} UNION {{
    ?event wdt:P131 ?location .
  }}

  OPTIONAL {{ ?event wdt:P580 ?startDate }}
  OPTIONAL {{ ?event wdt:P582 ?endDate }}
  OPTIONAL {{ ?event wdt:P585 ?pointInTime }}
  BIND(COALESCE(?startDate, ?pointInTime, ?endDate) AS ?eventDate)
  FILTER(BOUND(?eventDate))
  FILTER(YEAR(?eventDate) >= {year_start} && YEAR(?eventDate) <= {year_end})
  {event_type_filter}
}}
LIMIT {EVENT_LIMIT_PER_WINDOW}
"""


def collect_events_for_chunk(
    client: WikidataClient,
    location_chunk: list[str],
    year_start: int,
    year_end: int,
) -> list[dict]:
    rows = client.query(build_events_query_for_window(year_start, year_end, location_chunk))
    if rows or client.last_query_succeeded:
        return rows

    fallback_rows: list[dict] = []
    print(
        f"        full-range query failed for this location chunk; "
        f"retrying by year windows ({EVENT_MIN_YEAR_WINDOW}-year)"
    )
    windows = iter_year_windows(year_start, year_end, EVENT_MIN_YEAR_WINDOW)
    for window_idx, (window_start, window_end) in enumerate(windows, start=1):
        print(
            f"        chunk fallback window {window_idx}/{len(windows)}: "
            f"{window_start}-{window_end}"
        )
        year_rows = client.query(build_events_query_for_window(window_start, window_end, location_chunk))
        if not year_rows and not client.last_query_succeeded:
            print("        x fallback window failed; skipping window")
            continue
        fallback_rows.extend(year_rows)

    return fallback_rows


def collect_events(client: WikidataClient, location_qids: list[str] | None = None) -> list[dict]:
    all_rows: list[dict] = []
    if location_qids is None:
        location_qids = collect_scope_location_qids(client)
    if not location_qids:
        return []

    print(f"    scope locations discovered: {len(location_qids)}")
    location_chunks = chunked_list(location_qids, SCOPE_LOCATION_CHUNK_SIZE)

    full_range = (HISTORY_START_YEAR, HISTORY_END_YEAR)
    print(f"    full-range pass: {full_range[0]}-{full_range[1]}")
    failed_chunks = 0
    successful_chunks = 0
    for chunk_idx, location_chunk in enumerate(location_chunks, start=1):
        print(
            f"      location chunk {chunk_idx}/{len(location_chunks)} "
            f"({len(location_chunk)} places)"
        )
        rows = collect_events_for_chunk(client, location_chunk, full_range[0], full_range[1])
        if not rows and not client.last_query_succeeded:
            failed_chunks += 1
            continue
        successful_chunks += 1
        all_rows.extend(rows)

    unique_events = unique_bindings_by_key(all_rows, "event")
    print(
        f"    event chunk summary: success={successful_chunks}, failed={failed_chunks}, "
        f"unique events={len(unique_events)}"
    )
    return unique_events


def build_person_scope_ids_query(location_qids: list[str], mode: str) -> str:
    values_clause = values_clause_from_qids(location_qids)
    if mode == "birth":
        return f"""
SELECT DISTINCT ?person
WHERE {{
  VALUES ?location {{ {values_clause} }}
  ?person wdt:P31 wd:Q5 ;
          wdt:P19 ?location ;
          wdt:P569 ?birthDate .
  FILTER(YEAR(?birthDate) >= {PERSON_SCOPE_BIRTH_FROM_YEAR} && YEAR(?birthDate) <= {PERSON_SCOPE_BIRTH_TO_YEAR})
}}
LIMIT {PERSON_SCOPE_LIMIT_PER_CHUNK}
"""

    return f"""
SELECT DISTINCT ?person
WHERE {{
  VALUES ?location {{ {values_clause} }}
  ?person wdt:P31 wd:Q5 ;
          wdt:P20 ?location ;
          wdt:P570 ?deathDate .
  FILTER(YEAR(?deathDate) >= {PERSON_SCOPE_DEATH_FROM_YEAR} && YEAR(?deathDate) <= {PERSON_SCOPE_DEATH_TO_YEAR})
}}
LIMIT {PERSON_SCOPE_LIMIT_PER_CHUNK}
"""


def collect_person_ids_for_chunk(
    client: WikidataClient,
    location_chunk: list[str],
    mode: str,
    chunk_label: str,
) -> list[dict]:
    sparql = build_person_scope_ids_query(location_chunk, mode)
    rows = client.query(
        sparql,
        retries=PERSON_SCOPE_ID_RETRIES,
        timeout_seconds=PERSON_SCOPE_ID_TIMEOUT_SECONDS,
    )
    if rows or client.last_query_succeeded:
        return rows

    if len(location_chunk) <= 1:
        print(f"      x persons-by-scope ({mode}) failed for {chunk_label}; skipping")
        return []

    midpoint = len(location_chunk) // 2
    left_chunk = location_chunk[:midpoint]
    right_chunk = location_chunk[midpoint:]
    print(
        f"      persons-by-scope ({mode}) failed for {chunk_label}; "
        f"splitting into {len(left_chunk)} + {len(right_chunk)} places"
    )

    left_rows = collect_person_ids_for_chunk(
        client,
        left_chunk,
        mode,
        f"{chunk_label}L",
    )
    right_rows = collect_person_ids_for_chunk(
        client,
        right_chunk,
        mode,
        f"{chunk_label}R",
    )
    return left_rows + right_rows


def collect_person_details(client: WikidataClient, person_qids: list[str]) -> list[dict]:
    if not person_qids:
        return []

    all_rows: list[dict] = []
    person_chunks = chunked_list(person_qids, PERSON_SCOPE_DETAILS_CHUNK_SIZE)
    for idx, person_chunk in enumerate(person_chunks, start=1):
        values_clause = values_clause_from_qids(person_chunk)
        sparql = f"""
SELECT DISTINCT
  ?person ?personLabel ?personDescription
  ?birthDate ?deathDate
  ?birthPlace ?birthPlaceLabel
  ?nationality ?nationalityLabel
  ?occupation ?occupationLabel
WHERE {{
  VALUES ?person {{ {values_clause} }}
  OPTIONAL {{ ?person wdt:P569 ?birthDate }}
  OPTIONAL {{ ?person wdt:P570 ?deathDate }}
  OPTIONAL {{ ?person wdt:P19  ?birthPlace }}
  OPTIONAL {{ ?person wdt:P27  ?nationality }}
  OPTIONAL {{ ?person wdt:P106 ?occupation }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
"""
        print(f"    persons-details chunk {idx}/{len(person_chunks)} ({len(person_chunk)} persons)")
        rows = client.query(
            sparql,
            retries=PERSON_SCOPE_DETAILS_RETRIES,
            timeout_seconds=PERSON_SCOPE_DETAILS_TIMEOUT_SECONDS,
        )
        if not rows and not client.last_query_succeeded:
            print("      x persons-details chunk failed; keeping minimal person rows for this chunk")
            for qid in person_chunk:
                all_rows.append({"person": {"type": "uri", "value": f"http://www.wikidata.org/entity/{qid}"}})
            continue
        all_rows.extend(rows)

    return unique_bindings_by_key(all_rows, "person")


def collect_persons_by_scope(client: WikidataClient, location_qids: list[str]) -> list[dict]:
    if not location_qids:
        return []

    person_qids: set[str] = set()
    location_chunks = chunked_list(location_qids, SCOPE_LOCATION_CHUNK_SIZE)
    for idx, location_chunk in enumerate(location_chunks, start=1):
        chunk_label = f"{idx}/{len(location_chunks)}"
        print(f"    persons-by-scope (birth) chunk {chunk_label} ({len(location_chunk)} places)")
        birth_rows = collect_person_ids_for_chunk(client, location_chunk, "birth", chunk_label)
        for row in birth_rows:
            qid = qid_from_wd_uri(safe_str(row, "person"))
            if qid:
                person_qids.add(qid)

        if PERSON_SCOPE_INCLUDE_DEATH:
            print(f"    persons-by-scope (death) chunk {chunk_label} ({len(location_chunk)} places)")
            death_rows = collect_person_ids_for_chunk(client, location_chunk, "death", chunk_label)
            for row in death_rows:
                qid = qid_from_wd_uri(safe_str(row, "person"))
                if qid:
                    person_qids.add(qid)

    return collect_person_details(client, sorted(person_qids))


def collect_events_from_persons(client: WikidataClient, person_qids: list[str]) -> list[dict]:
    if not person_qids:
        return []

    all_rows: list[dict] = []
    person_chunks = chunked_list(person_qids, VALUES_CHUNK_SIZE)
    for idx, person_chunk in enumerate(person_chunks, start=1):
        values_clause = values_clause_from_qids(person_chunk)
        sparql = f"""
SELECT DISTINCT
  ?event
  ?startDate ?endDate
  ?location
WHERE {{
  VALUES ?person {{ {values_clause} }}
  {{
    ?person wdt:P1344 ?event .
  }} UNION {{
    ?person wdt:P793 ?event .
  }}

  OPTIONAL {{ ?event wdt:P580 ?startDate }}
  OPTIONAL {{ ?event wdt:P582 ?endDate }}
  OPTIONAL {{ ?event wdt:P585 ?pointInTime }}
  OPTIONAL {{ ?event wdt:P276 ?location }}

  BIND(COALESCE(?startDate, ?pointInTime, ?endDate) AS ?eventDate)
  FILTER(BOUND(?eventDate))
  FILTER(YEAR(?eventDate) >= {HISTORY_START_YEAR} && YEAR(?eventDate) <= {HISTORY_END_YEAR})
}}
LIMIT {EVENT_FROM_PERSON_LIMIT_PER_CHUNK}
"""
        print(f"    events-from-persons chunk {idx}/{len(person_chunks)} ({len(person_chunk)} persons)")
        all_rows.extend(client.query(sparql))

    return unique_bindings_by_key(all_rows, "event")


def collect_event_details(client: WikidataClient, event_qids: list[str]) -> list[dict]:
    if not event_qids:
        return []

    all_rows: list[dict] = []
    event_chunks = chunked_list(event_qids, EVENT_DETAILS_CHUNK_SIZE)
    for idx, event_chunk in enumerate(event_chunks, start=1):
        values_clause = values_clause_from_qids(event_chunk)
        sparql = f"""
SELECT DISTINCT
  ?event ?eventLabel ?eventDescription
  ?startDate ?endDate
  ?location ?locationLabel
  ?coords
WHERE {{
  VALUES ?event {{ {values_clause} }}
  OPTIONAL {{ ?event wdt:P580 ?startDate }}
  OPTIONAL {{ ?event wdt:P582 ?endDate }}
  OPTIONAL {{ ?event wdt:P585 ?pointInTime }}
  OPTIONAL {{
    ?event wdt:P276 ?location .
    OPTIONAL {{ ?location wdt:P625 ?coords }}
  }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
"""
        print(f"    event details chunk {idx}/{len(event_chunks)} ({len(event_chunk)} events)")
        rows = client.query(
            sparql,
            retries=EVENT_DETAILS_RETRIES,
            timeout_seconds=EVENT_DETAILS_TIMEOUT_SECONDS,
        )
        if not rows and not client.last_query_succeeded:
            print("      x event details chunk failed; keeping raw event rows for those IDs")
            continue
        all_rows.extend(rows)

    return unique_bindings_by_key(all_rows, "event")


def collect_persons_from_events(client: WikidataClient, event_qids: list[str]) -> list[dict]:
    if not event_qids:
        return []

    all_rows: list[dict] = []
    event_chunks = chunked_list(event_qids, VALUES_CHUNK_SIZE)
    for idx, event_chunk in enumerate(event_chunks, start=1):
        values_clause = values_clause_from_qids(event_chunk)
        sparql = f"""
SELECT DISTINCT
  ?person ?personLabel ?personDescription
  ?birthDate ?deathDate
  ?birthPlace ?birthPlaceLabel
  ?nationality ?nationalityLabel
  ?occupation ?occupationLabel
WHERE {{
  VALUES ?event {{ {values_clause} }}
  ?person wdt:P1344 ?event ;
          wdt:P31 wd:Q5 .

  OPTIONAL {{ ?person wdt:P569 ?birthDate }}
  OPTIONAL {{ ?person wdt:P570 ?deathDate }}
  OPTIONAL {{ ?person wdt:P19  ?birthPlace }}
  OPTIONAL {{ ?person wdt:P27  ?nationality }}
  OPTIONAL {{ ?person wdt:P106 ?occupation }}

  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
LIMIT {PERSON_LIMIT_PER_EVENT_CHUNK}
"""
        print(f"    persons chunk {idx}/{len(event_chunks)} ({len(event_chunk)} events)")
        all_rows.extend(client.query(sparql))

    return unique_bindings_by_key(all_rows, "person")


def collect_place_details(client: WikidataClient, place_qids: list[str]) -> list[dict]:
    if not place_qids:
        return []

    all_rows: list[dict] = []
    place_chunks = chunked_list(place_qids, VALUES_CHUNK_SIZE)
    for idx, place_chunk in enumerate(place_chunks, start=1):
        values_clause = values_clause_from_qids(place_chunk)
        sparql = f"""
SELECT DISTINCT
  ?place ?placeLabel ?placeDescription
  ?coords
  ?placeType ?placeTypeLabel
  ?country ?countryLabel
WHERE {{
  VALUES ?place {{ {values_clause} }}
  ?place wdt:P31 ?placeType .
  OPTIONAL {{ ?place wdt:P625 ?coords }}
  OPTIONAL {{ ?place wdt:P17  ?country }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
"""
        print(f"    places chunk {idx}/{len(place_chunks)} ({len(place_chunk)} places)")
        all_rows.extend(client.query(sparql))

    return unique_bindings_by_key(all_rows, "place")


def collect_organizations(client: WikidataClient, event_qids: list[str], person_qids: list[str]) -> list[dict]:
    org_qids: set[str] = set()

    for event_chunk in chunked_list(event_qids, VALUES_CHUNK_SIZE):
        values_clause = values_clause_from_qids(event_chunk)
        sparql = f"""
SELECT DISTINCT ?org WHERE {{
  VALUES ?event {{ {values_clause} }}
  ?org wdt:P1344 ?event ;
       wdt:P31/wdt:P279* wd:Q43229 .
}}
LIMIT {ORG_LIMIT_PER_CHUNK}
"""
        for row in client.query(sparql):
            qid = qid_from_wd_uri(safe_str(row, "org"))
            if qid:
                org_qids.add(qid)

    for person_chunk in chunked_list(person_qids, VALUES_CHUNK_SIZE):
        values_clause = values_clause_from_qids(person_chunk)
        sparql = f"""
SELECT DISTINCT ?org WHERE {{
  VALUES ?person {{ {values_clause} }}
  ?person wdt:P463 ?org .
  ?org wdt:P31/wdt:P279* wd:Q43229 .
}}
LIMIT {ORG_LIMIT_PER_CHUNK}
"""
        for row in client.query(sparql):
            qid = qid_from_wd_uri(safe_str(row, "org"))
            if qid:
                org_qids.add(qid)

    if not org_qids:
        return []

    all_rows: list[dict] = []
    org_chunks = chunked_list(sorted(org_qids), VALUES_CHUNK_SIZE)
    for idx, org_chunk in enumerate(org_chunks, start=1):
        values_clause = values_clause_from_qids(org_chunk)
        sparql = f"""
SELECT DISTINCT
  ?org ?orgLabel ?orgDescription
  ?foundingDate ?dissolutionDate
  ?headquarters ?headquartersLabel
  ?orgType ?orgTypeLabel
WHERE {{
  VALUES ?org {{ {values_clause} }}
  OPTIONAL {{ ?org wdt:P571 ?foundingDate }}
  OPTIONAL {{ ?org wdt:P576 ?dissolutionDate }}
  OPTIONAL {{ ?org wdt:P159 ?headquarters }}
  OPTIONAL {{ ?org wdt:P31  ?orgType }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
"""
        print(f"    organizations chunk {idx}/{len(org_chunks)} ({len(org_chunk)} orgs)")
        all_rows.extend(client.query(sparql))

    return unique_bindings_by_key(all_rows, "org")


def collect_documents(client: WikidataClient, event_qids: list[str], person_qids: list[str]) -> list[dict]:
    all_rows: list[dict] = []

    for event_chunk in chunked_list(event_qids, VALUES_CHUNK_SIZE):
        values_clause = values_clause_from_qids(event_chunk)
        sparql = f"""
SELECT DISTINCT
  ?doc ?docLabel ?docDescription
  ?date
  ?author ?authorLabel
  ?language ?languageLabel
WHERE {{
  VALUES ?event {{ {values_clause} }}
  ?doc wdt:P31/wdt:P279* wd:Q49848 .
  {{
    ?doc wdt:P361 ?event .
  }} UNION {{
    ?doc wdt:P921 ?event .
  }}
  OPTIONAL {{ ?doc wdt:P577 ?date }}
  OPTIONAL {{ ?doc wdt:P50  ?author }}
  OPTIONAL {{ ?doc wdt:P407 ?language }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
LIMIT {DOC_LIMIT_PER_CHUNK}
"""
        all_rows.extend(client.query(sparql))

    for person_chunk in chunked_list(person_qids, VALUES_CHUNK_SIZE):
        values_clause = values_clause_from_qids(person_chunk)
        sparql = f"""
SELECT DISTINCT
  ?doc ?docLabel ?docDescription
  ?date
  ?author ?authorLabel
  ?language ?languageLabel
WHERE {{
  VALUES ?author {{ {values_clause} }}
  ?doc wdt:P31/wdt:P279* wd:Q49848 ;
       wdt:P50 ?author .
  OPTIONAL {{ ?doc wdt:P577 ?date }}
  OPTIONAL {{ ?doc wdt:P407 ?language }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
LIMIT {DOC_LIMIT_PER_CHUNK}
"""
        all_rows.extend(client.query(sparql))

    return unique_bindings_by_key(all_rows, "doc")


def safe_str(binding: dict, key: str) -> str | None:
    val = binding.get(key, {}).get("value")
    return val if val else None


def wikidata_uri_to_local(wd_uri: str) -> URIRef:
    qid = wd_uri.rstrip("/").split("/")[-1]
    return MK[qid]


def add_label(g: Graph, subject: URIRef, label_str: str) -> None:
    if label_str:
        g.add((subject, RDFS.label, Literal(label_str, lang="mk")))


def normalize_wikidata_date(date_str: str) -> tuple[str | None, URIRef | None]:
    value = date_str.strip()
    if not value:
        return None, None

    if "T" in value:
        value = value.split("T", 1)[0]
    value = value.rstrip("Zz")
    if value.startswith("+"):
        value = value[1:]

    match_date = re.fullmatch(r"(-?\d+)-(\d{2})-(\d{2})", value)
    if match_date:
        year, month, day = match_date.groups()
        if month == "00" and day == "00":
            return year, XSD.gYear
        if month != "00" and day == "00":
            return f"{year}-{month}", XSD.gYearMonth
        if month != "00" and day != "00":
            return f"{year}-{month}-{day}", XSD.date
        return None, None

    match_year_month = re.fullmatch(r"(-?\d+)-(\d{2})", value)
    if match_year_month:
        year, month = match_year_month.groups()
        if month == "00":
            return year, XSD.gYear
        return f"{year}-{month}", XSD.gYearMonth

    if re.fullmatch(r"-?\d+", value):
        return value, XSD.gYear

    return None, None


def add_date_literal(g: Graph, subject: URIRef, predicate: URIRef, date_str: str) -> None:
    if not date_str:
        return

    lexical, datatype = normalize_wikidata_date(date_str)
    if lexical and datatype:
        g.add((subject, predicate, Literal(lexical, datatype=datatype)))
        return

    g.add((subject, predicate, Literal(date_str)))


def convert_persons(rows: list[dict], g: Graph) -> None:
    print(f"\n  Converting {len(rows)} persons to RDF ...")
    for row in rows:
        wd_uri = safe_str(row, "person")
        if not wd_uri:
            continue

        subject = wikidata_uri_to_local(wd_uri)
        g.add((subject, RDF.type, MKO.Person))
        g.add((subject, OWL.sameAs, URIRef(wd_uri)))

        add_label(g, subject, safe_str(row, "personLabel"))
        desc = safe_str(row, "personDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        add_date_literal(g, subject, MKO.birthDate, safe_str(row, "birthDate"))
        add_date_literal(g, subject, MKO.deathDate, safe_str(row, "deathDate"))

        bp_uri = safe_str(row, "birthPlace")
        if bp_uri:
            g.add((subject, MKO.bornIn, wikidata_uri_to_local(bp_uri)))

        occ = safe_str(row, "occupationLabel")
        if occ:
            g.add((subject, MKO.occupation, Literal(occ, lang="en")))


def convert_events(rows: list[dict], g: Graph) -> None:
    print(f"\n  Converting {len(rows)} events to RDF ...")
    for row in rows:
        wd_uri = safe_str(row, "event")
        if not wd_uri:
            continue

        subject = wikidata_uri_to_local(wd_uri)
        g.add((subject, RDF.type, MKO.HistoricalEvent))
        g.add((subject, OWL.sameAs, URIRef(wd_uri)))
        g.add((subject, MKO.partOf, MK[PERIOD_RESOURCE_ID]))

        add_label(g, subject, safe_str(row, "eventLabel"))
        desc = safe_str(row, "eventDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        add_date_literal(g, subject, MKO.startDate, safe_str(row, "startDate"))
        add_date_literal(g, subject, MKO.endDate, safe_str(row, "endDate"))

        loc_uri = safe_str(row, "location")
        if loc_uri:
            g.add((subject, MKO.tookPlaceIn, wikidata_uri_to_local(loc_uri)))

        start = safe_str(row, "startDate")
        if start:
            interval_node = BNode()
            g.add((subject, TIME.hasTime, interval_node))
            g.add((interval_node, RDF.type, TIME.Interval))

            start_lex, start_type = normalize_wikidata_date(start)
            if start_lex and start_type:
                g.add((interval_node, TIME.hasBeginning, Literal(start_lex, datatype=start_type)))

            end = safe_str(row, "endDate")
            end_lex, end_type = normalize_wikidata_date(end) if end else (None, None)
            if end_lex and end_type:
                g.add((interval_node, TIME.hasEnd, Literal(end_lex, datatype=end_type)))


def convert_places(rows: list[dict], g: Graph) -> None:
    print(f"\n  Converting {len(rows)} places to RDF ...")
    seen = set()
    for row in rows:
        wd_uri = safe_str(row, "place")
        if not wd_uri or wd_uri in seen:
            continue

        seen.add(wd_uri)
        subject = wikidata_uri_to_local(wd_uri)

        g.add((subject, RDF.type, MKO.Place))
        g.add((subject, OWL.sameAs, URIRef(wd_uri)))

        add_label(g, subject, safe_str(row, "placeLabel"))
        desc = safe_str(row, "placeDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        coords = safe_str(row, "coords")
        if coords:
            g.add((subject, GEO.asWKT, Literal(coords)))

        country_uri = safe_str(row, "country")
        if country_uri:
            g.add((subject, MKO.locatedInCountry, wikidata_uri_to_local(country_uri)))


def convert_organizations(rows: list[dict], g: Graph) -> None:
    print(f"\n  Converting {len(rows)} organizations to RDF ...")
    seen = set()
    for row in rows:
        wd_uri = safe_str(row, "org")
        if not wd_uri or wd_uri in seen:
            continue

        seen.add(wd_uri)
        subject = wikidata_uri_to_local(wd_uri)

        g.add((subject, RDF.type, MKO.Organization))
        g.add((subject, OWL.sameAs, URIRef(wd_uri)))

        add_label(g, subject, safe_str(row, "orgLabel"))
        desc = safe_str(row, "orgDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        add_date_literal(g, subject, MKO.foundingDate, safe_str(row, "foundingDate"))
        add_date_literal(g, subject, MKO.dissolutionDate, safe_str(row, "dissolutionDate"))

        hq_uri = safe_str(row, "headquarters")
        if hq_uri:
            g.add((subject, MKO.headquarteredIn, wikidata_uri_to_local(hq_uri)))


def convert_documents(rows: list[dict], g: Graph) -> None:
    print(f"\n  Converting {len(rows)} documents to RDF ...")
    seen = set()
    for row in rows:
        wd_uri = safe_str(row, "doc")
        if not wd_uri or wd_uri in seen:
            continue

        seen.add(wd_uri)
        subject = wikidata_uri_to_local(wd_uri)

        g.add((subject, RDF.type, MKO.HistoricalDocument))
        g.add((subject, OWL.sameAs, URIRef(wd_uri)))

        add_label(g, subject, safe_str(row, "docLabel"))
        desc = safe_str(row, "docDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        add_date_literal(g, subject, MKO.dateCreated, safe_str(row, "date"))

        author_uri = safe_str(row, "author")
        if author_uri:
            g.add((subject, MKO.authoredBy, wikidata_uri_to_local(author_uri)))

        lang = safe_str(row, "languageLabel")
        if lang:
            g.add((subject, MKO.inLanguage, Literal(lang, lang="en")))


def add_ontology_triples(g: Graph) -> None:
    classes = [
        (MKO.Person, "Person", "A historical individual"),
        (MKO.HistoricalEvent, "Historical event", "A discrete historical occurrence"),
        (MKO.Place, "Place", "A geographic location"),
        (MKO.Organization, "Organization", "A collective actor"),
        (MKO.HistoricalDocument, "Historical document", "A written primary source"),
        (MKO.Period, "Period", "A named historical interval"),
    ]
    for cls, label, comment in classes:
        g.add((cls, RDF.type, OWL.Class))
        g.add((cls, RDFS.label, Literal(label, lang="en")))
        g.add((cls, RDFS.comment, Literal(comment, lang="en")))

    props = [
        (MKO.partOf, "part of"),
        (MKO.participatedIn, "participated in"),
        (MKO.memberOf, "member of"),
        (MKO.tookPlaceIn, "took place in"),
        (MKO.bornIn, "born in"),
        (MKO.headquarteredIn, "headquartered in"),
        (MKO.authoredBy, "authored by"),
        (MKO.locatedInCountry, "located in country"),
    ]
    for prop, label in props:
        g.add((prop, RDF.type, OWL.ObjectProperty))
        g.add((prop, RDFS.label, Literal(label, lang="en")))

    dt_props = [
        (MKO.birthDate, "birth date", RDFS.Literal),
        (MKO.deathDate, "death date", RDFS.Literal),
        (MKO.startDate, "start date", RDFS.Literal),
        (MKO.endDate, "end date", RDFS.Literal),
        (MKO.foundingDate, "founding date", RDFS.Literal),
        (MKO.dissolutionDate, "dissolution date", RDFS.Literal),
        (MKO.dateCreated, "date created", RDFS.Literal),
        (MKO.occupation, "occupation", XSD.string),
        (MKO.inLanguage, "in language", XSD.string),
    ]
    for prop, label, range_type in dt_props:
        g.add((prop, RDF.type, OWL.DatatypeProperty))
        g.add((prop, RDFS.label, Literal(label, lang="en")))
        g.add((prop, RDFS.range, range_type))

    g.bind("mk", MK)
    g.bind("mko", MKO)
    g.bind("wd", WD)
    g.bind("time", TIME)
    g.bind("geo", GEO)
    g.bind("owl", OWL)
    g.bind("skos", SKOS)
    g.bind("foaf", FOAF)

    period = MK[PERIOD_RESOURCE_ID]
    g.add((period, RDF.type, MKO.Period))
    g.add((period, RDFS.label, Literal(f"Macedonian history {HISTORY_START_YEAR}-{HISTORY_END_YEAR}", lang="en")))
    g.add((period, MKO.startDate, Literal(str(HISTORY_START_YEAR), datatype=XSD.gYear)))
    g.add((period, MKO.endDate, Literal(str(HISTORY_END_YEAR), datatype=XSD.gYear)))

    interval_node = BNode()
    g.add((period, TIME.hasTime, interval_node))
    g.add((interval_node, RDF.type, TIME.Interval))
    g.add((interval_node, TIME.hasBeginning, Literal(str(HISTORY_START_YEAR), datatype=XSD.gYear)))
    g.add((interval_node, TIME.hasEnd, Literal(str(HISTORY_END_YEAR), datatype=XSD.gYear)))

    # Keep Ilinden uprising as a key anchor event in this broader period.
    uprising = MK[SEED_ILINDEN_UPRISING.split(":", 1)[1]]
    g.add((uprising, RDF.type, MKO.HistoricalEvent))
    g.add((uprising, RDFS.label, Literal("Ilinden Uprising", lang="en")))
    g.add((uprising, OWL.sameAs, URIRef("http://www.wikidata.org/entity/Q1145682")))
    g.add((uprising, MKO.startDate, Literal("1903-08-02", datatype=XSD.date)))
    g.add((uprising, MKO.endDate, Literal("1903-10-03", datatype=XSD.date)))
    g.add((uprising, MKO.partOf, period))


def enrich_from_mk_wikipedia(g: Graph, entity_uris: list[str], client: WikidataClient) -> None:
    print("\n  Enriching from Macedonian Wikipedia ...")
    enriched = 0

    for wd_uri in entity_uris:
        qid = wd_uri.rstrip("/").split("/")[-1]
        subject = MK[qid]

        try:
            data = client.get_json(
                "https://www.wikidata.org/w/api.php",
                params={
                    "action": "wbgetentities",
                    "ids": qid,
                    "props": "sitelinks",
                    "sitefilter": "mkwiki",
                    "format": "json",
                },
                retries=5,
                min_delay_seconds=WIKIPEDIA_MIN_DELAY_SECONDS,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                context=f"Wikidata sitelink lookup for {qid}",
            )
            if not data:
                continue

            entity_data = data.get("entities", {}).get(qid, {})
            mk_link = entity_data.get("sitelinks", {}).get("mkwiki", {}).get("title")
            if not mk_link:
                continue

            wiki_url = f"https://mk.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(mk_link)}"
            wiki_data = client.get_json(
                wiki_url,
                retries=5,
                min_delay_seconds=WIKIPEDIA_MIN_DELAY_SECONDS,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                context=f"Wikipedia summary lookup for {qid}",
            )
            if not wiki_data:
                continue

            extract = wiki_data.get("extract", "")
            wiki_page = wiki_data.get("content_urls", {}).get("desktop", {}).get("page", "")
            if extract:
                g.add((subject, RDFS.comment, Literal(extract[:1000], lang="mk")))
            if wiki_page:
                g.add((subject, RDFS.seeAlso, URIRef(wiki_page)))
            enriched += 1

        except Exception as exc:  # noqa: BLE001
            print(f"    ! Wikipedia enrichment failed for {qid}: {exc}")

    print(f"  ok  enriched {enriched} entities from mk.wikipedia.org")


def main() -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    client = WikidataClient()

    if CONTACT_EMAIL == "set-your-email@example.com":
        print("WARNING: set WIKIDATA_CONTACT_EMAIL to your real email for better API compliance.")

    print("Macedonian History KG collector")
    print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Scope: years {HISTORY_START_YEAR}-{HISTORY_END_YEAR}")

    results: dict[str, list[dict]] = {}

    print("[1/5] Querying Wikidata for persons (scope-first) ...")
    location_qids = collect_scope_location_qids(client)
    print(f"    scope locations discovered: {len(location_qids)}")

    if PERSON_SCOPE_ENABLED:
        scope_person_rows = collect_persons_by_scope(client, location_qids)
        results["persons"] = scope_person_rows[:PERSON_LIMIT]
    else:
        print("    person-scope collection disabled (PERSON_SCOPE_ENABLED=0)")
        results["persons"] = []

    person_qids = [
        qid for qid in (qid_from_wd_uri(safe_str(row, "person")) for row in results["persons"])
        if qid
    ]
    print(f"    scope persons collected: {len(results['persons'])}")

    print("[2/5] Querying Wikidata for events ...")
    location_event_rows = collect_events(client, location_qids)

    person_event_rows: list[dict] = []
    if EVENTS_FROM_PERSONS_ENABLED and person_qids:
        person_event_rows = collect_events_from_persons(client, person_qids)
    elif not person_qids:
        print("    no person QIDs available for events-from-persons expansion")
    else:
        print("    events-from-persons expansion disabled (EVENTS_FROM_PERSONS_ENABLED=0)")

    merged_event_rows = unique_bindings_by_key(location_event_rows + person_event_rows, "event")[:EVENT_LIMIT]
    if not merged_event_rows:
        print("    no events returned from location/person queries; falling back to seed events")
        seed_values = f"{SEED_ILINDEN_UPRISING} {SEED_KRUSEVO_REPUBLIC}"
        seed_query = f"""
SELECT DISTINCT
  ?event ?eventLabel ?eventDescription
  ?startDate ?endDate
  ?location ?locationLabel
  ?coords
WHERE {{
  VALUES ?event {{ {seed_values} }}
  OPTIONAL {{ ?event wdt:P580 ?startDate }}
  OPTIONAL {{ ?event wdt:P582 ?endDate }}
  OPTIONAL {{ ?event wdt:P276 ?location .
             OPTIONAL {{ ?location wdt:P625 ?coords }} }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "mk,en" . }}
}}
"""
        results["events"] = unique_bindings_by_key(client.query(seed_query), "event")
    else:
        event_qids_raw = [
            qid for qid in (qid_from_wd_uri(safe_str(row, "event")) for row in merged_event_rows)
            if qid
        ]
        if EVENT_DETAILS_ENABLED:
            detailed_event_rows = collect_event_details(client, event_qids_raw)[:EVENT_LIMIT]
            if detailed_event_rows:
                results["events"] = detailed_event_rows
            else:
                results["events"] = merged_event_rows
        else:
            print("    skipping event-details enrichment (EVENT_DETAILS_ENABLED=0)")
            results["events"] = merged_event_rows

    event_qids = [
        qid for qid in (qid_from_wd_uri(safe_str(row, "event")) for row in results["events"])
        if qid
    ]

    print("    augmenting persons from collected events ...")
    event_person_rows = collect_persons_from_events(client, event_qids)
    if event_person_rows:
        results["persons"] = unique_bindings_by_key(results["persons"] + event_person_rows, "person")[:PERSON_LIMIT]
    print(f"    total persons after merge: {len(results['persons'])}")

    person_qids = [
        qid for qid in (qid_from_wd_uri(safe_str(row, "person")) for row in results["persons"])
        if qid
    ]

    print("[3/5] Querying Wikidata for places (from collected entities) ...")
    place_qids = {
        qid for qid in (
            qid_from_wd_uri(safe_str(row, "location")) for row in results["events"]
        ) if qid
    }
    place_qids.update(
        qid for qid in (
            qid_from_wd_uri(safe_str(row, "birthPlace")) for row in results["persons"]
        ) if qid
    )
    place_qids.add("Q221")      # North Macedonia
    place_qids.add("Q103251")   # Macedonia (region)
    place_qids.add("Q83958")    # Ancient Macedonia
    results["places"] = collect_place_details(client, sorted(place_qids))[:PLACE_LIMIT]

    print("[4/5] Querying Wikidata for organizations ...")
    results["organizations"] = collect_organizations(client, event_qids, person_qids)[:ORG_LIMIT]

    print("[5/5] Querying Wikidata for documents ...")
    results["documents"] = collect_documents(client, event_qids, person_qids)[:DOC_LIMIT]

    print("\nConverting to RDF ...")
    graphs: dict[str, Graph] = {}
    converters = {
        "persons": (convert_persons, "persons.ttl"),
        "events": (convert_events, "events.ttl"),
        "places": (convert_places, "places.ttl"),
        "organizations": (convert_organizations, "organizations.ttl"),
        "documents": (convert_documents, "documents.ttl"),
    }

    all_wd_uris: list[str] = []

    for name, (converter, filename) in converters.items():
        g = Graph()
        g.bind("mk", MK)
        g.bind("mko", MKO)
        g.bind("wd", WD)
        g.bind("time", TIME)
        g.bind("geo", GEO)
        g.bind("owl", OWL)

        converter(results[name], g)

        uri_key = name.rstrip("s")
        for row in results[name]:
            for key in [uri_key, "person", "event", "place", "org", "doc"]:
                val = safe_str(row, key)
                if val and "wikidata.org" in val:
                    all_wd_uris.append(val)
                    break

        out_path = os.path.join(OUTPUT_DIR, filename)
        g.serialize(out_path, format="turtle")
        print(f"  -> saved {out_path} ({len(g)} triples)")
        graphs[name] = g

    print("\nBuilding merged graph ...")
    full_graph = Graph()
    add_ontology_triples(full_graph)
    for graph in graphs.values():
        for triple in graph:
            full_graph.add(triple)

    if ENABLE_WIKIPEDIA_ENRICH and WIKIPEDIA_ENRICH_LIMIT > 0:
        unique_uris = list(dict.fromkeys(all_wd_uris))
        enrich_from_mk_wikipedia(full_graph, unique_uris[:WIKIPEDIA_ENRICH_LIMIT], client)

    full_path = os.path.join(OUTPUT_DIR, "mk_history_full.ttl")
    full_graph.serialize(full_path, format="turtle")

    print("\nSummary")
    print(f"  Persons:       {len(results['persons'])}")
    print(f"  Events:        {len(results['events'])}")
    print(f"  Places:        {len(results['places'])}")
    print(f"  Organizations: {len(results['organizations'])}")
    print(f"  Documents:     {len(results['documents'])}")
    print(f"  Total triples: {len(full_graph)}")
    print(f"\nDone. Files saved to {os.path.abspath(OUTPUT_DIR)}")


if __name__ == "__main__":
    main()
