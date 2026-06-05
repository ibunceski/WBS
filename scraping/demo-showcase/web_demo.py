from __future__ import annotations

import csv
import io
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import streamlit as st


DEFAULT_ENDPOINT = "http://localhost:3030/macedonian-history/sparql"
ROOT_DIR = Path(__file__).resolve().parents[1]
QUERIES_DIR = ROOT_DIR / "queries"
MKO_NAMESPACE = "http://macedonian-kg.mk/ontology#"

TYPE_OPTIONS = {
    "All": None,
    "Person": "mko:Person",
    "Historical Event": "mko:HistoricalEvent",
    "Place": "mko:Place",
    "Organization": "mko:Organization",
    "Historical Document": "mko:HistoricalDocument",
    "Period": "mko:Period",
}

PREFIXES = """
PREFIX mko: <http://macedonian-kg.mk/ontology#>
PREFIX mk: <http://macedonian-kg.mk/resource/>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX time: <http://www.w3.org/2006/time#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
""".strip()


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --page-bg: linear-gradient(135deg, #f4efe7 0%, #dfe9f3 100%);
            --panel-bg: rgba(255, 255, 255, 0.82);
            --panel-border: rgba(30, 58, 95, 0.14);
            --heading: #17324d;
            --accent: #9a3412;
            --accent-soft: #fff1e8;
        }
        .stApp {
            background: var(--page-bg);
        }
        .block-container {
            padding-top: 2rem;
            padding-bottom: 2rem;
        }
        h1, h2, h3 {
            color: var(--heading);
            font-family: Georgia, "Palatino Linotype", serif;
            letter-spacing: 0.02em;
        }
        [data-testid="stMetric"] {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 16px;
            padding: 0.8rem;
            box-shadow: 0 12px 30px rgba(23, 50, 77, 0.08);
        }
        .hero-card, .section-card {
            background: var(--panel-bg);
            border: 1px solid var(--panel-border);
            border-radius: 22px;
            padding: 1.2rem 1.4rem;
            box-shadow: 0 16px 40px rgba(23, 50, 77, 0.08);
            backdrop-filter: blur(8px);
        }
        .hero-kicker {
            color: var(--accent);
            font-size: 0.92rem;
            font-weight: 700;
            letter-spacing: 0.08em;
            text-transform: uppercase;
        }
        .muted {
            color: #49627a;
        }
        .small-note {
            font-size: 0.92rem;
            color: #4a5565;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def get_endpoint() -> str:
    return st.session_state.get(
        "fuseki_endpoint",
        os.getenv("FUSEKI_SPARQL_ENDPOINT", DEFAULT_ENDPOINT),
    )


def escape_sparql_string(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


def short_uri(value: str) -> str:
    if "#" in value:
        return value.rsplit("#", 1)[-1]
    return value.rstrip("/").rsplit("/", 1)[-1]


def to_rows(payload: dict[str, Any]) -> list[dict[str, str]]:
    bindings = payload.get("results", {}).get("bindings", [])
    rows: list[dict[str, str]] = []
    for binding in bindings:
        row: dict[str, str] = {}
        for variable, details in binding.items():
            row[variable] = details.get("value", "")
        rows.append(row)
    return rows


def run_sparql(query: str) -> list[dict[str, str]]:
    params = urllib.parse.urlencode({"query": query})
    request = urllib.request.Request(
        f"{get_endpoint()}?{params}",
        headers={"Accept": "application/sparql-results+json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return to_rows(payload)


@st.cache_data(show_spinner=False)
def cached_query(query: str, endpoint: str) -> list[dict[str, str]]:
    del endpoint
    return run_sparql(query)


def safe_query(query: str) -> list[dict[str, str]]:
    try:
        return cached_query(query, get_endpoint())
    except urllib.error.URLError as exc:
        st.error(f"Could not reach Fuseki at `{get_endpoint()}`.\n\n{exc}")
    except Exception as exc:  # noqa: BLE001
        st.error(f"SPARQL query failed.\n\n{exc}")
    return []


def rows_to_csv(rows: list[dict[str, str]]) -> bytes:
    if not rows:
        return b""
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")


def render_header() -> None:
    st.markdown(
        """
        <div class="hero-card">
            <div class="hero-kicker">Web Demo</div>
            <h1>Macedonian History Knowledge Graph</h1>
            <p class="muted">
                A lightweight Streamlit showcase over Apache Jena Fuseki for searching entities,
                exploring historical events, and running prepared SPARQL demos.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_sidebar() -> None:
    st.sidebar.header("Demo Controls")
    endpoint = st.sidebar.text_input("Fuseki SPARQL endpoint", value=get_endpoint())
    st.session_state["fuseki_endpoint"] = endpoint.strip() or DEFAULT_ENDPOINT

    if st.sidebar.button("Check connection", use_container_width=True):
        rows = safe_query(
            f"{PREFIXES}\nSELECT (COUNT(?s) AS ?count) WHERE {{ ?s ?p ?o }}"
        )
        if rows:
            st.sidebar.success("Fuseki is reachable.")

    page = st.sidebar.radio(
        "Page",
        ["Overview", "Search", "Timeline", "Query Examples"],
    )
    st.sidebar.caption("Default dataset: `macedonian-history` on localhost:3030")
    st.sidebar.caption("Tip: keep Fuseki running while presenting.")
    st.session_state["page"] = page


def render_overview() -> None:
    st.subheader("Project Snapshot")
    counts_query = f"""
    {PREFIXES}
    SELECT ?class (COUNT(?entity) AS ?count)
    WHERE {{
      VALUES ?class {{
        mko:Person
        mko:HistoricalEvent
        mko:Place
        mko:Organization
        mko:HistoricalDocument
        mko:Period
      }}
      ?entity rdf:type ?class .
    }}
    GROUP BY ?class
    ORDER BY DESC(?count)
    """
    counts = safe_query(counts_query)

    metric_columns = st.columns(max(len(counts), 1))
    for column, row in zip(metric_columns, counts):
        column.metric(short_uri(row["class"]), row["count"])

    st.markdown(
        """
        <div class="section-card">
            <h3>Why Streamlit fits this demo</h3>
            <p class="small-note">
                It is fast to build, easy to present live, and strong for query-driven interfaces.
                For a course showcase, that matters more than building a heavier frontend stack.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.05, 0.95])

    with left:
        st.markdown("### Suggested showcase flow")
        st.markdown(
            """
            1. Start on the overview to show graph size.
            2. Search for a person, event, or place and open its details.
            3. Switch to the timeline to highlight temporal modeling.
            4. Run a prepared SPARQL query to demonstrate linked data exploration.
            """
        )

    with right:
        health_query = f"""
        {PREFIXES}
        SELECT (COUNT(?entity) AS ?entities) (COUNT(?sameAs) AS ?links)
        WHERE {{
          ?entity rdf:type ?type .
          OPTIONAL {{ ?entity owl:sameAs ?sameAs }}
        }}
        """
        health_rows = safe_query(health_query)
        if health_rows:
            row = health_rows[0]
            a, b = st.columns(2)
            a.metric("Total typed entities", row.get("entities", "0"))
            b.metric("Wikidata links", row.get("links", "0"))


def search_query(term: str, type_iri: str | None) -> list[dict[str, str]]:
    filters = ""
    if type_iri:
        filters = f"FILTER(?type = {type_iri})"

    query = f"""
    {PREFIXES}
    SELECT ?entity ?label ?type
    WHERE {{
      ?entity a ?type ;
              rdfs:label ?label .
      FILTER(CONTAINS(LCASE(STR(?label)), LCASE("{escape_sparql_string(term)}")))
      FILTER(STRSTARTS(STR(?type), "{MKO_NAMESPACE}"))
      {filters}
    }}
    ORDER BY ?label
    LIMIT 50
    """
    return safe_query(query)


def entity_summary_query(entity_uri: str) -> list[dict[str, str]]:
    query = f"""
    {PREFIXES}
    SELECT
      (SAMPLE(?type) AS ?type)
      (SAMPLE(?label) AS ?label)
      (SAMPLE(?wikidata) AS ?wikidata)
      (SAMPLE(?birthDate) AS ?birthDate)
      (SAMPLE(?deathDate) AS ?deathDate)
      (SAMPLE(?startDate) AS ?startDate)
      (SAMPLE(?endDate) AS ?endDate)
      (SAMPLE(?dateCreated) AS ?dateCreated)
      (SAMPLE(?foundingDate) AS ?foundingDate)
      (SAMPLE(?dissolutionDate) AS ?dissolutionDate)
      (SAMPLE(?occupation) AS ?occupation)
      (SAMPLE(?inLanguage) AS ?inLanguage)
    WHERE {{
      BIND(<{entity_uri}> AS ?entity)
      OPTIONAL {{ ?entity a ?type FILTER(STRSTARTS(STR(?type), "{MKO_NAMESPACE}")) }}
      OPTIONAL {{ ?entity rdfs:label ?label }}
      OPTIONAL {{ ?entity owl:sameAs ?wikidata }}
      OPTIONAL {{ ?entity mko:birthDate ?birthDate }}
      OPTIONAL {{ ?entity mko:deathDate ?deathDate }}
      OPTIONAL {{ ?entity mko:startDate ?startDate }}
      OPTIONAL {{ ?entity mko:endDate ?endDate }}
      OPTIONAL {{ ?entity mko:dateCreated ?dateCreated }}
      OPTIONAL {{ ?entity mko:foundingDate ?foundingDate }}
      OPTIONAL {{ ?entity mko:dissolutionDate ?dissolutionDate }}
      OPTIONAL {{ ?entity mko:occupation ?occupation }}
      OPTIONAL {{ ?entity mko:inLanguage ?inLanguage }}
    }}
    """
    return safe_query(query)


def entity_relations_query(entity_uri: str) -> list[dict[str, str]]:
    query = f"""
    {PREFIXES}
    SELECT ?direction ?predicateLabel ?related ?relatedLabel
    WHERE {{
      {{
        BIND("Outgoing" AS ?direction)
        BIND(<{entity_uri}> AS ?entity)
        ?entity ?predicate ?related .
        VALUES ?predicate {{
          mko:bornIn
          mko:headquarteredIn
          mko:tookPlaceIn
          mko:partOf
          mko:memberOf
          mko:participatedIn
          mko:authoredBy
        }}
        OPTIONAL {{ ?predicate rdfs:label ?predicateLabel }}
        OPTIONAL {{ ?related rdfs:label ?relatedLabel }}
        FILTER(isIRI(?related))
      }}
      UNION
      {{
        BIND("Incoming" AS ?direction)
        BIND(<{entity_uri}> AS ?entity)
        ?related ?predicate ?entity .
        VALUES ?predicate {{
          mko:bornIn
          mko:headquarteredIn
          mko:tookPlaceIn
          mko:partOf
          mko:memberOf
          mko:participatedIn
          mko:authoredBy
        }}
        OPTIONAL {{ ?predicate rdfs:label ?predicateLabel }}
        OPTIONAL {{ ?related rdfs:label ?relatedLabel }}
      }}
    }}
    ORDER BY ?direction ?predicateLabel ?relatedLabel
    LIMIT 100
    """
    return safe_query(query)


def render_entity_details(entity_uri: str) -> None:
    summary_rows = entity_summary_query(entity_uri)
    summary = summary_rows[0] if summary_rows else {}
    relations = entity_relations_query(entity_uri)

    title = summary.get("label") or short_uri(entity_uri)
    entity_type = short_uri(summary["type"]) if summary.get("type") else "Entity"
    st.markdown(f"### {title}")
    st.caption(f"{entity_type} | `{entity_uri}`")

    metric_items = [
        ("Birth", summary.get("birthDate", "-")),
        ("Death", summary.get("deathDate", "-")),
        ("Start", summary.get("startDate", "-")),
        ("End", summary.get("endDate", "-")),
    ]
    columns = st.columns(4)
    for column, (label, value) in zip(columns, metric_items):
        column.metric(label, value)

    facts = {
        "Created": summary.get("dateCreated", "-"),
        "Founded": summary.get("foundingDate", "-"),
        "Dissolved": summary.get("dissolutionDate", "-"),
        "Occupation": summary.get("occupation", "-"),
        "Language": summary.get("inLanguage", "-"),
        "Wikidata": summary.get("wikidata", "-"),
    }
    st.json(facts, expanded=False)

    if relations:
        st.markdown("#### Related entities")
        st.dataframe(relations, use_container_width=True, hide_index=True)
    else:
        st.info("No related entity links were found with the current relation set.")


def render_search() -> None:
    st.subheader("Search and Entity Detail")
    search_term = st.text_input(
        "Search by label",
        placeholder="Try 'Ilinden', 'Skopje', or a person name",
    )
    type_label = st.selectbox("Filter by type", list(TYPE_OPTIONS.keys()))

    if not search_term.strip():
        st.info("Enter a search term to find people, events, places, organizations, documents, or periods.")
        return

    results = search_query(search_term.strip(), TYPE_OPTIONS[type_label])
    if not results:
        st.warning("No matching entities were found.")
        return

    for row in results:
        row["typeLabel"] = short_uri(row["type"])
        row["display"] = f'{row["label"]} ({row["typeLabel"]})'

    st.dataframe(
        [{"label": row["label"], "type": row["typeLabel"], "uri": row["entity"]} for row in results],
        use_container_width=True,
        hide_index=True,
    )

    selected = st.selectbox(
        "Inspect one result",
        options=results,
        format_func=lambda item: item["display"],
    )
    render_entity_details(selected["entity"])


def timeline_query() -> list[dict[str, str]]:
    query = f"""
    {PREFIXES}
    SELECT ?event ?label ?start ?end ?placeLabel
    WHERE {{
      ?event a mko:HistoricalEvent .
      OPTIONAL {{ ?event rdfs:label ?label }}
      OPTIONAL {{ ?event mko:startDate ?start }}
      OPTIONAL {{ ?event mko:endDate ?end }}
      OPTIONAL {{
        ?event mko:tookPlaceIn ?place .
        OPTIONAL {{ ?place rdfs:label ?placeLabel }}
      }}
    }}
    ORDER BY ?start ?label
    LIMIT 200
    """
    return safe_query(query)


def render_timeline() -> None:
    st.subheader("Historical Event Timeline")
    rows = timeline_query()
    if not rows:
        return

    for row in rows:
        row["label"] = row.get("label") or short_uri(row["event"])

    chart_rows = [row for row in rows if row.get("start")]
    a, b = st.columns(2)
    a.metric("Total events", str(len(rows)))
    b.metric("Events with start dates", str(len(chart_rows)))

    if chart_rows:
        timeline_spec = {
            "mark": {"type": "circle", "size": 110, "color": "#9a3412"},
            "encoding": {
                "x": {"field": "start", "type": "temporal", "title": "Start date"},
                "y": {
                    "field": "label",
                    "type": "nominal",
                    "sort": {"field": "start", "order": "ascending"},
                    "title": "Event",
                },
                "tooltip": [
                    {"field": "label", "type": "nominal", "title": "Event"},
                    {"field": "start", "type": "temporal", "title": "Start"},
                    {"field": "end", "type": "temporal", "title": "End"},
                    {"field": "placeLabel", "type": "nominal", "title": "Place"},
                ],
            },
        }
        st.vega_lite_chart(chart_rows, timeline_spec, use_container_width=True)

    st.dataframe(rows, use_container_width=True, hide_index=True)


def load_query_examples() -> dict[str, str]:
    examples: dict[str, str] = {}
    for path in sorted(QUERIES_DIR.glob("*.rq")):
        examples[path.name] = path.read_text(encoding="utf-8")
    return examples


def render_query_examples() -> None:
    st.subheader("Prepared SPARQL Queries")
    examples = load_query_examples()
    if not examples:
        st.error("No `.rq` files were found in the queries directory.")
        return

    selected_name = st.selectbox("Choose a query file", list(examples.keys()))
    selected_query = examples[selected_name]
    st.code(selected_query, language="sparql")

    if st.button("Run selected query", use_container_width=True):
        rows = safe_query(selected_query)
        if rows:
            st.success(f"Returned {len(rows)} row(s).")
            st.dataframe(rows, use_container_width=True, hide_index=True)
            st.download_button(
                "Download results as CSV",
                data=rows_to_csv(rows),
                file_name=f"{selected_name.replace('.rq', '')}.csv",
                mime="text/csv",
                use_container_width=True,
            )
        else:
            st.warning("The query returned no rows.")


def main() -> None:
    st.set_page_config(
        page_title="Macedonian History KG Demo",
        layout="wide",
    )
    inject_styles()
    render_sidebar()
    render_header()

    page = st.session_state.get("page", "Overview")
    if page == "Overview":
        render_overview()
    elif page == "Search":
        render_search()
    elif page == "Timeline":
        render_timeline()
    else:
        render_query_examples()


if __name__ == "__main__":
    main()
