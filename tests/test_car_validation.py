import importlib.util
from pathlib import Path
import unittest

import numpy as np


MODULE_PATH = Path(__file__).parents[1] / "parsers" / "validate.py"
SPEC = importlib.util.spec_from_file_location("carnivores_validate", MODULE_PATH)
validate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validate)

VERTEX_DTYPE = np.dtype([
    ("coord", "<f4", (3,)),
    ("owner", "<u2"),
    ("hide", "<u2"),
])


class Context:
    def __init__(self):
        self.warnings = []


class CarVertexValidationTests(unittest.TestCase):
    def make_vertices(self, owners):
        vertices = np.zeros(len(owners), dtype=VERTEX_DTYPE)
        vertices["owner"] = owners
        return vertices

    def test_validation_never_rewrites_car_owners(self):
        vertices = self.make_vertices([0, 1, 4, 9])
        original = vertices["owner"].copy()
        context = Context()

        result = validate.validate_car_vertices(vertices, len(vertices), context)

        self.assertIs(result, vertices)
        np.testing.assert_array_equal(vertices["owner"], original)
        self.assertTrue(any("sparse/noncontiguous" in warning for warning in context.warnings))
        self.assertTrue(any("owner 0" in warning for warning in context.warnings))

    def test_all_zero_owners_are_preserved_and_reported(self):
        vertices = self.make_vertices([0, 0])
        context = Context()

        validate.validate_car_vertices(vertices, 2, context)

        np.testing.assert_array_equal(vertices["owner"], [0, 0])
        self.assertTrue(any("all vertices are unowned" in warning for warning in context.warnings))

    def test_shared_coordinate_validation_still_rejects_nonfinite_data(self):
        vertices = self.make_vertices([1])
        vertices["coord"][0, 0] = np.nan

        with self.assertRaisesRegex(ValueError, "NaN or infinite"):
            validate.validate_car_vertices(vertices, 1, Context())


if __name__ == "__main__":
    unittest.main()
