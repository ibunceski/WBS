from pathlib import Path
from urllib.parse import quote, unquote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from . import queries
from .sparql_client import SparqlError, binding_value, bindings, run_query


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
QUERY_DIR = PROJECT_ROOT / "queries"
REPORT_PATH = PROJECT_ROOT / "scripts" / "output" / "kg_report.md"

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


def sparql_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


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


@app.get("/entity/{encoded_uri:path}", response_class=HTMLResponse)
def entity_detail(request: Request, encoded_uri: str):
    uri = unquote(encoded_uri)
    if not uri.startswith(("http://macedonian-kg.mk/", "http://www.wikidata.org/")):
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
