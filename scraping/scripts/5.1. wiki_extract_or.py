import sys
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scraping.wiki_triple_extractor import WikiTripleExtractor

URLS = [
    "https://mk.wikipedia.org/wiki/Гоце_Делчев",
    "https://mk.wikipedia.org/wiki/Илинденско_востание",
]

OUTPUT_PATH = PROJECT_ROOT / "scraping" / "output" / "wiki_extracted.ttl"


def main():
    os.makedirs(OUTPUT_PATH.parent, exist_ok=True)

    extractor = WikiTripleExtractor(
        model="openai/gpt-4o-mini"
    )

    all_triples = []
    all_entities = []

    for url in URLS:
        triples, entities = extractor.process_wikipedia_page(url)
        all_triples.extend(triples)
        all_entities.extend(entities)

    extractor.save_to_turtle(all_triples, all_entities, str(OUTPUT_PATH))

    print(f"\n✓ Зачувано во {OUTPUT_PATH}")
    print(f"  Тројки: {len(all_triples)}")
    print(f"  По тип: {extractor.entity_summary()}")


if __name__ == "__main__":
    main()