"""Bounded intent aliases used only to verify proposed DAG operations."""

import re


_REQUEST = r"(?:(?:give(?: me)?|show(?: me)?|calculate|compute|return|what is|what's)\s+)?(?:the\s+)?"
_CHINOOK_ENTITIES = {"invoice": "count_invoices", "customer": "count_customers", "track": "count_tracks"}
_TAXI_MEASURES = {"trip distance": "mean_distance", "fare amount": "mean_fare"}


def paraphrase_intent(source, question):
    """Recognize explicit measures and scopes, consuming the entire clause."""
    text = " ".join(question.casefold().split())
    if source == "chinook":
        match = re.fullmatch(
            _REQUEST + r"(?:total )?(?:number|count) of (?:all )?(invoice|customer|track)s?(?: records| rows)?",
            text,
        )
        if match:
            entity = match[1]
            return _CHINOOK_ENTITIES[entity], {}, f"How many {entity}s"
    elif source == "taxi":
        match = re.fullmatch(
            _REQUEST + r"(?:arithmetic mean|average) of (?:the )?(?:recorded )?(trip distance|fare amount)s?",
            text,
        )
        if match:
            measure = match[1]
            return _TAXI_MEASURES[measure], {}, f"Average {measure}"
    elif source == "ldbc":
        match = re.fullmatch(r"who is directly linked to person ([0-9]+) by a knows edge", text)
        if match:
            return "friends", {"source": match[1]}, f"Friends of person {match[1]}"
    return None
