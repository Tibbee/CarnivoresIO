import importlib.util
from pathlib import Path
import unittest

import numpy as np


MODULE_PATH = Path(__file__).parents[1] / "utils" / "rig_reconstruction.py"
SPEC = importlib.util.spec_from_file_location("rig_reconciliation", MODULE_PATH)
rig = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rig)


class RigReconciliationTests(unittest.TestCase):
    def _mapping(
        self,
        owners,
        raw_ids=(0, 4, 9),
        *,
        fuzzy=(),
        fallback=(),
        no_group=(),
        unowned=(),
        warnings=(),
        errors=(),
    ):
        bones = tuple(
            rig.ExportBoneRecord(
                export_index=index,
                compact_id=index,
                raw_owner_id=raw_id,
                source_name=f"CarBone_{raw_id}",
                blender_name=f"Bone_{index}",
                export_name=f"CarBone_{raw_id}",
                parent_export_index=(index - 1 if index else -1),
            )
            for index, raw_id in enumerate(raw_ids)
        )
        return rig.ExportMapping(
            source="ARMATURE",
            bones=bones,
            bone_positions=tuple((float(index), 0.0, 0.0) for index in range(len(bones))),
            vertex_owners=np.asarray(owners, dtype=np.int32),
            fuzzy_matches=tuple(fuzzy),
            fallback_to_root_vertices=tuple(fallback),
            no_group_vertices=tuple(no_group),
            unowned_vertices=tuple(unowned),
            warnings=tuple(warnings),
            errors=tuple(errors),
        )

    def _reconcile(self, canonical, dominant, exported, *, mapping=None, skipped=(), smoothing=False, expected=None, actual=None):
        return rig.reconcile_rig_export(
            canonical_compact_owners=canonical,
            canonical_raw_by_compact=[0, 4, 9],
            generated_dominant_owners=dominant,
            export_owner_by_vertex=exported,
            export_bones=mapping.bones if mapping else (),
            skipped_groups=skipped,
            export_mapping=mapping,
            smoothing_enabled=smoothing,
            generated_weight_checksum=actual,
            expected_weight_checksum=expected,
        )

    def test_exact_one_hot_round_trip_is_pass(self):
        mapping = self._mapping([0, 0, 1, 1, 2, 2])
        result = self._reconcile(
            [0, 0, 1, 1, 2, 2],
            [0, 0, 1, 1, 2, 2],
            [0, 0, 1, 1, 2, 2],
            mapping=mapping,
        )

        self.assertEqual(result.level, "PASS")
        self.assertTrue(result.valid)
        self.assertEqual(result.counts["export_drift"], 0)
        self.assertEqual(result.counts["dominant_drift"], 0)

    def test_smoothing_only_dominant_drift_is_expected(self):
        mapping = self._mapping([0, 0, 1, 1])
        result = self._reconcile(
            [0, 0, 1, 1],
            [0, 1, 1, 1],
            [0, 0, 1, 1],
            mapping=mapping,
            smoothing=True,
        )

        self.assertEqual(result.level, "EXPECTED_DRIFT")
        self.assertTrue(result.valid)
        self.assertEqual(result.counts["dominant_drift"], 1)
        self.assertEqual(result.counts["export_drift"], 0)

    def test_unowned_vertices_are_counted_without_owned_mismatch(self):
        mapping = self._mapping([-1, 0, -1, 1])
        result = self._reconcile(
            [-1, 0, -1, 1],
            [-1, 0, -1, 1],
            [0, 0, 0, 1],
            mapping=mapping,
        )

        self.assertEqual(result.level, "WARNING")
        self.assertEqual(result.counts["unowned_vertices"], 2)
        self.assertEqual(result.counts["unmatched_owned_vertices"], 0)

    def test_no_group_and_unowned_vertices_are_counted_separately(self):
        mapping = self._mapping(
            [0, -1, -1],
            fallback=[1, 2],
            no_group=[1, 2],
            unowned=[1, 2],
        )
        result = self._reconcile(
            [0, -1, -1],
            [0, -1, -1],
            [0, 0, 0],
            mapping=mapping,
        )

        self.assertEqual(result.level, "WARNING")
        self.assertEqual(result.counts["unowned_vertices"], 2)
        self.assertEqual(result.counts["no_group_vertices"], 2)
        self.assertEqual(result.counts["unmatched_owned_vertices"], 0)

    def test_out_of_range_dominant_owner_is_error(self):
        mapping = self._mapping([0])
        result = self._reconcile(
            [0],
            [3],
            [0],
            mapping=mapping,
        )

        self.assertEqual(result.level, "ERROR")
        self.assertTrue(any("dominant owners" in message for message in result.errors))

    def test_export_mapping_error_is_not_hidden_by_smoothing(self):
        mapping = self._mapping([0], errors=("ambiguous mapping",))
        result = self._reconcile(
            [0],
            [0],
            [0],
            mapping=mapping,
            smoothing=True,
        )

        self.assertEqual(result.level, "ERROR")
        self.assertTrue(any("ambiguous" in message for message in result.errors))

    def test_fallback_is_reported_as_warning(self):
        mapping = self._mapping([0, 1], fallback=[1])
        result = self._reconcile(
            [0, 1],
            [0, 1],
            [0, 0],
            mapping=mapping,
        )

        self.assertEqual(result.level, "WARNING")
        self.assertEqual(result.counts["fallback_to_root_vertices"], 1)
        self.assertTrue(any("root 0" in message for message in result.warnings))

    def test_skipped_group_root_fallback_is_error_with_count(self):
        mapping = self._mapping([0, 0, 1], fallback=[2])
        result = self._reconcile(
            [0, 0, 2],
            [0, 0, -1],
            [0, 0, 0],
            mapping=mapping,
            skipped=(2,),
        )

        self.assertEqual(result.level, "ERROR")
        self.assertFalse(result.valid)
        self.assertEqual(result.counts["skipped_fallback_to_root_vertices"], 1)
        self.assertEqual(result.skipped_groups[0]["compact_id"], 2)

    def test_missing_deform_bone_is_error(self):
        mapping = self._mapping([0, 0, 1])
        result = self._reconcile(
            [0, 1, 1],
            [0, -1, 1],
            [0, 1, 1],
            mapping=mapping,
        )

        self.assertEqual(result.level, "ERROR")
        self.assertEqual(result.counts["missing_deform_assignments"], 1)

    def test_unmatched_owned_export_vertex_is_error(self):
        mapping = self._mapping([0, 0, 1], errors=("export mapping failure",))
        result = self._reconcile(
            [0, 1, 1],
            [0, 1, 1],
            [0, -1, 1],
            mapping=mapping,
        )

        self.assertEqual(result.level, "ERROR")
        self.assertEqual(result.counts["unmatched_owned_vertices"], 1)
        self.assertTrue(any("export mapping failure" in message for message in result.errors))

    def test_fuzzy_match_is_structured_warning(self):
        mapping = self._mapping(
            [0, 1],
            raw_ids=(0, 4),
            fuzzy=({"vertex_group": "Bone.001", "bone_name": "Bone", "candidate_count": 1},),
        )
        result = rig.reconcile_rig_export(
            canonical_compact_owners=[0, 1],
            canonical_raw_by_compact=[0, 4],
            generated_dominant_owners=[0, 1],
            export_owner_by_vertex=[0, 1],
            export_bones=mapping.bones,
            export_mapping=mapping,
        )

        self.assertEqual(result.level, "WARNING")
        self.assertEqual(result.counts["fuzzy_matches"], 1)

    def test_invalid_parent_mapping_is_error(self):
        mapping = self._mapping([0, 1])
        bad_bones = (
            mapping.bones[0],
            rig.ExportBoneRecord(
                export_index=1,
                compact_id=1,
                raw_owner_id=4,
                source_name="CarBone_4",
                blender_name="Bone_1",
                export_name="CarBone_4",
                parent_export_index=1,
            ),
        )
        result = rig.reconcile_rig_export(
            canonical_compact_owners=[0, 1],
            canonical_raw_by_compact=[0, 4],
            generated_dominant_owners=[0, 1],
            export_owner_by_vertex=[0, 1],
            export_bones=bad_bones,
        )

        self.assertEqual(result.level, "ERROR")
        self.assertTrue(any("invalid parent" in message for message in result.errors))

    def test_checksum_mismatch_is_error(self):
        mapping = self._mapping([0, 1], raw_ids=(0, 4))
        result = self._reconcile(
            [0, 1],
            [0, 1],
            [0, 1],
            mapping=mapping,
            actual="actual",
            expected="stored",
        )

        self.assertEqual(result.level, "ERROR")
        self.assertTrue(any("checksum" in message.lower() for message in result.errors))


if __name__ == "__main__":
    unittest.main()
