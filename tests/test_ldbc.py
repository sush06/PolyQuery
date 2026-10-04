from pathlib import Path
import tempfile
import unittest

from polyquery.engine import Engine
from polyquery.ldbc import LDBCGraph
from polyquery.backends import UnsupportedPlan
from polyquery.planner import Plan


class LDBCTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.people = Path(self.directory.name) / "people.csv"
        self.edges = Path(self.directory.name) / "knows.csv"
        self.people.write_text("id|firstName|lastName\n1|A|One\n2|B|Two\n3|C|Three\n4|D|Four\n", encoding="utf-8")
        self.edges.write_text("Person.id|Person.id|creationDate\n1|2|0\n2|3|0\n2|1|0\n", encoding="utf-8")

    def engine(self):
        return Engine(source="ldbc", people_path=self.people, edges_path=self.edges)

    def test_projection_and_paths(self):
        with self.engine() as engine:
            self.assertEqual(engine.source_metadata["edge_rows"], 3)
            self.assertEqual(engine.source_metadata["undirected_edges"], 2)
            self.assertEqual(engine.query("How many people?")["result"]["value"], 4)
            self.assertEqual(engine.query("Shortest path from 3 to 1")["result"]["value"], ["3", "2", "1"])
            self.assertEqual(engine.query("Shortest path from 1 to 1")["result"]["value"], ["1"])
            self.assertIsNone(engine.query("Shortest path from 1 to 4")["result"]["value"])
            self.assertEqual([p["id"] for p in engine.query("Friends of 2")["result"]["value"]], ["1", "3"])

    def test_fail_closed_grammar_and_plans(self):
        with self.engine() as engine:
            for question in ["How many employees?", "Friends of 99", "Friends of 1 before 2020", "Delete friends of 1", "How many people and friends of 1?", None]:
                with self.subTest(question=question):
                    self.assertEqual(engine.query(question)["status"], "abstained")
            for plan in [Plan("graph", "friends", {"source": "99"}), Plan("graph", "count_people", {"limit": 1}), Plan("graph", "descendants", {"source": "1"})]:
                with self.subTest(plan=plan), self.assertRaises(UnsupportedPlan):
                    engine.backends["graph"].execute(plan)

    def test_reject_bad_sources(self):
        for content in ["wrong|header\n1|2\n", "Person.id|Person.id|creationDate\n1|99|0\n", "Person.id|Person.id|creationDate\n1|2\n"]:
            self.edges.write_text(content, encoding="utf-8")
            with self.subTest(content=content), self.assertRaises(ValueError):
                LDBCGraph(self.people, self.edges)
        self.people.write_text("id|firstName|lastName\n1|A|One\n1|B|Two\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            LDBCGraph(self.people, self.edges)


if __name__ == "__main__":
    unittest.main()
