"""Exercise actual notebook input-selection cells without Spark or cloud access."""
import ast
from contextlib import redirect_stdout
import io
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[2]


class Widgets:
    def __init__(self, params):
        self.values = {"uc_catalog": "demo", "uc_schema": "credit", **params}

    def text(self, key, default, *args):
        self.values.setdefault(key, default)

    def get(self, key):
        return self.values.get(key, "credit_features_bronze" if key == "input_table" else "")


def selected_table(notebook, params):
    tree = ast.parse((ROOT / "notebooks" / notebook).read_text())
    if notebook.startswith("02"):
        end = next(i for i, node in enumerate(tree.body)
                   if isinstance(node, ast.Import) and node.names[0].name == "mlflow")
        nodes = tree.body[:end]
    else:
        def assigns(node, name):
            return isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets)
        start = next(i for i, node in enumerate(tree.body) if assigns(node, "input_table"))
        end = next(i for i, node in enumerate(tree.body) if assigns(node, "bronze"))
        nodes = tree.body[start:end + 1]
    tables = []
    env = {"dbutils": SimpleNamespace(widgets=Widgets(params)), "re": re,
           "CATALOG": "demo", "SCHEMA": "credit",
           "spark": SimpleNamespace(table=lambda name: tables.append(name) or name)}
    module = ast.Module(body=nodes, type_ignores=[])
    with redirect_stdout(io.StringIO()):
        exec(compile(module, notebook, "exec"), env)
    return env["TABLE_NAME"] if notebook.startswith("02") else tables[-1]


class InputSelectionTests(unittest.TestCase):
    def test_training_manual_default(self):
        self.assertEqual(selected_table("02_model_training.py", {}), "demo.credit.credit_features_bronze")

    def test_training_reads_declarative_output(self):
        self.assertEqual(selected_table("02_model_training.py", {"input_table": "credit_ingest_silver"}),
                         "demo.credit.credit_ingest_silver")

    def test_training_rejects_qualified_or_malformed_name(self):
        for value in ("other.schema.table", "bad; SELECT 1", "", "a/b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                selected_table("02_model_training.py", {"input_table": value})

    def test_features_manual_default(self):
        self.assertEqual(selected_table("03_feature_store.py", {}), "demo.credit.credit_features_bronze")

    def test_features_read_declarative_output(self):
        self.assertEqual(selected_table("03_feature_store.py", {"input_table": "credit_ingest_silver"}),
                         "demo.credit.credit_ingest_silver")

    def test_features_reject_qualified_or_malformed_name(self):
        for value in ("other.schema.table", "bad; SELECT 1", "", "a/b"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                selected_table("03_feature_store.py", {"input_table": value})


if __name__ == "__main__":
    unittest.main()
