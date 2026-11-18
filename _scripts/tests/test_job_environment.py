"""Regression contract for the Serverless v3 built-in MLflow serializer.

The deployed serializer accesses FieldDescriptor.label. Protobuf 7 removed that
attribute; an unconstrained environment resolved to an incompatible version.
This offline check prevents that resolution without changing model logic.
The real MLflow create_run round trip is also checked by the pipeline run.
"""
from pathlib import Path
import re
import unittest


class JobEnvironmentTest(unittest.TestCase):
    def test_builtin_mlflow_has_compatible_protobuf(self):
        job = Path(__file__).resolve().parents[2] / "resources/job.yml"
        pins = re.findall(r"^\s*-\s*protobuf==([0-9.]+)\s*$", job.read_text(), re.M)
        self.assertEqual(len(pins), 1, "Pin a protobuf version compatible with built-in MLflow")
        self.assertEqual(int(pins[0].split('.')[0]), 5,
                         "Serverless v3 MLflow needs the protobuf 5 descriptor API")


if __name__ == "__main__":
    unittest.main()
