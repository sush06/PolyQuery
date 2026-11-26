import pytest

from benchmarks.minimal_decomposition import protect_identifiers, restore


@pytest.mark.parametrize(("question", "protected", "mapping"), [
    ("Shortest path from 008 to 12.", "Shortest path from ENTITY_1 to ENTITY_2.",
     {"ENTITY_1": "008", "ENTITY_2": "12"}),
    ("List friends of person 8, then compare friends of person ID: 12 and person 8.",
     "List friends of person ENTITY_1, then compare friends of person ID: ENTITY_2 and person ENTITY_1.",
     {"ENTITY_1": "8", "ENTITY_2": "12"}),
    ("For 2024 and 2025-03-01, show the top 10 above -2.75, limit to 3, and select the 2nd row.",
     "For 2024 and 2025-03-01, show the top 10 above -2.75, limit to 3, and select the 2nd row.", {}),
    ("Select person 2 on the path.", "Select person 2 on the path.", {}),
    ("Find ID#86 as of 2024-06-01 at a threshold of .5.",
     "Find ID#ENTITY_1 as of 2024-06-01 at a threshold of .5.", {"ENTITY_1": "86"}),
])
def test_protection_is_scoped_to_identifier_slots(question, protected, mapping):
    assert protect_identifiers(question) == (protected, mapping)


def payload(*intents):
    return {
        "decision": "continue", "ambiguity": "", "final_task": len(intents),
        "tasks": [{"intent": intent, "depends_on": [index] if index else []}
                  for index, intent in enumerate(intents)],
    }


def test_restore_exact_identifiers_and_allow_ordinal_and_task_references():
    question = "Shortest path from 008 to 12; then list friends of the second person on that path."
    result = restore(payload("Find the shortest path from ENTITY_1 to ENTITY_2.",
                             "From task 1, list the friends of person 2 on the path."), question)
    assert result["tasks"][0]["intent"] == "Find the shortest path from 008 to 12."
    assert result["tasks"][1]["depends_on"] == [1]
    assert result["tasks"][1]["intent"].endswith("person 2 on the path.")


@pytest.mark.parametrize(("intent", "reason"), [
    ("Find friends of person 800.", "protected placeholder"),
    ("Find friends of ENTITY_1 and 950000.", "Unverified numeric"),
    ("Find friends.", "missing from the tasks"),
    ("Find friends of ENTITY_9.", "Unknown identifier placeholder"),
    ("Find friends of ENTITY_1.25.", "Unknown identifier placeholder"),
])
def test_reject_missing_invented_or_malformed_identifiers(intent, reason):
    with pytest.raises(ValueError, match=reason):
        restore(payload(intent), "Find friends of person 8.")


def test_accept_unchanged_year_limit_and_decimal_threshold():
    question = "For the 2024 report, show the top 10 with a value below -2.75."
    answer = payload("Find the top 10 with a value below -2.750 for the 2024 report.")
    assert restore(answer, question) == answer
