import re
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import quote

import ollama
import requests
from bs4 import BeautifulSoup
from rdflib import Graph, Literal, Namespace, URIRef
from rdflib.namespace import RDF, RDFS, OWL, XSD


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)


MKO = Namespace("http://macedonian-history.mk/ontology/")
MKR = Namespace("http://macedonian-history.mk/resource/")
SCHEMA = Namespace("https://schema.org/")



@dataclass
class RDFTriple:
    subject: str
    predicate: str
    obj: str
    obj_is_literal: bool = False
    obj_lang: str = "mk"
    confidence: float = 1.0
    source_sentence: str = ""


@dataclass
class KnownEntity:
    local_name: str
    label_mk: str
    entity_type: str
    source_page: str = ""
    wikidata_id: str = ""


def fetch_wikipedia_text(url: str) -> tuple[str, str]:
    if "wikipedia.org/wiki/" in url:
        base = url.split("/wiki/")[0]
        page_title = url.split("/wiki/")[1]
        api_url = (
            f"{base}/w/api.php"
            f"?action=query&prop=extracts&explaintext=true"
            f"&format=json&titles={page_title}"
        )
    else:
        raise ValueError(f"Не е препознаен Wikipedia URL: {url}")

    resp = requests.get(api_url, timeout=30, headers={"User-Agent": "WBS-KG-Bot/1.0"})
    resp.raise_for_status()
    data = resp.json()
    pages = data["query"]["pages"]
    page = next(iter(pages.values()))

    title = page.get("title", page_title)
    text = page.get("extract", "")
    return title, text



def slugify(text: str) -> str:

    slug = re.sub(r"\s+", "_", text.strip())
    slug = re.sub(r"[^\w\-]", "", slug, flags=re.UNICODE)
    return slug


SYSTEM_PROMPT = """Ti si ekspert za ekstrakcija na RDF trojki od tekst za makedonska istorija.

Tvojata cel e da identifikuvas trojki vo format:
  (subjekt, predikat, objekt)

Vazni pravila:
1. Subjekt i objekt sekogash treba da bidat IMENUVANI ENTITETI (lica, mesta, organizacii, nastani, dokumenti).
2. Predikatot treba da bide eden od dozvolitele predikati od listata podole.
3. Ako objektot e literal (datum, opis), oznaci go so "LITERAL:" prefiks.
4. Vrati SAMO validen JSON, bez Markdown blokovi.

Dozvoleni predikati i nivno znacenje:
  participatedIn     - lice ucestvuvalo vo nastan ili organizacija
  ledBy              - nastan/organizacija vodena od lice
  tookPlaceIn        - nastan se odigral vo mesto
  bornIn             - lice rodeno vo mesto
  diedIn             - lice pochinalo vo mesto
  memberOf           - lice/entitet e clen na organizacija
  founded            - lice osnovalo organizacija
  authorOf           - lice avtor na dokument
  hasPurpose         - organizacija/nastan ima cel (LITERAL)
  hasDate            - entitet ima datum (LITERAL)
  locatedIn          - mesto se naogja vo pogolem entitet
  relatedTo          - generalna vrska megju dva entiteti
  hasRole            - lice ima uloga (LITERAL)
  partOf             - entitet e del od pogolem entitet

Format na odgovor (SAMO JSON, bez nikakov drug tekst):
{
  "entities": [
    {"label": "Gоце Делчев", "type": "Person"},
    {"label": "Солунски конгрес", "type": "Event"},
    ...
  ],
  "triples": [
    {
      "subject": "Гоце Делчев",
      "predicate": "participatedIn",
      "object": "Солунски конгрес",
      "is_literal": false,
      "confidence": 0.95,
      "source": "реченица од која е извлечена тројката"
    },
    ...
  ]
}
"""


class WikiTripleExtractor:

    def __init__(
        self,
        model: str = "llama3.2",
        chunk_size: int = 2000,
        delay_between_chunks: float = 1.0,
    ):
        self.model = model
        self.chunk_size = chunk_size
        self.delay = delay_between_chunks

        self.entity_registry: dict[str, KnownEntity] = {}


    def _extract_from_chunk(
        self, chunk: str, page_title: str
    ) -> tuple[list[RDFTriple], list[KnownEntity]]:
        user_msg = (
            f"Страна: {page_title}\n\n"
            f"Текст:\n{chunk}\n\n"
            "Извлечи ги сите RDF тројки и ентитети од овој текст."
        )

        try:
            response = ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_msg},
                ],
            )
            raw = response["message"]["content"].strip()
        except Exception as e:
            log.error("Ollama грешка: %s", e)
            return [], []

        raw = re.sub(r"^```json\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning("Неуспешен JSON parse: %s\nОдговор: %s", e, raw[:300])
            return [], []

        triples: list[RDFTriple] = []
        entities: list[KnownEntity] = []

        for ent in data.get("entities", []):
            label = ent.get("label", "").strip()
            etype = ent.get("type", "Thing")
            if not label:
                continue
            local = slugify(label)
            if label not in self.entity_registry:
                known = KnownEntity(
                    local_name=local,
                    label_mk=label,
                    entity_type=etype,
                )
                self.entity_registry[label] = known
                entities.append(known)

        for t in data.get("triples", []):
            subj = t.get("subject", "").strip()
            pred = t.get("predicate", "").strip()
            obj = t.get("object", "").strip()
            is_lit = t.get("is_literal", False)
            conf = float(t.get("confidence", 1.0))
            src = t.get("source", "")

            if not subj or not pred or not obj:
                continue

            if not is_lit and obj not in self.entity_registry:
                local = slugify(obj)
                self.entity_registry[obj] = KnownEntity(
                    local_name=local,
                    label_mk=obj,
                    entity_type="Thing",
                )
                entities.append(self.entity_registry[obj])

            triples.append(
                RDFTriple(
                    subject=subj,
                    predicate=pred,
                    obj=obj,
                    obj_is_literal=is_lit,
                    confidence=conf,
                    source_sentence=src,
                )
            )

        return triples, entities

    def process_wikipedia_page(
        self, url: str
    ) -> tuple[list[RDFTriple], list[KnownEntity]]:

        log.info("Превземање страна: %s", url)
        title, text = fetch_wikipedia_text(url)
        log.info("Страна '%s' — %d знаци", title, len(text))

        all_triples: list[RDFTriple] = []
        all_entities: list[KnownEntity] = []

        chunks = [text[i : i + self.chunk_size] for i in range(0, len(text), self.chunk_size)]
        log.info("Обработка на %d chunk(s)...", len(chunks))

        for i, chunk in enumerate(chunks, 1):
            log.info("  Chunk %d/%d", i, len(chunks))
            triples, entities = self._extract_from_chunk(chunk, title)
            all_triples.extend(triples)
            all_entities.extend(entities)

            for ent in entities:
                ent.source_page = url

            if i < len(chunks):
                time.sleep(self.delay)

        log.info(
            "Извлечено: %d тројки, %d нови ентитети", len(all_triples), len(all_entities)
        )
        return all_triples, all_entities


    def save_to_turtle(
        self,
        triples: list[RDFTriple],
        entities: list[KnownEntity],
        output_path: str,
    ) -> None:
        g = Graph()
        g.bind("mko", MKO)
        g.bind("mkr", MKR)
        g.bind("rdf", RDF)
        g.bind("rdfs", RDFS)
        g.bind("owl", OWL)
        g.bind("schema", SCHEMA)

        type_map = {
            "Person": SCHEMA.Person,
            "Place": SCHEMA.Place,
            "Organization": SCHEMA.Organization,
            "Event": SCHEMA.Event,
            "Document": SCHEMA.CreativeWork,
            "Thing": OWL.Thing,
        }

        for label, ent in self.entity_registry.items():
            uri = MKR[ent.local_name]
            rdf_type = type_map.get(ent.entity_type, OWL.Thing)
            g.add((uri, RDF.type, rdf_type))
            g.add((uri, RDFS.label, Literal(ent.label_mk, lang="mk")))
            if ent.source_page:
                g.add((uri, RDFS.seeAlso, URIRef(ent.source_page)))

        for t in triples:
            if t.confidence < 0.5:
                continue

            subj_uri = MKR[slugify(t.subject)]
            pred_uri = MKO[t.predicate]

            if t.obj_is_literal:
                obj_node = Literal(t.obj, lang=t.obj_lang)
            else:
                obj_node = MKR[slugify(t.obj)]

            g.add((subj_uri, pred_uri, obj_node))

            if t.source_sentence:
                stmt = URIRef(
                    f"http://macedonian-history.mk/stmt/{slugify(t.subject)}_{t.predicate}_{slugify(t.obj)}"
                )
                g.add((stmt, RDF.type, RDF.Statement))
                g.add((stmt, RDF.subject, subj_uri))
                g.add((stmt, RDF.predicate, pred_uri))
                g.add((stmt, RDF.object, obj_node))
                g.add((stmt, RDFS.comment, Literal(t.source_sentence, lang="mk")))

        g.serialize(destination=output_path, format="turtle")
        log.info("Зачувано во: %s (%d тројки вкупно)", output_path, len(g))


    def entity_summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ent in self.entity_registry.values():
            counts[ent.entity_type] = counts.get(ent.entity_type, 0) + 1
        return counts


def process_page_list(
    urls: list[str],
    output_ttl: str = "output/wiki_extracted.ttl",
    model: str = "llama3.2",
) -> None:
    extractor = WikiTripleExtractor(model=model)
    all_triples: list[RDFTriple] = []
    all_entities: list[KnownEntity] = []

    for url in urls:
        try:
            triples, entities = extractor.process_wikipedia_page(url)
            all_triples.extend(triples)
            all_entities.extend(entities)
        except Exception as e:
            log.error("Грешка при обработка на %s: %s", url, e)
        time.sleep(2)

    extractor.save_to_turtle(all_triples, all_entities, output_ttl)

    summary = extractor.entity_summary()
    log.info("Резиме на ентитети: %s", summary)
    log.info("Вкупно тројки: %d", len(all_triples))



if __name__ == "__main__":
    import argparse
    import os

    parser = argparse.ArgumentParser(
        description="Извлечи RDF тројки од Wikipedia страни."
    )
    parser.add_argument(
        "urls",
        nargs="+",
        help="Еден или повеќе Wikipedia URL-ови за обработка.",
    )
    parser.add_argument(
        "--output",
        default="output/wiki_extracted.ttl",
        help="Излезен Turtle фајл (default: output/wiki_extracted.ttl)",
    )
    parser.add_argument(
        "--model",
        default="llama3.2",
        help="Ollama модел за користење (default: llama3.2)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=2000,
        help="Број на знаци по chunk (default: 2000)",
    )
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)

    extractor = WikiTripleExtractor(
        model=args.model,
        chunk_size=args.chunk_size,
    )
    all_triples = []
    all_entities = []

    for url in args.urls:
        t, e = extractor.process_wikipedia_page(url)
        all_triples.extend(t)
        all_entities.extend(e)
        time.sleep(2)

    extractor.save_to_turtle(all_triples, all_entities, args.output)
    print(f"\n✓ Зачувано во {args.output}")
    print(f"  Тројки: {len(all_triples)}")
    print(f"  Ентитети: {sum(extractor.entity_summary().values())}")
    print(f"  По тип: {extractor.entity_summary()}")