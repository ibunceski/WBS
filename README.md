# Macedonian History Knowledge Graph Demo

This project collects data about Macedonian history, converts it to RDF/Turtle, loads it into Apache Jena Fuseki, and exposes a small web demo for searching, browsing, and running SPARQL queries.

Project topic:

> Development of a knowledge graph for Macedonian history, linking it with international historical databases, and modeling it as a temporal knowledge graph.

## What Is Included

- `scripts/main.py` - collects data from Wikidata/Wikipedia and generates RDF files.
- `scripts/validate_clean.py` - validates and cleans the generated RDF graph.
- `scripts/graph_report.py` - creates a small report about the graph.
- `output/mk_history_clean.ttl` - the cleaned graph that should be loaded into Fuseki.
- `queries/` - example SPARQL queries.
- `app/` - FastAPI web demo.

## Requirements

Install these before running the demo:

- Python 3.10+
- Java 11+ or Java 17+
- Apache Jena Fuseki

Python packages:

```powershell
pip install -r requirements.txt
```

If you are using the existing virtual environment:

```powershell
.\.venv\Scripts\activate
pip install rdflib requests python-dotenv fastapi uvicorn jinja2 markupsafe python-multipart markdown
```

## 1. Generate The RDF Graph

If `output/mk_history_clean.ttl` already exists, you can skip this section and go directly to Fuseki.

Run the collector:

```powershell
python scripts\main.py
```

This creates Turtle files in `output/`, including:

- `persons.ttl`
- `events.ttl`
- `places.ttl`
- `organizations.ttl`
- `documents.ttl`
- `mk_history_full.ttl`

Clean and validate the graph:

```powershell
python scripts\validate_clean.py --input output\mk_history_full.ttl --output output\mk_history_clean.ttl
```

Create the graph report used by the web app:

```powershell
python scripts\graph_report.py --input output\mk_history_clean.ttl --output output\kg_report.md
```

The most important file for the demo is:

```text
output\mk_history_clean.ttl
```

## 2. Start Apache Jena Fuseki

The web app expects Fuseki to run on this dataset URL:

```text
http://localhost:3030/macedonian-history/sparql
```

Start Fuseki with an in-memory writable dataset named `macedonian-history`.

If `fuseki-server` is available in your terminal:

```powershell
fuseki-server --update --mem /macedonian-history
```

On Windows, if you are inside the Fuseki folder, you may need:

```powershell
.\fuseki-server.bat --update --mem /macedonian-history
```

If you only have the Fuseki `.jar` file:

```powershell
java -jar fuseki-server.jar --update --mem /macedonian-history
```

Open Fuseki in the browser:

```text
http://localhost:3030
```

## 3. Load The Graph Into Fuseki

In the Fuseki browser UI:

1. Open `http://localhost:3030`
2. Go to the dataset named `macedonian-history`
3. Open the upload/add data page
4. Upload this file:

```text
output\mk_history_clean.ttl
```

After upload, test the dataset by opening:

```text
http://localhost:3030/macedonian-history/query
```

Run a simple query:

```sparql
SELECT * WHERE {
  ?s ?p ?o .
}
LIMIT 10
```

If this returns rows, Fuseki is ready.

## 4. Run The Web App

In a second terminal, from the project root:

```powershell
python -m uvicorn app.main:app --reload --port 8000
```

Open:

```text
http://localhost:8000
```

The app will connect to Fuseki using:

```text
http://localhost:3030/macedonian-history/sparql
```

If your Fuseki endpoint is different, set `FUSEKI_ENDPOINT` before starting the app:

```powershell
$env:FUSEKI_ENDPOINT="http://localhost:3030/macedonian-history/sparql"
python -m uvicorn app.main:app --reload --port 8000
```

## Web App Pages

- Home - graph statistics and project overview.
- Search - search entities by label.
- Timeline - browse historical events by date.
- Entity pages - inspect labels, dates, Wikidata links, and related triples.
- Queries - run prepared SPARQL examples from `queries/`.
- Report - shows the generated graph report from `output/kg_report.md`.

## Useful SPARQL Queries

Example queries are stored in:

```text
queries\
```

The most useful ones for the demo are:

- `01_entity_counts.rq`
- `02_timeline_events.rq`
- `05_wikidata_links.rq`
- `07_documents_and_authors.rq`
- `09_missing_labels.rq`
- `10_temporal_intervals.rq`

You can run them either in Fuseki or from the web app's Queries page.

## Troubleshooting

If the web app says Fuseki is not reachable:

- Make sure Fuseki is running.
- Make sure the dataset name is exactly `macedonian-history`.
- Check that this URL works: `http://localhost:3030/macedonian-history/sparql`.

If the app opens but shows zero data:

- Make sure `output/mk_history_clean.ttl` was uploaded to Fuseki.
- Try the `SELECT * WHERE { ?s ?p ?o } LIMIT 10` query in Fuseki.
- Restart the web app after changing `FUSEKI_ENDPOINT`.

If Python cannot import `app.main`:

- Run the command from the project root, not from inside `app/`.
- Use: `python -m uvicorn app.main:app --reload --port 8000`.

If graph generation is slow or Wikidata returns 429/502/504:

- Wait and rerun later.
- The script already has retry and backoff handling.
- For the demo, you can use the existing `output/mk_history_clean.ttl` instead of recollecting data.

## Recommended Demo Flow

1. Start Fuseki.
2. Upload `output/mk_history_clean.ttl`.
3. Run one simple SPARQL query in Fuseki.
4. Start the web app.
5. Show Search, Timeline, Entity details, Queries, and Report.
