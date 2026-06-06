import os
from typing import Any

import requests


FUSEKI_ENDPOINT = os.getenv(
    "FUSEKI_ENDPOINT",
    "http://localhost:3030/macedonian-history/sparql",
)


class SparqlError(RuntimeError):
    """Raised when the SPARQL backend cannot return usable JSON."""


def run_query(query: str) -> dict[str, Any]:
    try:
        response = requests.get(
            FUSEKI_ENDPOINT,
            params={"query": query},
            headers={"Accept": "application/sparql-results+json"},
            timeout=12,
        )
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise SparqlError(
            "Fuseki is not reachable. Start Apache Jena Fuseki and load the RDF graph."
        ) from exc
    except ValueError as exc:
        raise SparqlError("Fuseki returned a non-JSON response.") from exc


def bindings(query: str) -> list[dict[str, dict[str, str]]]:
    data = run_query(query)
    return data.get("results", {}).get("bindings", [])


def binding_value(row: dict[str, dict[str, str]], key: str, default: str = "") -> str:
    return row.get(key, {}).get("value", default)


def binding_type(row: dict[str, dict[str, str]], key: str, default: str = "") -> str:
    return row.get(key, {}).get("type", default)
