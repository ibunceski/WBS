#!/usr/bin/env python3
"""
Generate a compact Markdown report for the cleaned Macedonian History KG.

Usage:
    .venv\\Scripts\\python.exe scripts\\graph_report.py
    .venv\\Scripts\\python.exe scripts\\graph_report.py --input output\\mk_history_clean.ttl
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

from rdflib import Graph, Literal, Namespace
from rdflib.namespace import OWL, RDF, RDFS, XSD

MKO = Namespace("http://macedonian-kg.mk/ontology#")
TIME = Namespace("http://www.w3.org/2006/time#")

MOJIBAKE_RE = re.compile(r"[ÐÑÂâÃ]")
QID_LABEL_RE = re.compile(r"^Q\d+$")


def qname(graph: Graph, node) -> str:
    try:
        return graph.namespace_manager.normalizeUri(node)
    except Exception:
        return str(node)


def count_class(graph: Graph, rdf_type) -> int:
    return sum(1 for _ in graph.subjects(RDF.type, rdf_type))


def literal_has_mojibake(value: Literal) -> bool:
    return bool(MOJIBAKE_RE.search(str(value)))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="output/mk_history_clean.ttl")
    parser.add_argument("--output", default="output/kg_report.md")
    args = parser.parse_args()

    graph = Graph()
    graph.parse(args.input, format="turtle")

    class_counts = {
        "Persons": count_class(graph, MKO.Person),
        "Events": count_class(graph, MKO.HistoricalEvent),
        "Places": count_class(graph, MKO.Place),
        "Organizations": count_class(graph, MKO.Organization),
        "Documents": count_class(graph, MKO.HistoricalDocument),
        "Periods": count_class(graph, MKO.Period),
    }

    same_as_count = sum(1 for _ in graph.triples((None, OWL.sameAs, None)))
    dated_entities = set()
    date_predicates = {
        MKO.birthDate,
        MKO.deathDate,
        MKO.startDate,
        MKO.endDate,
        MKO.foundingDate,
        MKO.dissolutionDate,
        MKO.dateCreated,
        TIME.hasBeginning,
        TIME.hasEnd,
    }
    for subject, predicate, _ in graph:
        if predicate in date_predicates:
            dated_entities.add(subject)

    qid_labels = []
    mojibake_literals = []
    missing_labels = []
    for subject in set(graph.subjects()):
        types = set(graph.objects(subject, RDF.type))
        if types & {
            MKO.Person,
            MKO.HistoricalEvent,
            MKO.Place,
            MKO.Organization,
            MKO.HistoricalDocument,
        }:
            labels = list(graph.objects(subject, RDFS.label))
            if not labels:
                missing_labels.append(subject)
            for label in labels:
                if isinstance(label, Literal) and QID_LABEL_RE.match(str(label)):
                    qid_labels.append((subject, label))

    for subject, predicate, obj in graph:
        if isinstance(obj, Literal) and literal_has_mojibake(obj):
            mojibake_literals.append((subject, predicate, obj))

    predicate_counts = Counter(predicate for _, predicate, _ in graph)

    lines = [
        "# Macedonian History KG Report",
        "",
        f"Source file: `{args.input}`",
        f"Total triples: `{len(graph):,}`",
        "",
        "## Entity Counts",
        "",
    ]
    for name, count in class_counts.items():
        lines.append(f"- {name}: `{count:,}`")

    lines.extend(
        [
            "",
            "## Linking And Time",
            "",
            f"- Wikidata `owl:sameAs` links: `{same_as_count:,}`",
            f"- Subjects with temporal/date predicates: `{len(dated_entities):,}`",
            "",
            "## Top Predicates",
            "",
        ]
    )
    for predicate, count in predicate_counts.most_common(15):
        lines.append(f"- `{qname(graph, predicate)}`: `{count:,}`")

    lines.extend(
        [
            "",
            "## Data Quality Signals",
            "",
            f"- Entities missing `rdfs:label`: `{len(missing_labels):,}`",
            f"- QID-only labels: `{len(qid_labels):,}`",
            f"- Literals that look mojibake-encoded: `{len(mojibake_literals):,}`",
            "",
        ]
    )

    if missing_labels:
        lines.append("### Missing Label Examples")
        lines.extend(f"- `{qname(graph, subject)}`" for subject in missing_labels[:10])
        lines.append("")

    if qid_labels:
        lines.append("### QID Label Examples")
        lines.extend(f"- `{qname(graph, subject)}` -> `{label}`" for subject, label in qid_labels[:10])
        lines.append("")

    if mojibake_literals:
        lines.append("### Mojibake Examples")
        for subject, predicate, obj in mojibake_literals[:10]:
            lines.append(f"- `{qname(graph, subject)}` `{qname(graph, predicate)}` `{str(obj)[:90]}`")
        lines.append("")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {output} from {len(graph):,} triples")


if __name__ == "__main__":
    main()
