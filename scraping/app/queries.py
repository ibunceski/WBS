PREFIXES = """
PREFIX mk: <http://macedonian-kg.mk/resource/>
PREFIX mko: <http://macedonian-kg.mk/ontology#>
PREFIX owl: <http://www.w3.org/2002/07/owl#>
PREFIX rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>
PREFIX xsd: <http://www.w3.org/2001/XMLSchema#>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
"""

HOME_STATS = PREFIXES + """
SELECT ?key ?value
WHERE {
  {
    SELECT ("entities" AS ?key) (COUNT(?entity) AS ?value)
    WHERE { ?entity rdf:type ?class . FILTER(STRSTARTS(STR(?class), STR(mko:))) }
  }
  UNION
  {
    SELECT ("events" AS ?key) (COUNT(?event) AS ?value)
    WHERE { ?event rdf:type mko:HistoricalEvent . }
  }
  UNION
  {
    SELECT ("places" AS ?key) (COUNT(?place) AS ?value)
    WHERE { ?place rdf:type mko:Place . }
  }
  UNION
  {
    SELECT ("wikidata" AS ?key) (COUNT(?entity) AS ?value)
    WHERE { ?entity owl:sameAs ?wikidata . }
  }
  UNION
  {
    SELECT ("start" AS ?key) (MIN(?date) AS ?value)
    WHERE { ?entity mko:startDate|mko:birthDate|mko:foundingDate|mko:dateCreated ?date . }
  }
  UNION
  {
    SELECT ("end" AS ?key) (MAX(?date) AS ?value)
    WHERE { ?entity mko:endDate|mko:deathDate|mko:dissolutionDate|mko:dateCreated ?date . }
  }
}
"""

SEARCH = PREFIXES + """
SELECT ?entity ?label ?type ?date ?wikidata
WHERE {
  ?entity rdf:type ?type ;
          rdfs:label ?label .
  VALUES ?type { mko:Person mko:HistoricalEvent mko:Place mko:Organization mko:HistoricalDocument mko:Period }
  FILTER(CONTAINS(LCASE(STR(?label)), LCASE("%s")))
  OPTIONAL { ?entity owl:sameAs ?wikidata }
  OPTIONAL {
    ?entity mko:startDate|mko:birthDate|mko:foundingDate|mko:dateCreated ?date .
  }
}
ORDER BY LCASE(STR(?label))
LIMIT 40
"""

TIMELINE = PREFIXES + """
SELECT ?event ?label ?start ?end ?placeLabel ?description
WHERE {
  ?event rdf:type mko:HistoricalEvent .
  OPTIONAL { ?event rdfs:label ?label }
  OPTIONAL { ?event mko:startDate ?start }
  OPTIONAL { ?event mko:endDate ?end }
  OPTIONAL {
    ?event mko:tookPlaceIn ?place .
    OPTIONAL { ?place rdfs:label ?placeLabel }
  }
  OPTIONAL { ?event rdfs:comment ?description }
  %s
}
ORDER BY ?start ?label
LIMIT 120
"""

ENTITY_DETAILS = PREFIXES + """
SELECT ?label ?type ?birth ?death ?start ?end ?founding ?dissolution ?created ?wikidata ?wkt ?comment
WHERE {
  VALUES ?entity { <%s> }
  OPTIONAL { ?entity rdfs:label ?label }
  OPTIONAL { ?entity rdf:type ?type }
  OPTIONAL { ?entity mko:birthDate ?birth }
  OPTIONAL { ?entity mko:deathDate ?death }
  OPTIONAL { ?entity mko:startDate ?start }
  OPTIONAL { ?entity mko:endDate ?end }
  OPTIONAL { ?entity mko:foundingDate ?founding }
  OPTIONAL { ?entity mko:dissolutionDate ?dissolution }
  OPTIONAL { ?entity mko:dateCreated ?created }
  OPTIONAL { ?entity owl:sameAs ?wikidata }
  OPTIONAL { ?entity geo:asWKT ?wkt }
  OPTIONAL { ?entity rdfs:comment ?comment }
}
LIMIT 1
"""

ENTITY_RELATED = PREFIXES + """
SELECT ?direction ?predicate ?predicateLabel ?related ?relatedLabel ?relatedType
WHERE {
  VALUES ?entity { <%s> }
  {
    BIND("outgoing" AS ?direction)
    ?entity ?predicate ?related .
  }
  UNION
  {
    BIND("incoming" AS ?direction)
    ?related ?predicate ?entity .
  }
  FILTER(isIRI(?related))
  OPTIONAL { ?predicate rdfs:label ?predicateLabel }
  OPTIONAL { ?related rdfs:label ?relatedLabel }
  OPTIONAL { ?related rdf:type ?relatedType }
  FILTER(?predicate != rdf:type)
}
ORDER BY ?direction ?predicateLabel ?relatedLabel
LIMIT 80
"""

ENTITY_TRIPLES = PREFIXES + """
SELECT ?subject ?predicate ?object
WHERE {
  VALUES ?entity { <%s> }
  {
    BIND(?entity AS ?subject)
    ?entity ?predicate ?object .
  }
  UNION
  {
    ?subject ?predicate ?entity .
    BIND(?entity AS ?object)
  }
}
ORDER BY ?subject ?predicate ?object
LIMIT 160
"""
