import importlib.util
from pathlib import Path
import unittest

import numpy as np


MODULE_PATH = Path(__file__).parents[1] / "utils" / "rig_reconstruction.py"
SPEC = importlib.util.spec_from_file_location("rig_reconstruction", MODULE_PATH)
rig_reconstruction = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rig_reconstruction)


class OwnerMappingTests(unittest.TestCase):
    def test_sparse_raw_ids_include_zero_and_preserve_unowned(self):
        raw = np.array([-1, 0, 1, 4, 9, 0], dtype=np.int16)
        original = raw.copy()

        mapping = rig_reconstruction.build_owner_mapping(raw)

        np.testing.assert_array_equal(raw, original)
        np.testing.assert_array_equal(mapping.raw_per_vertex, original.astype(np.int32))
        np.testing.assert_array_equal(mapping.compact_per_vertex, [-1, 0, 1, 2, 3, 0])
        np.testing.assert_array_equal(mapping.raw_by_compact, [0, 1, 4, 9])
        self.assertEqual(mapping.compact_by_raw, {0: 0, 1: 1, 4: 2, 9: 3})
        self.assertEqual(mapping.bone_names, ["CarBone_0", "CarBone_1", "CarBone_4", "CarBone_9"])

    def test_all_unowned_has_no_groups(self):
        mapping = rig_reconstruction.build_owner_mapping([-1, -2, -1])
        self.assertEqual(mapping.group_count, 0)
        np.testing.assert_array_equal(mapping.compact_per_vertex, [-1, -1, -1])
        np.testing.assert_array_equal(mapping.unowned_vertex_indices, [0, 1, 2])

    def test_metadata_round_trip_preserves_compact_order(self):
        mapping = rig_reconstruction.build_owner_mapping([9, 0, 4])
        metadata = rig_reconstruction.owner_mapping_to_metadata(mapping)
        restored = rig_reconstruction.raw_ids_from_metadata(metadata)
        np.testing.assert_array_equal(restored, [0, 4, 9])

    def test_invalid_metadata_is_rejected(self):
        self.assertIsNone(rig_reconstruction.raw_ids_from_metadata("not json"))
        self.assertIsNone(
            rig_reconstruction.raw_ids_from_metadata(
                '{"schema_version":2,"raw_by_compact":[1,1]}'
            )
        )


if __name__ == "__main__":
    unittest.main()
