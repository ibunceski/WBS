"""
Ilinden Period Knowledge Graph â€” Data Collector
================================================
Collects structured data about the Ilinden Uprising period (1893â€“1908)
from Wikidata, then converts everything to RDF (Turtle format) ready
to load into Apache Jena Fuseki or GraphDB.

Entities collected:
  - Persons  (VMRO members, voivodes, fighters, Ottoman officials)
  - Events   (battles, congresses, declarations)
  - Places   (cities, villages, battle sites)
  - Orgs     (VMRO, Äeti, Ottoman units, Exarchate)
  - Documents (manifestos, statutes, proclamations â€” metadata only)

Usage:
  pip install rdflib requests
  python main.py

Output files (in ./output/):
  persons.ttl, events.ttl, places.ttl, organizations.ttl, documents.ttl
  ilinden_full.ttl  â† merged graph, ready to load into triple store
"""

import os
import random
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests
from rdflib import (
    Graph, ConjunctiveGraph,
    Namespace, URIRef, Literal, BNode,
    XSD, RDF, RDFS, OWL
)
from rdflib.namespace import SKOS, FOAF

# â”€â”€ Namespace declarations â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

MK   = Namespace("http://macedonian-kg.mk/resource/")
MKO  = Namespace("http://macedonian-kg.mk/ontology#")
WD   = Namespace("http://www.wikidata.org/entity/")
WDT  = Namespace("http://www.wikidata.org/prop/direct/")
TIME = Namespace("http://www.w3.org/2006/time#")
GEO  = Namespace("http://www.opengis.net/ont/geosparql#")
CRM  = Namespace("http://www.cidoc-crm.org/cidoc-crm/")

WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"
CONTACT_EMAIL = os.getenv("WIKIDATA_CONTACT_EMAIL", "set-your-email@example.com").strip()
USER_AGENT = f"IlindenKG/1.1 (university project; contact: {CONTACT_EMAIL})"
DEFAULT_TIMEOUT_SECONDS = 30
WIKIDATA_MIN_DELAY_SECONDS = float(os.getenv("WIKIDATA_MIN_DELAY_SECONDS", "3.0"))
WIKIPEDIA_MIN_DELAY_SECONDS = float(os.getenv("WIKIPEDIA_MIN_DELAY_SECONDS", "1.5"))
WIKIPEDIA_ENRICH_LIMIT = int(os.getenv("WIKIPEDIA_ENRICH_LIMIT", "20"))
MAX_BACKOFF_SECONDS = 120.0

OUTPUT_DIR = "./output"


# â”€â”€ Core Wikidata IDs for the Ilinden period â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# These seed the queries.  All others are discovered through relationships.

ILINDEN_UPRISING = "wd:Q1145682"  # Ilinden-Preobrazhenie Uprising
KRUSEVO_REPUBLIC = "wd:Q1771831"  # KruÅ¡evo Republic
VMRO_HISTORICAL  = "wd:Q488297"   # Internal Macedonian Revolutionary Organization
SMILEVO_CONGRESS = "wd:Q12294079" # Smilevo Congress, 1903
KRUSEVO_CITY     = "wd:Q157034"   # KruÅ¡evo
BITOLA           = "wd:Q157246"   # Bitola (Monastir)
NORTH_MACEDONIA  = "wd:Q221"      # North Macedonia (modern state, used for geo filter)


# â”€â”€ SPARQL helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

class WikidataClient:
    """HTTP client with throttling + retry handling for 429/5xx responses."""
    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "application/sparql-results+json, application/json;q=0.9",
            "Accept-Language": "mk,en;q=0.9",
        })
        self.last_request_started_at = 0.0
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
        # Exponential backoff + jitter so retries do not synchronize.
        base_wait = min((2 ** attempt), MAX_BACKOFF_SECONDS)
        jitter = random.uniform(0.0, 1.0)
        return min(base_wait + jitter, MAX_BACKOFF_SECONDS)
    def get_json(
        self,
        url: str,
        *,
        params: dict | None = None,
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
    def query(self, sparql: str, retries: int = 6) -> list[dict]:
        payload = self.get_json(
            WIKIDATA_SPARQL,
            params={"query": sparql, "format": "json"},
            retries=retries,
            min_delay_seconds=WIKIDATA_MIN_DELAY_SECONDS,
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            context="Wikidata SPARQL query",
        )
        if not payload:
            return []
        bindings = payload.get("results", {}).get("bindings", [])
        print(f"  ok  {len(bindings)} results")
        return bindings

# â”€â”€ Query definitions â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

QUERY_PERSONS = """
SELECT DISTINCT
  ?person ?personLabel ?personDescription
  ?birthDate ?deathDate
  ?birthPlace ?birthPlaceLabel
  ?nationality ?nationalityLabel
  ?occupation ?occupationLabel
WHERE {
  {
    ?person wdt:P1344 wd:Q1145682 .      # participated in Ilinden Uprising
  } UNION {
    ?person wdt:P463 wd:Q488297 .       # member of VMRO
  } UNION {
    ?person wdt:P1344 wd:Q1771831 .      # participated in KruÅ¡evo Republic
  }
  ?person wdt:P31 wd:Q5 .               # must be a human

  OPTIONAL { ?person wdt:P569 ?birthDate }
  OPTIONAL { ?person wdt:P570 ?deathDate }
  OPTIONAL { ?person wdt:P19  ?birthPlace }
  OPTIONAL { ?person wdt:P27  ?nationality }
  OPTIONAL { ?person wdt:P106 ?occupation }

  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "mk,en,bg,sr" .
  }
}
ORDER BY ?personLabel
LIMIT 300
"""

QUERY_EVENTS = """
SELECT DISTINCT
  ?event ?eventLabel ?eventDescription
  ?startDate ?endDate
  ?location ?locationLabel
  ?coords
WHERE {
  {
    ?event wdt:P361 wd:Q1145682 .        # part of Ilinden Uprising
  } UNION {
    wd:Q1145682 wdt:P527 ?event .         # Ilinden has part
  } UNION {
    ?event wdt:P361 wd:Q1771831 .         # part of KruÅ¡evo Republic events
  } UNION {
    ?event wdt:P361 wd:Q488297 .         # part of VMRO history
  }
  FILTER(?event != wd:Q1145682)
  FILTER(?event != wd:Q1771831)
  ?event wdt:P31/wdt:P279* wd:Q13418847 .  # must be a historical event

  OPTIONAL { ?event wdt:P580 ?startDate }
  OPTIONAL { ?event wdt:P582 ?endDate }
  FILTER(!BOUND(?startDate) || (YEAR(?startDate) >= 1890 && YEAR(?startDate) <= 1910))
  OPTIONAL {
    ?event wdt:P276 ?location .
    OPTIONAL { ?location wdt:P625 ?coords }
  }
  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "mk,en" .
  }
}
ORDER BY ?startDate
LIMIT 200
"""

QUERY_PLACES = """
SELECT DISTINCT
  ?place ?placeLabel ?placeDescription
  ?coords
  ?placeType ?placeTypeLabel
  ?country ?countryLabel
WHERE {
  {
    ?event wdt:P361 wd:Q1145682 .
    ?event wdt:P276 ?place .            # battle / event locations
  } UNION {
    ?person wdt:P1344 wd:Q1145682 .
    ?person wdt:P19 ?place .            # birthplaces of participants
  } UNION {
    VALUES ?place {
      wd:Q157034   # KruÅ¡evo
      wd:Q157246   # Bitola
      wd:Q221      # North Macedonia (territory)
      wd:Q3136412  # Smilevo
      wd:Q202272   # Lerin / Florina
      wd:Q147243   # Å tip
      wd:Q157050   # Veles
      wd:Q1223508  # Ohrid
    }
  }
  ?place wdt:P31 ?placeType .
  OPTIONAL { ?place wdt:P625 ?coords }
  OPTIONAL { ?place wdt:P17  ?country }

  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "mk,en" .
  }
}
"""

QUERY_ORGANIZATIONS = """
SELECT DISTINCT
  ?org ?orgLabel ?orgDescription
  ?foundingDate ?dissolutionDate
  ?headquarters ?headquartersLabel
  ?orgType ?orgTypeLabel
WHERE {
  {
    VALUES ?org {
      wd:Q488297    # VMRO
      wd:Q1771831    # KruÅ¡evo Republic (also an org/state)
    }
  } UNION {
    ?org wdt:P361 wd:Q1145682 .          # part of Ilinden context
    ?org wdt:P31/wdt:P279* wd:Q43229 . # instance of organisation
  } UNION {
    ?person wdt:P1344 wd:Q1145682 .
    ?person wdt:P463 ?org .              # orgs that participants were members of
    ?org wdt:P31/wdt:P279* wd:Q43229 .
  }

  OPTIONAL { ?org wdt:P571 ?foundingDate }
  OPTIONAL { ?org wdt:P576 ?dissolutionDate }
  OPTIONAL { ?org wdt:P159 ?headquarters }
  OPTIONAL { ?org wdt:P31  ?orgType }

  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "mk,en" .
  }
}
LIMIT 100
"""

# Documents are sparse in Wikidata; we collect metadata here and add full text later
QUERY_DOCUMENTS = """
SELECT DISTINCT
  ?doc ?docLabel ?docDescription
  ?date
  ?author ?authorLabel
  ?language ?languageLabel
WHERE {
  {
    ?doc wdt:P361 wd:Q1145682 .          # document part of Ilinden context
    ?doc wdt:P31/wdt:P279* wd:Q49848 . # instance of document
  } UNION {
    ?doc wdt:P50 ?author .
    ?author wdt:P1344 wd:Q1145682 .      # written by an Ilinden participant
    ?doc wdt:P31/wdt:P279* wd:Q49848 .
  }
  OPTIONAL { ?doc wdt:P577 ?date }
  OPTIONAL { ?doc wdt:P50  ?author }
  OPTIONAL { ?doc wdt:P407 ?language }

  SERVICE wikibase:label {
    bd:serviceParam wikibase:language "mk,en" .
  }
}
LIMIT 100
"""


# â”€â”€ RDF conversion helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def safe_str(binding: dict, key: str) -> str | None:
    """Extract string value from a SPARQL binding safely."""
    val = binding.get(key, {}).get("value")
    return val if val else None


def wikidata_uri_to_local(wd_uri: str) -> URIRef:
    """Convert a full Wikidata URI to a local MK resource URI."""
    qid = wd_uri.rstrip("/").split("/")[-1]
    return MK[qid]


def add_label(g: Graph, subject: URIRef, label_str: str) -> None:
    """Add rdfs:label in Macedonian and English."""
    if label_str:
        g.add((subject, RDFS.label, Literal(label_str, lang="mk")))


def normalize_wikidata_date(date_str: str) -> tuple[str | None, URIRef | None]:
    """
    Normalize Wikidata time lexicals to safe XSD temporal literals.
    Examples:
      +1903-08-02T00:00:00Z -> ("1903-08-02", xsd:date)
      +1903-08-00T00:00:00Z -> ("1903-08", xsd:gYearMonth)
      +1903-00-00T00:00:00Z -> ("1903", xsd:gYear)
    """
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


def add_date_literal(g: Graph, subject: URIRef, predicate: URIRef,
                     date_str: str) -> None:
    """Add a date triple while preserving available precision safely."""
    if not date_str:
        return

    lexical, datatype = normalize_wikidata_date(date_str)
    if lexical and datatype:
        g.add((subject, predicate, Literal(lexical, datatype=datatype)))
        return

    # Fallback as plain literal to avoid rdflib datatype parse errors.
    g.add((subject, predicate, Literal(date_str)))


# â”€â”€ Per-entity-type converters â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def convert_persons(rows: list[dict], g: Graph) -> None:
    print(f"\n  Converting {len(rows)} persons to RDF ...")
    for row in rows:
        wd_uri  = safe_str(row, "person")
        if not wd_uri:
            continue
        subject = wikidata_uri_to_local(wd_uri)

        g.add((subject, RDF.type,          MKO.Person))
        g.add((subject, RDF.type,          MKO.IlindenFigure))
        g.add((subject, OWL.sameAs,        URIRef(wd_uri)))

        label = safe_str(row, "personLabel")
        add_label(g, subject, label)

        desc = safe_str(row, "personDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        add_date_literal(g, subject, MKO.birthDate,  safe_str(row, "birthDate"))
        add_date_literal(g, subject, MKO.deathDate,  safe_str(row, "deathDate"))

        bp_uri = safe_str(row, "birthPlace")
        if bp_uri:
            g.add((subject, MKO.bornIn, wikidata_uri_to_local(bp_uri)))

        occ = safe_str(row, "occupationLabel")
        if occ:
            g.add((subject, MKO.occupation, Literal(occ, lang="en")))


def convert_events(rows: list[dict], g: Graph) -> None:
    print(f"\n  Converting {len(rows)} events to RDF ...")
    for row in rows:
        wd_uri  = safe_str(row, "event")
        if not wd_uri:
            continue
        subject = wikidata_uri_to_local(wd_uri)

        g.add((subject, RDF.type,   MKO.HistoricalEvent))
        g.add((subject, RDF.type,   MKO.IlindenEvent))
        g.add((subject, OWL.sameAs, URIRef(wd_uri)))

        # Link to the umbrella event
        g.add((subject, MKO.partOf, URIRef("http://macedonian-kg.mk/resource/Q1145682")))

        add_label(g, subject, safe_str(row, "eventLabel"))
        desc = safe_str(row, "eventDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        add_date_literal(g, subject, MKO.startDate, safe_str(row, "startDate"))
        add_date_literal(g, subject, MKO.endDate,   safe_str(row, "endDate"))

        loc_uri = safe_str(row, "location")
        if loc_uri:
            g.add((subject, MKO.tookPlaceIn, wikidata_uri_to_local(loc_uri)))

        # Temporal named-graph anchor (for temporal KG queries)
        start = safe_str(row, "startDate")
        if start:
            interval_node = BNode()
            g.add((subject,        TIME.hasTime,     interval_node))
            g.add((interval_node,  RDF.type,          TIME.Interval))
            g.add((interval_node,  TIME.hasBeginning,
                   Literal(start[:10], datatype=XSD.date)))
            end = safe_str(row, "endDate")
            if end:
                g.add((interval_node, TIME.hasEnd,
                       Literal(end[:10], datatype=XSD.date)))


def convert_places(rows: list[dict], g: Graph) -> None:
    print(f"\n  Converting {len(rows)} places to RDF ...")
    seen = set()
    for row in rows:
        wd_uri = safe_str(row, "place")
        if not wd_uri or wd_uri in seen:
            continue
        seen.add(wd_uri)
        subject = wikidata_uri_to_local(wd_uri)

        g.add((subject, RDF.type,   MKO.Place))
        g.add((subject, OWL.sameAs, URIRef(wd_uri)))

        add_label(g, subject, safe_str(row, "placeLabel"))
        desc = safe_str(row, "placeDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        coords = safe_str(row, "coords")
        if coords:
            # Wikidata returns Point(lon lat) in WKT
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

        g.add((subject, RDF.type,   MKO.Organization))
        g.add((subject, OWL.sameAs, URIRef(wd_uri)))

        add_label(g, subject, safe_str(row, "orgLabel"))
        desc = safe_str(row, "orgDescription")
        if desc:
            g.add((subject, RDFS.comment, Literal(desc, lang="en")))

        add_date_literal(g, subject, MKO.foundingDate,    safe_str(row, "foundingDate"))
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

        g.add((subject, RDF.type,   MKO.HistoricalDocument))
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


# â”€â”€ Ontology triples (lightweight schema) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def add_ontology_triples(g: Graph) -> None:
    """
    Add the core class and property definitions for the Ilinden KG.
    A full OWL ontology should be designed in ProtÃ©gÃ©; this is a
    minimal bootstrap for the collected data to be self-describing.
    """
    classes = [
        (MKO.Person,              "Person",              "A historical individual"),
        (MKO.IlindenFigure,       "Ilinden figure",      "Person active in the Ilinden period"),
        (MKO.HistoricalEvent,     "Historical event",    "A discrete historical occurrence"),
        (MKO.IlindenEvent,        "Ilinden event",       "Event within the 1893â€“1908 period"),
        (MKO.Place,               "Place",               "A geographic location"),
        (MKO.Organization,        "Organization",        "A collective actor"),
        (MKO.HistoricalDocument,  "Historical document", "A written primary source"),
        (MKO.Period,              "Period",              "A named historical interval"),
    ]
    for cls, label, comment in classes:
        g.add((cls, RDF.type,     OWL.Class))
        g.add((cls, RDFS.label,   Literal(label, lang="en")))
        g.add((cls, RDFS.comment, Literal(comment, lang="en")))

    # Sub-class hierarchy
    g.add((MKO.IlindenFigure, RDFS.subClassOf, MKO.Person))
    g.add((MKO.IlindenEvent,  RDFS.subClassOf, MKO.HistoricalEvent))

    # Object properties
    props = [
        (MKO.partOf,             "part of"),
        (MKO.participatedIn,     "participated in"),
        (MKO.memberOf,           "member of"),
        (MKO.tookPlaceIn,        "took place in"),
        (MKO.bornIn,             "born in"),
        (MKO.headquarteredIn,    "headquartered in"),
        (MKO.authoredBy,         "authored by"),
        (MKO.locatedInCountry,   "located in country"),
    ]
    for prop, label in props:
        g.add((prop, RDF.type,   OWL.ObjectProperty))
        g.add((prop, RDFS.label, Literal(label, lang="en")))

    # Datatype properties
    dt_props = [
        # Temporal literals may be xsd:date, xsd:gYearMonth, or xsd:gYear.
        (MKO.birthDate,        "birth date",        RDFS.Literal),
        (MKO.deathDate,        "death date",        RDFS.Literal),
        (MKO.startDate,        "start date",        RDFS.Literal),
        (MKO.endDate,          "end date",          RDFS.Literal),
        (MKO.foundingDate,     "founding date",     RDFS.Literal),
        (MKO.dissolutionDate,  "dissolution date",  RDFS.Literal),
        (MKO.dateCreated,      "date created",      RDFS.Literal),
        (MKO.occupation,       "occupation",        XSD.string),
        (MKO.inLanguage,       "in language",       XSD.string),
    ]
    for prop, label, range_type in dt_props:
        g.add((prop, RDF.type,       OWL.DatatypeProperty))
        g.add((prop, RDFS.label,     Literal(label, lang="en")))
        g.add((prop, RDFS.range,     range_type))

    # Namespace declarations
    g.bind("mk",   MK)
    g.bind("mko",  MKO)
    g.bind("wd",   WD)
    g.bind("time", TIME)
    g.bind("geo",  GEO)
    g.bind("owl",  OWL)
    g.bind("skos", SKOS)

    # The umbrella Ilinden Uprising instance
    uprising = MK["Q1145682"]
    g.add((uprising, RDF.type,       MKO.HistoricalEvent))
    g.add((uprising, RDFS.label,     Literal("Ð˜Ð»Ð¸Ð½Ð´ÐµÐ½ÑÐºÐ¾ Ð²Ð¾ÑÑ‚Ð°Ð½Ð¸Ðµ", lang="mk")))
    g.add((uprising, RDFS.label,     Literal("Ilinden Uprising", lang="en")))
    g.add((uprising, OWL.sameAs,     URIRef("http://www.wikidata.org/entity/Q1145682")))
    g.add((uprising, MKO.startDate,  Literal("1903-08-02", datatype=XSD.date)))
    g.add((uprising, MKO.endDate,    Literal("1903-10-03", datatype=XSD.date)))

    # The Ilinden period (broader temporal context)
    period = MK["IlindenPeriod"]
    g.add((period, RDF.type,       MKO.Period))
    g.add((period, RDFS.label,     Literal("Ð˜Ð»Ð¸Ð½Ð´ÐµÐ½ Ð¿ÐµÑ€Ð¸Ð¾Ð´", lang="mk")))
    g.add((period, RDFS.label,     Literal("Ilinden period", lang="en")))
    g.add((period, MKO.startDate,  Literal("1893-10-23", datatype=XSD.date)))
    g.add((period, MKO.endDate,    Literal("1908-07-23", datatype=XSD.date)))
    time_interval = BNode()
    g.add((period,         TIME.hasTime,     time_interval))
    g.add((time_interval,  RDF.type,          TIME.Interval))
    g.add((time_interval,  TIME.hasBeginning,
           Literal("1893-10-23", datatype=XSD.date)))
    g.add((time_interval,  TIME.hasEnd,
           Literal("1908-07-23", datatype=XSD.date)))


# â”€â”€ Wikipedia enrichment (Macedonian) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def enrich_from_mk_wikipedia(g: Graph, entity_uris: list[str], client: WikidataClient) -> None:
    """
    For each entity that has a Macedonian Wikipedia article,
    fetch the abstract (first paragraph) and add it as a mk-language comment.
    Uses the Wikipedia REST API â€” no scraping needed.
    """
    print("\n  Enriching from Macedonian Wikipedia ...")
    enriched = 0

    for wd_uri in entity_uris:
        qid = wd_uri.rstrip("/").split("/")[-1]
        subject = MK[qid]

        # Ask Wikidata for the Macedonian Wikipedia sitelink
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
            sitelinks  = entity_data.get("sitelinks", {})
            mk_link    = sitelinks.get("mkwiki", {}).get("title")

            if not mk_link:
                continue

            # Fetch the abstract from the Macedonian Wikipedia REST API
            title_encoded = requests.utils.quote(mk_link)
            wiki_url = f"https://mk.wikipedia.org/api/rest_v1/page/summary/{title_encoded}"
            wiki_data = client.get_json(
                wiki_url,
                retries=5,
                min_delay_seconds=WIKIPEDIA_MIN_DELAY_SECONDS,
                timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
                context=f"Wikipedia summary lookup for {qid}",
            )
            if not wiki_data:
                continue

            extract   = wiki_data.get("extract", "")
            wiki_page = wiki_data.get("content_urls", {}).get("desktop", {}).get("page", "")
            if extract:
                g.add((subject, RDFS.comment,   Literal(extract[:1000], lang="mk")))
            if wiki_page:
                g.add((subject, RDFS.seeAlso,   URIRef(wiki_page)))
            enriched += 1

        except Exception as exc:
            print(f"    âš   Wikipedia enrichment failed for {qid}: {exc}")

    print(f"  âœ“  enriched {enriched} entities from mk.wikipedia.org")


# â”€â”€ Main â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    client = WikidataClient()

    if CONTACT_EMAIL == "set-your-email@example.com":
        print("WARNING: set WIKIDATA_CONTACT_EMAIL to your real email for better API compliance.")

    print("â•”â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•—")
    print("â•‘  Ilinden Period Knowledge Graph â€” Data Collector         â•‘")
    print(f"â•‘  Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                          â•‘")
    print("â•šâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n")

    # â”€â”€ 1. Run all SPARQL queries â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    queries = {
        "persons":       QUERY_PERSONS,
        "events":        QUERY_EVENTS,
        "places":        QUERY_PLACES,
        "organizations": QUERY_ORGANIZATIONS,
        "documents":     QUERY_DOCUMENTS,
    }

    results: dict[str, list[dict]] = {}
    for idx, (name, sparql) in enumerate(queries.items(), start=1):
        print(f"[{idx}/{len(queries)}] Querying Wikidata for {name} ...")
        results[name] = client.query(sparql)

    # â”€â”€ 2. Convert each entity type to RDF â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print("\nâ”€â”€ Converting to RDF â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€")

    graphs: dict[str, Graph] = {}
    converters = {
        "persons":       (convert_persons,       "persons.ttl"),
        "events":        (convert_events,         "events.ttl"),
        "places":        (convert_places,         "places.ttl"),
        "organizations": (convert_organizations,  "organizations.ttl"),
        "documents":     (convert_documents,      "documents.ttl"),
    }

    all_wd_uris: list[str] = []  # collected for Wikipedia enrichment

    for name, (converter, filename) in converters.items():
        g = Graph()
        g.bind("mk",  MK);  g.bind("mko",  MKO)
        g.bind("wd",  WD);  g.bind("time", TIME)
        g.bind("geo", GEO); g.bind("owl",  OWL)
        converter(results[name], g)

        # Collect Wikidata URIs for Wikipedia enrichment
        uri_key = name[:-1]  if name != "organizations" else "org"
        uri_key = name.rstrip("s")  # personsâ†’person, eventsâ†’event, etc.
        for row in results[name]:
            for key in [uri_key, "person", "event", "place", "org", "doc"]:
                val = safe_str(row, key)
                if val and "wikidata.org" in val:
                    all_wd_uris.append(val)
                    break

        out_path = os.path.join(OUTPUT_DIR, filename)
        g.serialize(out_path, format="turtle")
        print(f"  â†’ saved {out_path}  ({len(g)} triples)")
        graphs[name] = g

    # â”€â”€ 3. Build the merged full graph â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    print("\nâ”€â”€ Building merged graph â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€")
    full_graph = Graph()
    add_ontology_triples(full_graph)
    for g in graphs.values():
        for triple in g:
            full_graph.add(triple)

    # â”€â”€ 4. Wikipedia enrichment â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    unique_uris = list(dict.fromkeys(all_wd_uris))
    enrich_from_mk_wikipedia(
        full_graph,
        unique_uris[:WIKIPEDIA_ENRICH_LIMIT],
        client,
    )

    # â”€â”€ 5. Save the full merged graph â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    full_path = os.path.join(OUTPUT_DIR, "ilinden_full.ttl")
    full_graph.serialize(full_path, format="turtle")

    print("\nâ”€â”€ Summary â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€")
    print(f"  Persons:       {len(results['persons'])}")
    print(f"  Events:        {len(results['events'])}")
    print(f"  Places:        {len(results['places'])}")
    print(f"  Organizations: {len(results['organizations'])}")
    print(f"  Documents:     {len(results['documents'])}")
    print(f"  Total triples: {len(full_graph)}")
    print(f"\n  âœ…  All files saved to {os.path.abspath(OUTPUT_DIR)}/")
    print(f"  âœ…  Load ilinden_full.ttl into Fuseki to start querying.\n")


if __name__ == "__main__":
    main()

