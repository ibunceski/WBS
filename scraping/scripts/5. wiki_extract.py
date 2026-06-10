"""
5. wiki_extract.py
==================
Извлекува RDF тројки од Wikipedia страни на македонски јазик
и ги зачувува во output/wiki_extracted.ttl

Употреба:
    python "scraping/scripts/5. wiki_extract.py"
"""

import sys
import os
from pathlib import Path

# Осигури дека root на проектот е во path-от
PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from scraping.wiki_triple_extractor import WikiTripleExtractor

URLS = [
    "https://mk.wikipedia.org/wiki/Гоце_Делчев",
    "https://mk.wikipedia.org/wiki/Илинденско_востание",
    # додај уште URL-ови овде
]

OUTPUT_PATH = PROJECT_ROOT / "scraping" / "output" / "wiki_extracted.ttl"

def main():
    os.makedirs(OUTPUT_PATH.parent, exist_ok=True)

    extractor = WikiTripleExtractor()  # чита ANTHROPIC_API_KEY од env
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