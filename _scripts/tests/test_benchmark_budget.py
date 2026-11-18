"""The demo benchmark must fit the 1-CU small demo workspace connection budget."""
import ast
from pathlib import Path
import unittest


class BenchmarkBudgetTest(unittest.TestCase):
    def test_default_keeps_required_100_clients_without_500_connection_burst(self):
        source = Path(__file__).resolve().parents[2] / "notebooks/04_realtime_serving.py"
        tree = ast.parse(source.read_text())
        levels = next(ast.literal_eval(n.value) for n in tree.body
                      if isinstance(n, ast.Assign)
                      and any(isinstance(t, ast.Name) and t.id == "CONCURRENCY_LEVELS"
                              for t in n.targets))
        self.assertIn(100, levels)
        self.assertLessEqual(max(levels), 100,
                             "500 clients exhausted small demo workspace Postgres connection slots")


if __name__ == "__main__":
    unittest.main()
