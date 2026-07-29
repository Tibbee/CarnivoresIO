from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np


PACKAGE_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PACKAGE_ROOT.parent))
from carnivores_io.core.core import BONE_DTYPE, CAR_HEADER_DTYPE, FACE_DTYPE, VERTEX_DTYPE
from carnivores_io.parsers import validate


class Context:
    def __init__(self):
        self.warnings = []


class CarVertexValidationTests(unittest.TestCase):
    def make_vertices(self, owners):
        vertices = np.zeros(len(owners), dtype=VERTEX_DTYPE)
        vertices["owner"] = owners
        return vertices

    def test_validation_never_rewrites_car_owners(self):
        vertices = self.make_vertices([-1, 0, 4, 9])
        original = vertices["owner"].copy()
        context = Context()

        result = validate.validate_car_vertices(vertices, len(vertices), context)

        self.assertIs(result, vertices)
        np.testing.assert_array_equal(vertices["owner"], original)
        self.assertTrue(any("sparse/noncontiguous" in warning for warning in context.warnings))
        self.assertTrue(any("negative owner" in warning for warning in context.warnings))

    def test_owner_zero_is_a_valid_group(self):
        vertices = self.make_vertices([0, 0])
        context = Context()

        validate.validate_car_vertices(vertices, 2, context)

        np.testing.assert_array_equal(vertices["owner"], [0, 0])
        self.assertFalse(any("all vertices are unowned" in warning for warning in context.warnings))

    def test_all_negative_owners_are_reported_as_unowned(self):
        vertices = self.make_vertices([-1, -1])
        context = Context()

        validate.validate_car_vertices(vertices, 2, context)

        self.assertTrue(any("all vertices are unowned" in warning for warning in context.warnings))

    def test_mesh_counts_above_old_limit_are_compatibility_warnings(self):
        header = np.zeros(1, dtype=CAR_HEADER_DTYPE)[0]
        header["vertex_count"] = 3000
        header["face_count"] = 1
        required_size = CAR_HEADER_DTYPE.itemsize + 3000 * VERTEX_DTYPE.itemsize + FACE_DTYPE.itemsize
        with tempfile.NamedTemporaryFile() as fixture:
            fixture.truncate(required_size)
            context = Context()
            validate.validate_car_header(header, fixture.name, context)

        self.assertTrue(any("AltEdit" in warning for warning in context.warnings))

    def test_bone_cycles_are_rejected_without_repair(self):
        bones = np.zeros(2, dtype=BONE_DTYPE)
        bones["parent"] = [1, 0]
        original = bones["parent"].copy()

        with self.assertRaisesRegex(ValueError, "cycle"):
            validate.validate_3df_bones(bones, 2, Context())

        np.testing.assert_array_equal(bones["parent"], original)

    def test_shared_coordinate_validation_still_rejects_nonfinite_data(self):
        vertices = self.make_vertices([1])
        vertices["coord"][0, 0] = np.nan

        with self.assertRaisesRegex(ValueError, "NaN or infinite"):
            validate.validate_car_vertices(vertices, 1, Context())


if __name__ == "__main__":
    unittest.main()
