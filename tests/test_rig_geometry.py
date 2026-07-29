import importlib.util
from pathlib import Path
import unittest

import numpy as np


MODULE_PATH = Path(__file__).parents[1] / "utils" / "rig_reconstruction.py"
SPEC = importlib.util.spec_from_file_location("rig_geometry", MODULE_PATH)
rig = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rig)


class RigGeometryTests(unittest.TestCase):
    def _scaled_analysis(self, scale, offset=(0.0, 0.0, 0.0)):
        vertices = np.array(
            [
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
                [3.0, 0.0, 0.0],
                [5.0, 0.0, 0.0],
                [6.0, 0.0, 0.0],
            ],
            dtype=np.float64,
        ) * scale + np.asarray(offset, dtype=np.float64)
        mesh = rig.build_mesh_analysis_input(
            vertices,
            [0, 0, 1, 1, 2, 2],
            [0, 4, 9],
            edges=[[0, 1], [1, 2], [2, 3], [3, 4], [4, 5]],
        )
        return rig.analyze_rig_geometry(mesh)

    def test_uniform_scale_preserves_normalized_geometry(self):
        baseline = self._scaled_analysis(1.0)
        tiny = self._scaled_analysis(0.01)
        huge = self._scaled_analysis(100.0)
        translated = self._scaled_analysis(1.0, offset=(100.0, -20.0, 7.0))

        np.testing.assert_allclose(
            tiny.normalized_group_centroids,
            baseline.normalized_group_centroids,
            atol=1e-10,
        )
        np.testing.assert_allclose(
            huge.normalized_group_centroids,
            baseline.normalized_group_centroids,
            atol=1e-10,
        )
        np.testing.assert_allclose(
            translated.normalized_group_centroids,
            baseline.normalized_group_centroids,
            atol=1e-10,
        )
        self.assertAlmostEqual(tiny.characteristic_scale, baseline.characteristic_scale * 0.01)
        self.assertAlmostEqual(huge.characteristic_scale, baseline.characteristic_scale * 100.0)

    def test_group_geometry_uses_explicit_sparse_raw_mapping(self):
        analysis = self._scaled_analysis(1.0)

        self.assertEqual([group.compact_id for group in analysis.groups], [0, 1, 2])
        self.assertEqual([group.raw_owner_id for group in analysis.groups], [0, 4, 9])
        self.assertEqual([group.name for group in analysis.groups], ["CarBone_0", "CarBone_4", "CarBone_9"])
        np.testing.assert_array_equal(analysis.groups[1].vertex_indices, [2, 3])
        self.assertGreater(analysis.groups[1].principal_direction_confidence, 0.9)

    def test_topology_islands_are_deterministic(self):
        mesh = rig.build_mesh_analysis_input(
            [
                [0, 0, 0], [1, 0, 0], [0, 1, 0],
                [10, 0, 0], [11, 0, 0], [10, 1, 0],
            ],
            [0, 0, 0, 0, 0, 0],
            [7],
            triangles=[[0, 1, 2], [3, 4, 5]],
        )
        analysis = rig.analyze_rig_geometry(mesh)

        self.assertEqual(len(analysis.groups[0].topology_islands), 2)
        np.testing.assert_array_equal(analysis.groups[0].topology_islands[0], [0, 1, 2])
        np.testing.assert_array_equal(analysis.groups[0].topology_islands[1], [3, 4, 5])
        self.assertTrue(any("2 disconnected" in warning for warning in analysis.warnings))

    def test_co_located_groups_use_finite_fallback_scale(self):
        mesh = rig.build_mesh_analysis_input(
            [[0, 0, 0], [1, 0, 0], [0, 0, 0], [1, 0, 0]],
            [0, 0, 1, 1],
            [0, 1],
            edges=[[0, 1], [2, 3]],
        )
        analysis = rig.analyze_rig_geometry(mesh)

        self.assertTrue(np.isfinite(analysis.characteristic_scale))
        self.assertGreater(analysis.characteristic_scale, 0.0)
        self.assertTrue(np.isfinite(analysis.normalized_group_centroids).all())

    def test_input_validation_rejects_bad_lengths_and_indices(self):
        with self.assertRaisesRegex(ValueError, "Owner count"):
            rig.build_mesh_analysis_input([[0, 0, 0]], [], [])
        with self.assertRaisesRegex(ValueError, "Triangle indices"):
            rig.build_mesh_analysis_input(
                [[0, 0, 0]], [-1], [], triangles=[[0, 1, 0]]
            )

    def test_empty_mesh_produces_a_safe_empty_analysis(self):
        mesh = rig.build_mesh_analysis_input([], [], [])
        analysis = rig.analyze_rig_geometry(mesh)

        self.assertEqual(analysis.characteristic_scale, 1.0)
        self.assertEqual(analysis.groups, ())
        self.assertEqual(analysis.normalized_group_centroids.shape, (0, 3))
        self.assertTrue(any("no nonempty" in warning.lower() for warning in analysis.warnings))


if __name__ == "__main__":
    unittest.main()
