from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from . import queries
from .sparql_client import SparqlError, binding_value, bindings, run_query


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
QUERY_DIR = PROJECT_ROOT / "queries"
REPORT_PATH = PROJECT_ROOT / "output" / "kg_report.md"

GRAPH_RELATIONS = [
    "mko:participatedIn",
    "mko:tookPlaceIn",
    "mko:memberOf",
    "mko:ledBy",
    "mko:bornIn",
    "mko:locatedInCountry",
    "mko:authoredBy",
    "mko:headquarteredIn",
    "mko:partOf",
    "oldmko:participatedIn",
    "oldmko:tookPlaceIn",
    "oldmko:memberOf",
    "oldmko:ledBy",
    "oldmko:bornIn",
    "oldmko:locatedInCountry",
    "oldmko:authoredBy",
    "oldmko:headquarteredIn",
    "oldmko:partOf",
]
GRAPH_TYPES = [
    "mko:HistoricalPerson",
    "mko:HistoricalEvent",
    "mko:HistoricalPlace",
    "mko:Organization",
    "mko:HistoricalDocument",
    "oldmko:Person",
    "oldmko:HistoricalEvent",
    "oldmko:Place",
    "oldmko:Organization",
    "oldmko:HistoricalDocument",
]
GRAPH_PREFIXES = """
PREFIX mko: <http://macedonian-history.mk/ontology#>
PREFIX mkr: <http://macedonian-history.mk/resource/>
PREFIX oldmko: <http://macedonian-kg.mk/ontology#>
PREFIX oldmkr: <http://macedonian-kg.mk/resource/>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX time: <http://www.w3.org/2006/time#>
"""

app = FastAPI(title="Macedonian History Knowledge Graph Demo")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def local_name(uri: str) -> str:
    if not uri:
        return ""
    return uri.rstrip("/#").split("/")[-1].split("#")[-1]


def entity_url(uri: str) -> str:
    return f"/entity/{quote(uri, safe='')}"


def wikidata_url(uri: str) -> str:
    if uri.startswith("http://www.wikidata.org/entity/"):
        return uri
    return ""


def wikidata_id(uri: str) -> str:
    if uri.startswith("http://www.wikidata.org/entity/"):
        return local_name(uri)
    return ""


def sparql_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def sparql_iri(uri: str) -> str:
    if not uri.startswith(("http://macedonian-history.mk/", "http://macedonian-kg.mk/", "http://www.wikidata.org/")):
        raise HTTPException(status_code=400, detail="Unsupported entity URI.")
    return f"<{uri.replace('>', '%3E')}>"


def short_type(uri: str) -> str:
    name = local_name(uri)
    if not name:
        return "Entity"
    aliases = {
        "Person": "HistoricalPerson",
        "Place": "HistoricalPlace",
    }
    return aliases.get(name, name)


def any_triple(subject: str, predicate: str, obj: str, graph_var: str) -> str:
    return f"""
    {{
      {subject} {predicate} {obj} .
    }}
    UNION
    {{
      GRAPH {graph_var} {{
        {subject} {predicate} {obj} .
      }}
    }}
    """


def graph_step(left: str, right: str, predicate: str) -> str:
    relations = " ".join(GRAPH_RELATIONS)
    return f"""
    VALUES {predicate} {{ {relations} }}
    {{
      {any_triple(left, predicate, right, predicate.replace("?", "?graph_"))}
    }}
    UNION
    {{
      {any_triple(right, predicate, left, predicate.replace("?", "?graph_reverse_"))}
    }}
    """


def entity_type_filter(entity_var: str = "?entity", type_var: str = "?type") -> str:
    graph_types = " ".join(GRAPH_TYPES)
    return f"""
    VALUES {type_var} {{ {graph_types} }}
    {{
      {entity_var} rdf:type {type_var} .
    }}
    UNION
    {{
      GRAPH ?typeGraph {{
        {entity_var} rdf:type {type_var} .
      }}
    }}
    """


def centered_node_query(center_uri: str, depth: int, limit: int) -> str:
    center = sparql_iri(center_uri)
    branches = [f"{{ BIND({center} AS ?entity) }}"]
    for hop_count in range(1, depth + 1):
        previous = center
        steps = []
        for hop in range(1, hop_count + 1):
            next_node = "?entity" if hop == hop_count else f"?hop{hop_count}_{hop}"
            steps.append(graph_step(previous, next_node, f"?p{hop_count}_{hop}"))
            previous = next_node
        branches.append("{\n" + "\n".join(steps) + "\n}")

    return GRAPH_PREFIXES + f"""
SELECT DISTINCT ?entity
WHERE {{
  {' UNION '.join(branches)}
  FILTER(isIRI(?entity))
  {entity_type_filter()}
}}
LIMIT {limit}
"""


def overview_node_query(limit: int) -> str:
    return GRAPH_PREFIXES + f"""
SELECT ?entity (COUNT(DISTINCT ?neighbor) AS ?degree)
WHERE {{
  {entity_type_filter()}
  OPTIONAL {{
    {graph_step("?entity", "?neighbor", "?edgePredicate")}
    FILTER(isIRI(?neighbor))
  }}
}}
GROUP BY ?entity
ORDER BY DESC(?degree) ?entity
LIMIT {limit}
"""


def overview_edges_query(limit: int) -> str:
    return GRAPH_PREFIXES + f"""
SELECT DISTINCT ?source ?target ?predicate
WHERE {{
  VALUES ?edgePredicate {{ {' '.join(GRAPH_RELATIONS)} }}
  {{
    ?source ?edgePredicate ?target .
  }}
  UNION
  {{
    GRAPH ?edgeGraph {{
      ?source ?edgePredicate ?target .
    }}
  }}
  FILTER(isIRI(?source) && isIRI(?target))
  BIND(REPLACE(STR(?edgePredicate), "^.*[#/]", "") AS ?predicate)
}}
ORDER BY ?predicate ?source ?target
LIMIT {limit}
"""


def node_details_query(uris: list[str]) -> str:
    values = " ".join(sparql_iri(uri) for uri in uris)
    return GRAPH_PREFIXES + f"""
SELECT ?entity
       (SAMPLE(?mkLabelValue) AS ?mkLabel)
       (SAMPLE(?enLabelValue) AS ?enLabel)
       (SAMPLE(?typeValue) AS ?type)
       (SAMPLE(?wikidataValue) AS ?wikidata)
       (SAMPLE(?contextValue) AS ?context)
       (SAMPLE(?startDateValue) AS ?startDate)
       (SAMPLE(?endDateValue) AS ?endDate)
       (SAMPLE(?confidenceValue) AS ?dateConfidence)
WHERE {{
  VALUES ?entity {{ {values} }}

  OPTIONAL {{
    {{
      ?entity rdfs:label ?mkLabelValue .
    }}
    UNION
    {{
      GRAPH ?labelGraphMk {{ ?entity rdfs:label ?mkLabelValue . }}
    }}
    FILTER(LANGMATCHES(LANG(?mkLabelValue), "mk"))
  }}
  OPTIONAL {{
    {{
      ?entity rdfs:label ?enLabelValue .
    }}
    UNION
    {{
      GRAPH ?labelGraphEn {{ ?entity rdfs:label ?enLabelValue . }}
    }}
    FILTER(LANGMATCHES(LANG(?enLabelValue), "en"))
  }}
  OPTIONAL {{
    VALUES ?typeValue {{ {' '.join(GRAPH_TYPES)} }}
    {{
      ?entity rdf:type ?typeValue .
    }}
    UNION
    {{
      GRAPH ?nodeTypeGraph {{ ?entity rdf:type ?typeValue . }}
    }}
  }}
  OPTIONAL {{
    {{
      ?entity owl:sameAs ?wikidataValue .
    }}
    UNION
    {{
      GRAPH ?sameAsGraph {{ ?entity owl:sameAs ?wikidataValue . }}
    }}
    FILTER(STRSTARTS(STR(?wikidataValue), "http://www.wikidata.org/entity/"))
  }}
  OPTIONAL {{
    ?contextValue a time:Interval ;
                  time:hasBeginning/time:inXSDDate ?startDateValue ;
                  time:hasEnd/time:inXSDDate ?endDateValue .
    OPTIONAL {{ ?contextValue mko:dateConfidence ?confidenceValue }}
    GRAPH ?contextValue {{
      ?entity ?contextPredicate ?contextObject .
    }}
  }}
}}
GROUP BY ?entity
"""


def edges_query(uris: list[str]) -> str:
    values = " ".join(sparql_iri(uri) for uri in uris)
    return GRAPH_PREFIXES + f"""
SELECT DISTINCT ?source ?target ?predicate
WHERE {{
  VALUES ?source {{ {values} }}
  VALUES ?target {{ {values} }}
  VALUES ?edgePredicate {{ {' '.join(GRAPH_RELATIONS)} }}
  FILTER(?source != ?target)

  {{
    ?source ?edgePredicate ?target .
  }}
  UNION
  {{
    GRAPH ?edgeGraph {{
      ?source ?edgePredicate ?target .
    }}
  }}
  BIND(REPLACE(STR(?edgePredicate), "^.*[#/]", "") AS ?predicate)
}}
ORDER BY ?source ?predicate ?target
"""


def markdown_to_html(text: str) -> Markup:
    try:
        import markdown

        return Markup(markdown.markdown(text, extensions=["tables", "fenced_code"]))
    except ImportError:
        lines = []
        for line in text.splitlines():
            if line.startswith("# "):
                lines.append(f"<h1>{escape(line[2:])}</h1>")
            elif line.startswith("## "):
                lines.append(f"<h2>{escape(line[3:])}</h2>")
            elif line.strip():
                lines.append(f"<p>{escape(line)}</p>")
        return Markup("\n".join(lines))


templates.env.filters["local_name"] = local_name
templates.env.filters["entity_url"] = entity_url
templates.env.filters["wikidata_url"] = wikidata_url


def rows_from_bindings(raw_rows: list[dict]) -> list[dict[str, str]]:
    return [{key: value.get("value", "") for key, value in row.items()} for row in raw_rows]


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    stats = {
        "entities": "0",
        "events": "0",
        "places": "0",
        "wikidata": "0",
        "coverage": "Unavailable",
    }
    error = None
    try:
        for row in bindings(queries.HOME_STATS):
            stats[binding_value(row, "key")] = binding_value(row, "value")
        if stats.get("start") or stats.get("end"):
            stats["coverage"] = f"{stats.get('start', '?')} to {stats.get('end', '?')}"
    except SparqlError as exc:
        error = str(exc)
    return templates.TemplateResponse(request, "index.html", {"stats": stats, "error": error})


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request):
    return templates.TemplateResponse(request, "search.html")


@app.get("/search/results", response_class=HTMLResponse)
def search_results(request: Request, q: str = ""):
    error = None
    results = []
    if q.strip():
        try:
            query = queries.SEARCH % sparql_literal(q.strip())
            results = rows_from_bindings(bindings(query))
        except SparqlError as exc:
            error = str(exc)
    return templates.TemplateResponse(
        request,
        "partials/search_results.html",
        {"q": q, "results": results, "error": error},
    )


@app.get("/timeline", response_class=HTMLResponse)
def timeline_page(request: Request):
    return templates.TemplateResponse(request, "timeline.html")


@app.get("/timeline/results", response_class=HTMLResponse)
def timeline_results(request: Request, start: str = "", end: str = ""):
    filters = []
    if start.strip():
        filters.append(f'FILTER(!BOUND(?start) || STR(?start) >= "{sparql_literal(start.strip())}")')
    if end.strip():
        filters.append(f'FILTER(!BOUND(?start) || STR(?start) <= "{sparql_literal(end.strip())}")')
    error = None
    results = []
    try:
        results = rows_from_bindings(bindings(queries.TIMELINE % "\n  ".join(filters)))
    except SparqlError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request,
        "partials/timeline_results.html",
        {"results": results, "error": error},
    )


@app.get("/graph", response_class=HTMLResponse)
def graph_page(request: Request):
    return templates.TemplateResponse(request, "graph.html")


@app.get("/api/graph")
def graph_data(
    center_uri: str | None = None,
    depth: int = Query(default=2, ge=1, le=3),
    limit: int = Query(default=900, ge=1, le=900),
):
    try:
        seeded_edges = None
        if center_uri:
            node_rows = rows_from_bindings(bindings(centered_node_query(center_uri, depth, limit)))
            node_uris = [row["entity"] for row in node_rows if row.get("entity")]
        else:
            seed_edges = rows_from_bindings(bindings(overview_edges_query(limit * 4)))
            seed_degrees = {}
            for row in seed_edges:
                for key in ("source", "target"):
                    uri = row.get(key, "")
                    if uri:
                        seed_degrees[uri] = seed_degrees.get(uri, 0) + 1
            node_uris = [
                uri
                for uri, _ in sorted(seed_degrees.items(), key=lambda item: (-item[1], item[0]))[:limit]
            ]
            if not node_uris:
                node_rows = rows_from_bindings(bindings(overview_node_query(limit)))
                node_uris = [row["entity"] for row in node_rows if row.get("entity")]
            else:
                node_set = set(node_uris)
                seeded_edges = [
                    {
                        "source": row.get("source", ""),
                        "target": row.get("target", ""),
                        "predicate": row.get("predicate", ""),
                    }
                    for row in seed_edges
                    if row.get("source") in node_set and row.get("target") in node_set
                ]

        if not node_uris:
            return {"nodes": [], "edges": []}

        detail_rows = rows_from_bindings(bindings(node_details_query(node_uris)))
        detail_by_uri = {row.get("entity", ""): row for row in detail_rows}
        if seeded_edges is not None:
            edges = seeded_edges
        else:
            edges = [
                {
                    "source": row.get("source", ""),
                    "target": row.get("target", ""),
                    "predicate": row.get("predicate", ""),
                }
                for row in rows_from_bindings(bindings(edges_query(node_uris)))
                if row.get("source") and row.get("target")
            ]
    except SparqlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    nodes = []
    degrees = {uri: 0 for uri in node_uris}
    for edge in edges:
        if edge["source"] in degrees:
            degrees[edge["source"]] += 1
        if edge["target"] in degrees:
            degrees[edge["target"]] += 1

    for uri in node_uris:
        row = detail_by_uri.get(uri, {})
        label_mk = row.get("mkLabel", "")
        label_en = row.get("enLabel", "")
        nodes.append(
            {
                "id": uri,
                "label": label_mk or label_en or local_name(uri),
                "labelEn": label_en,
                "type": short_type(row.get("type", "")),
                "wikidataId": wikidata_id(row.get("wikidata", "")),
                "degree": degrees.get(uri, 0),
                "context": row.get("context", ""),
                "startDate": row.get("startDate", ""),
                "endDate": row.get("endDate", ""),
                "dateConfidence": row.get("dateConfidence", ""),
            }
        )

    return {"nodes": nodes, "edges": edges}


@app.get("/api/graph/stats")
def graph_stats():
    query = GRAPH_PREFIXES + f"""
SELECT ?key ?value
WHERE {{
  {{
    SELECT ("HistoricalPerson" AS ?key) (COUNT(DISTINCT ?entity) AS ?value)
    WHERE {{
      VALUES ?type {{ mko:HistoricalPerson oldmko:Person }}
      {{
        ?entity rdf:type ?type .
      }}
      UNION
      {{
        GRAPH ?typeGraph {{ ?entity rdf:type ?type . }}
      }}
    }}
  }}
  UNION
  {{
    SELECT ("HistoricalEvent" AS ?key) (COUNT(DISTINCT ?entity) AS ?value)
    WHERE {{
      VALUES ?type {{ mko:HistoricalEvent oldmko:HistoricalEvent }}
      {{
        ?entity rdf:type ?type .
      }}
      UNION
      {{
        GRAPH ?typeGraph {{ ?entity rdf:type ?type . }}
      }}
    }}
  }}
  UNION
  {{
    SELECT ("HistoricalPlace" AS ?key) (COUNT(DISTINCT ?entity) AS ?value)
    WHERE {{
      VALUES ?type {{ mko:HistoricalPlace oldmko:Place }}
      {{
        ?entity rdf:type ?type .
      }}
      UNION
      {{
        GRAPH ?typeGraph {{ ?entity rdf:type ?type . }}
      }}
    }}
  }}
  UNION
  {{
    SELECT ("Organization" AS ?key) (COUNT(DISTINCT ?entity) AS ?value)
    WHERE {{
      VALUES ?type {{ mko:Organization oldmko:Organization }}
      {{
        ?entity rdf:type ?type .
      }}
      UNION
      {{
        GRAPH ?typeGraph {{ ?entity rdf:type ?type . }}
      }}
    }}
  }}
  UNION
  {{
    SELECT ("HistoricalDocument" AS ?key) (COUNT(DISTINCT ?entity) AS ?value)
    WHERE {{
      VALUES ?type {{ mko:HistoricalDocument oldmko:HistoricalDocument }}
      {{
        ?entity rdf:type ?type .
      }}
      UNION
      {{
        GRAPH ?typeGraph {{ ?entity rdf:type ?type . }}
      }}
    }}
  }}
  UNION
  {{
    SELECT ("total_edges" AS ?key) (COUNT(*) AS ?value)
    WHERE {{
      {{
        SELECT DISTINCT ?source ?edgePredicate ?target
        WHERE {{
          VALUES ?edgePredicate {{ {' '.join(GRAPH_RELATIONS)} }}
          {{
            ?source ?edgePredicate ?target .
          }}
          UNION
          {{
            GRAPH ?edgeGraph {{ ?source ?edgePredicate ?target . }}
          }}
          FILTER(isIRI(?source) && isIRI(?target))
        }}
      }}
    }}
  }}
}}
"""
    try:
        stats = {row.get("key", ""): int(row.get("value") or 0) for row in rows_from_bindings(bindings(query))}
    except SparqlError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    return {
        "HistoricalPerson": stats.get("HistoricalPerson", 0),
        "HistoricalEvent": stats.get("HistoricalEvent", 0),
        "HistoricalPlace": stats.get("HistoricalPlace", 0),
        "Organization": stats.get("Organization", 0),
        "HistoricalDocument": stats.get("HistoricalDocument", 0),
        "total_edges": stats.get("total_edges", 0),
    }


@app.get("/entity/{encoded_uri:path}", response_class=HTMLResponse)
def entity_detail(request: Request, encoded_uri: str):
    uri = unquote(encoded_uri)
    if not uri.startswith(("http://macedonian-history.mk/", "http://macedonian-kg.mk/", "http://www.wikidata.org/")):
        return templates.TemplateResponse(
            request,
            "entity.html",
            {"uri": uri, "error": "Unsupported entity URI.", "entity": None},
            status_code=400,
        )
    error = None
    entity = None
    related = []
    triples = []
    try:
        detail_rows = rows_from_bindings(bindings(queries.ENTITY_DETAILS % uri))
        entity = detail_rows[0] if detail_rows else {}
        related = rows_from_bindings(bindings(queries.ENTITY_RELATED % uri))
        triples = rows_from_bindings(bindings(queries.ENTITY_TRIPLES % uri))
    except SparqlError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request,
        "entity.html",
        {"uri": uri, "entity": entity, "related": related, "triples": triples, "error": error},
    )


@app.get("/queries", response_class=HTMLResponse)
def queries_page(request: Request):
    query_files = sorted(path.name for path in QUERY_DIR.glob("*.rq"))
    selected = query_files[0] if query_files else ""
    query_text = (QUERY_DIR / selected).read_text(encoding="utf-8") if selected else ""
    return templates.TemplateResponse(
        request,
        "queries.html",
        {"query_files": query_files, "selected": selected, "query_text": query_text},
    )


@app.get("/queries/select", response_class=HTMLResponse)
def select_query(request: Request, file: str):
    query_files = sorted(path.name for path in QUERY_DIR.glob("*.rq"))
    if file not in query_files:
        query_text = ""
        selected = ""
    else:
        selected = file
        query_text = (QUERY_DIR / file).read_text(encoding="utf-8")
    return templates.TemplateResponse(
        request,
        "partials/query_results.html",
        {"selected": selected, "query_text": query_text, "rows": [], "headers": [], "error": None},
    )


@app.post("/queries/run", response_class=HTMLResponse)
async def run_query_example(request: Request):
    form = await request.form()
    query_text = str(form.get("query", ""))
    selected = str(form.get("selected", ""))
    rows = []
    headers = []
    error = None
    try:
        data = run_query(query_text)
        headers = data.get("head", {}).get("vars", [])
        rows = rows_from_bindings(data.get("results", {}).get("bindings", []))
    except SparqlError as exc:
        error = str(exc)
    return templates.TemplateResponse(
        request,
        "partials/query_results.html",
        {"selected": selected, "query_text": query_text, "rows": rows, "headers": headers, "error": error},
    )


@app.get("/report", response_class=HTMLResponse)
def report_page(request: Request):
    if REPORT_PATH.exists():
        report_html = markdown_to_html(REPORT_PATH.read_text(encoding="utf-8"))
        missing = False
    else:
        report_html = Markup("")
        missing = True
    return templates.TemplateResponse(
        request,
        "report.html",
        {"report_html": report_html, "missing": missing, "report_path": REPORT_PATH},
    )
