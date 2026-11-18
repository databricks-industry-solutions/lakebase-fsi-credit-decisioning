"""Test scoring helpers without importing Dash or opening external connections."""
import ast
from pathlib import Path
import random
import time
import unittest


SOURCE = Path(__file__).resolve().parents[2] / "06_credit_app/app.py"


class Matrix:
    def __getitem__(self, key):
        return 0.8


class Model:
    def predict(self, X):
        return [0.6]

    def predict_proba(self, X):
        return Matrix()


class BrokenModel(Model):
    def predict_proba(self, X):
        raise ValueError("inference unavailable")


def helpers(**models):
    tree = ast.parse(SOURCE.read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
             and n.name in {"_all_model_scores", "_predict_score", "run_decision"}]
    scope = {"_model": Model(), "_model_xgb": None, "_model_lr": None,
             "random": random, "time": time, "N_ROWS": 100000, "print": lambda *a: None}
    scope.update(models)
    exec(compile("from __future__ import annotations\n" +
                 "\n\n".join(ast.unparse(n) for n in nodes), str(SOURCE), "exec"), scope)
    return scope


class ScoringTest(unittest.TestCase):
    def test_unknown_customer_returns_connection_exactly_once(self):
        class Connection:
            def cursor(self): return self
            def execute(self, *a): pass
            def fetchone(self): return None
            def close(self): pass
        class Pool:
            returned = 0
            def getconn(self): return Connection()
            def putconn(self, conn):
                self.returned += 1
        pool = Pool()
        result = helpers(get_pool=lambda: pool)["run_decision"](-1)
        self.assertIn("error", result)
        self.assertEqual(pool.returned, 1)

    def test_missing_challengers_do_not_generate_scores(self):
        scores = helpers()["_all_model_scores"](None, 0.6, 42)
        self.assertEqual(scores, {"LightGBM": 0.6})

    def test_broken_challenger_does_not_generate_score(self):
        scores = helpers(_model_xgb=BrokenModel())["_all_model_scores"](None, 0.6, 42)
        self.assertNotIn("XGBoost", scores)

    def test_lightgbm_label_uses_lightgbm_when_challenger_selected(self):
        scores = helpers(_model_xgb=Model())["_all_model_scores"](None, 0.8, 42)
        self.assertEqual(scores["LightGBM"], 0.6)
        self.assertEqual(scores["XGBoost"], 0.8)

    def test_unavailable_selected_model_does_not_silently_use_another(self):
        self.assertIsNone(helpers()["_predict_score"]("XGBoost", None))


if __name__ == "__main__":
    unittest.main()
