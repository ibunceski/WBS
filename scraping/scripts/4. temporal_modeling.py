#!/usr/bin/env python3
"""
Build temporal named graphs for Macedonian history events.

Input:
    output/mk_history_clean.ttl

Outputs:
    output/mk_history_temporal.nq
    output/mk_history_temporal.trig
"""

from __future__ import annotations

import re
from pathlib import Path

from rdflib import BNode, Dataset, Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, XSD


BASE_DIR = Path(__file__).resolve().parents[1]
INPUT_PATH = BASE_DIR / "output" / "mk_history_clean.ttl"
NQUADS_PATH = BASE_DIR / "output" / "mk_history_temporal.nq"
TRIG_PATH = BASE_DIR / "output" / "mk_history_temporal.trig"

MKO = Namespace("http://macedonian-history.mk/ontology#")
MKR = Namespace("http://macedonian-history.mk/resource/")
MKC = Namespace("http://macedonian-history.mk/context/")
TIME = Namespace("http://www.w3.org/2006/time#")

LEGACY_MKO = Namespace("http://macedonian-kg.mk/ontology#")
LEGACY_MKR = Namespace("http://macedonian-kg.mk/resource/")

EVENT_CLASSES = {
    MKO.HistoricalEvent,
    MKO.Battle,
    MKO.Congress,
    MKO.Uprising,
    MKO.PoliticalEntity,
    LEGACY_MKO.HistoricalEvent,
    LEGACY_MKO.Battle,
    LEGACY_MKO.Congress,
    LEGACY_MKO.Uprising,
    LEGACY_MKO.PoliticalEntity,
}

START_DATE_PREDICATES = {MKO.startDate, LEGACY_MKO.startDate}
END_DATE_PREDICATES = {MKO.endDate, LEGACY_MKO.endDate}
DATE_PREDICATES = {MKO.date, LEGACY_MKO.date}

DATE_RE = re.compile(r"^(?P<year>-?\d{1,6})(?:-(?P<month>\d{2})(?:-(?P<day>\d{2}))?)?$")


def bind_namespaces(graph: Graph | Dataset) -> None:
    graph.bind("mko", MKO)
    graph.bind("mkr", MKR)
    graph.bind("mkc", MKC)
    graph.bind("time", TIME)
    graph.bind("xsd", XSD)


def local_name(uri: URIRef) -> str:
    text = str(uri)
    for separator in ("#", "/"):
        if separator in text:
            tail = text.rsplit(separator, 1)[1]
            if tail:
                return tail
    return re.sub(r"\W+", "_", text).strip("_") or "event"


def normalize_date(value: Literal) -> tuple[str, bool] | None:
    """Return an xsd:date lexical value and whether it is approximate."""
    text = str(value).strip()
    if not text:
        return None

    if text.startswith("+"):
        text = text[1:]
    if "T" in text:
        text = text.split("T", 1)[0]
    text = text.rstrip("Zz")

    match = DATE_RE.match(text)
    if not match:
        return None

    year = int(match.group("year"))
    month_text = match.group("month")
    day_text = match.group("day")
    approximate = value.datatype == XSD.gYear or (month_text is None and day_text is None)

    month = int(month_text) if month_text else 1
    day = int(day_text) if day_text else 1
    if month == 0:
        month = 1
        approximate = True
    if day == 0:
        day = 1
        approximate = True

    return f"{year:04d}-{month:02d}-{day:02d}", approximate


def sorted_dates(values: list[Literal]) -> list[tuple[str, bool]]:
    dates = [normalized for value in values if isinstance(value, Literal) for normalized in [normalize_date(value)] if normalized]
    return sorted(dates, key=lambda item: item[0])


def is_event(graph: Graph, subject: URIRef) -> bool:
    return any(rdf_type in EVENT_CLASSES for rdf_type in graph.objects(subject, RDF.type))


def event_dates(graph: Graph, event: URIRef) -> tuple[tuple[str, bool], tuple[str, bool]] | None:
    starts = sorted_dates([obj for pred in START_DATE_PREDICATES for obj in graph.objects(event, pred)])
    ends = sorted_dates([obj for pred in END_DATE_PREDICATES for obj in graph.objects(event, pred)])
    dates = sorted_dates([obj for pred in DATE_PREDICATES for obj in graph.objects(event, pred)])

    if starts or ends:
        start = starts[0] if starts else ends[0]
        end = ends[-1] if ends else start
        return start, end
    if dates:
        return dates[0], dates[-1]
    return None


def add_interval_metadata(dataset: Dataset, context_uri: URIRef, start: tuple[str, bool], end: tuple[str, bool]) -> None:
    default_graph = dataset.default_graph
    beginning = BNode()
    ending = BNode()
    confidence = "approximate" if start[1] or end[1] else "certain"

    default_graph.add((context_uri, RDF.type, TIME.Interval))
    default_graph.add((context_uri, TIME.hasBeginning, beginning))
    default_graph.add((beginning, RDF.type, TIME.Instant))
    default_graph.add((beginning, TIME.inXSDDate, Literal(start[0], datatype=XSD.date)))
    default_graph.add((context_uri, TIME.hasEnd, ending))
    default_graph.add((ending, RDF.type, TIME.Instant))
    default_graph.add((ending, TIME.inXSDDate, Literal(end[0], datatype=XSD.date)))
    default_graph.add((context_uri, MKO.dateConfidence, Literal(confidence)))


def build_temporal_dataset(input_path: Path) -> tuple[Dataset, int, int]:
    source = Graph()
    bind_namespaces(source)
    source.parse(str(input_path), format="turtle")

    dataset = Dataset()
    bind_namespaces(dataset)
    for prefix, namespace in source.namespaces():
        dataset.bind(prefix, namespace)
    bind_namespaces(dataset)

    events = sorted(
        [subject for subject in set(source.subjects(RDF.type, None)) if isinstance(subject, URIRef) and is_event(source, subject)],
        key=str,
    )
    processable_events = {event: dates for event in events for dates in [event_dates(source, event)] if dates}

    for subject, predicate, obj in source:
        if subject in processable_events:
            context_uri = URIRef(MKC[local_name(subject)])
            dataset.graph(context_uri).add((subject, predicate, obj))
        else:
            dataset.default_graph.add((subject, predicate, obj))

    for event, (start, end) in processable_events.items():
        context_uri = URIRef(MKC[local_name(event)])
        add_interval_metadata(dataset, context_uri, start, end)

    return dataset, len(events), len(processable_events)


def main() -> None:
    dataset, total_events, contexts_created = build_temporal_dataset(INPUT_PATH)

    NQUADS_PATH.parent.mkdir(parents=True, exist_ok=True)
    dataset.serialize(destination=str(NQUADS_PATH), format="nquads")
    dataset.serialize(destination=str(TRIG_PATH), format="trig")

    print(f"Total events processed: {total_events}")
    print(f"Temporal contexts created: {contexts_created}")
    print(f"Total quads written: {len(dataset)}")


if __name__ == "__main__":
    main()
