from pathlib import Path
import json
import sys
import unittest

import bpy
import numpy as np


PACKAGE_ROOT = Path(__file__).parents[1]
PACKAGE_PARENT = PACKAGE_ROOT.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

from carnivores_io.utils import animation
from carnivores_io.utils.rig_reconstruction import (
    OWNER_MAPPING_PROPERTY,
    build_owner_mapping,
    owner_mapping_to_metadata,
)


class RigAdapterTests(unittest.TestCase):
    def test_legacy_preserves_groups_from_separate_centroid_clusters_by_default(self):
        mesh = bpy.data.meshes.new("LegacyClusterMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.1, 0, 0), (1, 0, 0), (1.1, 0, 0), (10, 0, 0), (10.1, 0, 0)],
            [(0, 1), (2, 3), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("LegacyClusterObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([1, 1, 2, 2, 3, 3])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)

        armature = None
        armature_data = None
        try:
            armature = animation.reconstruct_armature(obj)
            self.assertIsNotNone(armature)
            armature_data = armature.data
            self.assertEqual(len(armature.data.bones), 3)
            self.assertEqual(armature.get("carnivores_reconstruct_skipped_count", 0), 0)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_topology_proposal_applies_boundary_hierarchy(self):
        mesh = bpy.data.meshes.new("TopologyApplyMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("TopologyApplyObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(armature)
            armature_data = armature.data
            self.assertEqual(armature["carnivores_rig_algorithm"], "TOPOLOGY")
            self.assertEqual(len(armature.data.bones), 3)
            self.assertEqual(armature.data.bones["CarBone_0"].parent.name, "CarBone_4")
            self.assertEqual(armature.data.bones["CarBone_9"].parent.name, "CarBone_4")
            np.testing.assert_allclose(armature.data.bones["CarBone_0"].head_local, [0.9, 0, 0])
            np.testing.assert_allclose(armature.data.bones["CarBone_0"].tail_local, [0.1, 0, 0])
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_topology_generates_smoothed_semantic_deform_groups_from_canonical_owners(self):
        mesh = bpy.data.meshes.new("TopologySmoothMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0, 1, 0), (1, 1, 0), (2, 1, 0), (-1, 1, 0), (-2, 1, 0)],
            [(0, 1), (1, 2), (2, 3), (1, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("TopologySmoothObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([10, 10, 20, 20, 30, 30])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_smooth_weights"] = True

        armatures = []
        armature_data = []

        def deform_weights():
            values = np.zeros((len(mesh.vertices), len(obj.vertex_groups)), dtype=np.float64)
            for vertex in mesh.vertices:
                for assignment in vertex.groups:
                    values[vertex.index, assignment.group] = assignment.weight
            return values

        try:
            armature = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(armature)
            armatures.append(armature)
            armature_data.append(armature.data)
            self.assertIn("CarBone_20_L", armature.data.bones)
            self.assertIn("CarBone_30_R", armature.data.bones)
            self.assertIsNotNone(obj.vertex_groups.get("CarBone_20_L"))
            self.assertIsNotNone(obj.vertex_groups.get("CarBone_30_R"))
            self.assertTrue(any(len(vertex.groups) > 1 for vertex in mesh.vertices))
            first_weights = deform_weights()

            repeated = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(repeated)
            armatures.append(repeated)
            armature_data.append(repeated.data)
            np.testing.assert_allclose(deform_weights(), first_weights)

            cached = np.empty(len(mesh.vertices), dtype=np.int32)
            mesh.attributes[animation.OWNER_ATTR_NAME].data.foreach_get("value", cached)
            np.testing.assert_array_equal(cached, mapping.compact_per_vertex)
            self.assertTrue(armature["carnivores_reconstruct_smoothing"])
            self.assertTrue(armature["carnivores_reconstruct_semantic_naming"])
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            for armature in armatures:
                if armature.name in bpy.data.objects:
                    bpy.data.objects.remove(armature, do_unlink=True)
            for data in armature_data:
                if data.name in bpy.data.armatures:
                    bpy.data.armatures.remove(data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_blender_adapter_preserves_local_geometry_and_raw_mapping(self):
        mesh = bpy.data.meshes.new("RigAdapterMesh")
        mesh.from_pydata(
            [(0, 0, 0), (1, 0, 0), (0, 1, 0), (2, 0, 0)],
            [],
            [(0, 1, 2), (1, 3, 2)],
        )
        obj = bpy.data.objects.new("RigAdapterObject", mesh)
        obj.location = (20, -5, 3)
        obj.rotation_euler = (0.2, 0.4, 0.1)
        obj.scale = (2, 2, 2)

        mapping = build_owner_mapping([0, 0, 4, 4])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)

        try:
            analysis = animation.analyze_reconstruction_geometry(obj)

            self.assertEqual([group.raw_owner_id for group in analysis.groups], [0, 4])
            self.assertEqual([group.name for group in analysis.groups], ["CarBone_0", "CarBone_4"])
            np.testing.assert_allclose(analysis.groups[0].centroid, [0.5, 0.0, 0.0])
            self.assertEqual(analysis.mesh.triangles.shape, (2, 3))
            self.assertEqual(analysis.mesh.edges.shape, (5, 2))
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_topology_armature_shares_transformed_mesh_world_matrix(self):
        """Phase 5 10.5: armature matches the mesh's world transform, bones stay local."""
        mesh = bpy.data.meshes.new("TransformMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("TransformObject", mesh)
        obj.location = (10, -3, 2)
        obj.rotation_euler = (0.1, 0.2, 0.3)
        obj.scale = (2, 3, 4)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(armature)
            armature_data = armature.data
            # Armature world transform equals the mesh's (bones authored in local space).
            for row_a, row_b in zip(armature.matrix_world, obj.matrix_world):
                for value_a, value_b in zip(row_a, row_b):
                    self.assertAlmostEqual(value_a, value_b, places=4)
            # Bone heads stay in mesh-local space (not world).
            self.assertTrue(np.isfinite([
                animation.io_utils.get_bone_roll(bone) for bone in armature.data.bones
            ]).all())
            first_bone = armature.data.bones["CarBone_0"]
            self.assertAlmostEqual(first_bone.head_local.x, 0.9, places=4)
            self.assertAlmostEqual(first_bone.head_local.y, 0.0, places=4)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_armature_construction_failure_leaves_no_partial_object(self):
        before = {object_.name for object_ in bpy.data.objects}
        with self.assertRaises(ValueError):
            animation.io_utils.create_armature(
                ["Broken"],
                [(0.0, 0.0, 0.0)],
                [4],
                "TransactionalFailure",
                bpy.context.scene.collection,
            )
        after = {object_.name for object_ in bpy.data.objects}
        self.assertEqual(before, after)

    def _make_phase6_topology_object(self, name):
        mesh = bpy.data.meshes.new(f"{name}Mesh")
        mesh.from_pydata(
            [
                (0.0, 0.0, 0.0), (0.8, 0.0, 0.0),
                (1.0, 0.0, 0.0), (1.8, 0.0, 0.0),
                (2.0, 0.0, 0.0), (2.8, 0.0, 0.0),
            ],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_algorithm"] = "TOPOLOGY"
        obj["carnivores_reconstruct_semantic_naming"] = False
        return obj, mesh

    def test_phase6_analyze_is_non_mutating_and_preview_cleanup_is_scoped(self):
        obj, mesh = self._make_phase6_topology_object("Phase6AnalyzeObject")
        unrelated = bpy.data.objects.new("Phase6UserObject", None)
        bpy.context.scene.collection.objects.link(unrelated)
        original_groups = [group.name for group in obj.vertex_groups]
        original_assignments = [
            [(group.group, group.weight) for group in vertex.groups]
            for vertex in mesh.vertices
        ]
        armature_names = {
            item.name for item in bpy.data.objects if item.type == 'ARMATURE'
        }
        try:
            proposal, checksum = animation.analyze_topology_proposal(obj)
            animation.store_topology_proposal(obj, proposal, checksum)
            self.assertLess(len(mesh.get("carnivores_rig_proposal", "")), 256)
            self.assertTrue(
                bpy.data.texts.get(mesh.get("carnivores_rig_proposal_text", ""))
            )
            preview = animation.create_topology_preview(obj, proposal)
            self.assertTrue(preview.get("carnivores_rig_preview"))
            self.assertEqual(
                [group.name for group in obj.vertex_groups],
                original_groups,
            )
            self.assertEqual(
                [
                    [(group.group, group.weight) for group in vertex.groups]
                    for vertex in mesh.vertices
                ],
                original_assignments,
            )
            self.assertEqual(
                {item.name for item in bpy.data.objects if item.type == 'ARMATURE'},
                armature_names,
            )
            preview_objects = [
                item for item in bpy.data.objects
                if item.get("carnivores_rig_preview_source") == obj.name
            ]
            preview_curve_data = {
                item.data.name for item in preview_objects
                if item.type == 'CURVE' and item.data
            }
            self.assertTrue(preview_objects)
            obj.name = "Phase6AnalyzeObjectRenamed"
            animation.clear_topology_preview(obj)
            animation.clear_topology_proposal(obj)
            self.assertFalse(any(item.get("carnivores_rig_preview_source_id") == obj.get(animation.RECONSTRUCTION_SOURCE_ID_PROPERTY) for item in bpy.data.objects))
            self.assertFalse(any(name in bpy.data.curves for name in preview_curve_data))
            self.assertIn(unrelated.name, bpy.data.objects)
        finally:
            animation.clear_topology_preview(obj)
            animation.clear_topology_proposal(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.objects.remove(unrelated, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase6_apply_rejects_changed_settings_through_direct_api(self):
        obj, mesh = self._make_phase6_topology_object("Phase6SettingsObject")
        try:
            proposal, checksum = animation.analyze_topology_proposal(obj)
            animation.store_topology_proposal(obj, proposal, checksum)
            obj["carnivores_reconstruct_semantic_naming"] = not bool(
                proposal.settings["semantic_naming"]
            )
            with self.assertRaisesRegex(ValueError, "settings changed"):
                animation.apply_stored_topology_proposal(obj)
            self.assertFalse(
                any(item.type == 'ARMATURE' for item in bpy.data.objects if item.parent == obj)
            )
        finally:
            animation.clear_topology_proposal(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase6_apply_rejects_modified_proposal_text(self):
        obj, mesh = self._make_phase6_topology_object("Phase6PayloadHashObject")
        try:
            proposal, checksum = animation.analyze_topology_proposal(obj)
            animation.store_topology_proposal(obj, proposal, checksum)
            text = bpy.data.texts.get(mesh.get("carnivores_rig_proposal_text", ""))
            self.assertIsNotNone(text)
            original = text.as_string()
            text.clear()
            text.write(original + " ")
            with self.assertRaisesRegex(ValueError, "payload was modified"):
                animation.apply_stored_topology_proposal(obj)
            self.assertEqual(
                len(mesh.get(animation.TOPOLOGY_PROPOSAL_PAYLOAD_HASH_PROPERTY, "")),
                64,
            )
        finally:
            animation.clear_topology_proposal(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase6_apply_consumes_stored_proposal_and_validates_structure(self):
        obj, mesh = self._make_phase6_topology_object("Phase6ApplyObject")
        armature = None
        armature_data = None
        try:
            proposal, checksum = animation.analyze_topology_proposal(obj)
            animation.store_topology_proposal(obj, proposal, checksum)
            armature = animation.apply_stored_topology_proposal(obj)
            armature_data = armature.data
            self.assertEqual(armature.get("carnivores_rig_algorithm"), "TOPOLOGY")
            validation = animation.validate_stored_topology_proposal(obj)
            self.assertTrue(validation["valid"], validation["errors"])
            self.assertEqual(mesh.get("carnivores_rig_proposal_checksum"), checksum)
        finally:
            animation.clear_topology_proposal(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase6_stale_proposal_is_rejected_before_apply(self):
        obj, mesh = self._make_phase6_topology_object("Phase6StaleObject")
        try:
            proposal, checksum = animation.analyze_topology_proposal(obj)
            animation.store_topology_proposal(obj, proposal, checksum)
            mesh.vertices[0].co.x += 0.25
            mesh.update()
            with self.assertRaisesRegex(ValueError, "stale"):
                animation.apply_stored_topology_proposal(obj)
            self.assertFalse(any(item.type == 'ARMATURE' for item in bpy.data.objects if item.parent == obj))
        finally:
            animation.clear_topology_preview(obj)
            animation.clear_topology_proposal(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase6_apply_operator_rejects_stale_mesh_without_reanalysis(self):
        obj, mesh = self._make_phase6_topology_object("Phase6OperatorStaleObject")
        armature = None
        try:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            result = bpy.ops.carnivores.analyze_rig_proposal()
            self.assertEqual(result, {'FINISHED'})
            mesh.vertices[0].co.x += 0.5
            mesh.update()
            with self.assertRaisesRegex(RuntimeError, "stale"):
                bpy.ops.carnivores.apply_rig_proposal()
            armature = next(
                (
                    modifier.object for modifier in obj.modifiers
                    if modifier.type == 'ARMATURE' and modifier.object
                ),
                None,
            )
            self.assertIsNone(armature)
        finally:
            animation.clear_topology_preview(obj)
            animation.clear_topology_proposal(obj)
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)
    def test_parented_nonuniform_mesh_preserves_world_matrix(self):
        mesh = bpy.data.meshes.new("ParentTransformMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        parent = bpy.data.objects.new("ParentTransformParent", None)
        parent.location = (100, 5, -2)
        bpy.context.scene.collection.objects.link(parent)
        obj = bpy.data.objects.new("ParentTransformObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.location = (10, -3, 2)
        obj.rotation_euler = (0.1, 0.2, 0.3)
        obj.scale = (2, 3, 4)
        obj.parent = parent
        obj.matrix_parent_inverse = parent.matrix_world.inverted()
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT')
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT')
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        armature = None
        armature_data = None
        try:
            bpy.context.view_layer.update()
            before = obj.matrix_world.copy()
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            bpy.context.view_layer.update()
            np.testing.assert_allclose(np.asarray(obj.matrix_world), np.asarray(before), atol=1e-4)
            self.assertIs(obj.parent, armature)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.objects.remove(parent, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_name_safety_truncates_and_dedupes_bone_names(self):
        """Phase 5 10.4: ASCII cleaning, 32-byte limit, and duplicate resolution."""
        resolved = animation._resolve_generated_bone_names(
            ["A" * 40, "A" * 40, "B" * 20, "name_with_under", "😀emoji"]
        )
        self.assertEqual(len(resolved), 5)
        self.assertEqual(len(set(resolved)), 5)
        for name in resolved:
            self.assertLessEqual(len(name.encode("utf-8", "ignore")), 31)
        # The emoji (non-ASCII) should be stripped, and long names truncated.
        self.assertIn("B" * 20, resolved)
        self.assertTrue(any(name.startswith("A") for name in resolved))
        self.assertTrue(any(name.endswith(".1") for name in resolved))
        blender_names = animation._resolve_blender_bone_names(["A" * 63, "A" * 63])
        self.assertEqual(len(set(blender_names)), 2)
        self.assertLessEqual(max(len(name.encode("utf-8")) for name in blender_names), 63)

    def test_name_collision_keeps_owner_weights_by_compact_id(self):
        mesh = bpy.data.meshes.new("NameCollisionMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.1, 0, 0), (1, 0, 0), (1.1, 0, 0)],
            [(0, 1), (1, 2), (2, 3)],
            [],
        )
        obj = bpy.data.objects.new("NameCollisionObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([10, 10, 20, 20])
        owner_attr = mesh.attributes.new(name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT')
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT')
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        first_group = obj.vertex_groups.new(name="A😀")
        second_group = obj.vertex_groups.new(name="A")
        first_group.add([0, 1], 1.0, 'REPLACE')
        second_group.add([2, 3], 1.0, 'REPLACE')
        obj["carnivores_reconstruct_semantic_naming"] = False

        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            self.assertEqual([bone.name for bone in armature.data.bones], ["A😀", "A"])
            self.assertEqual([group.name for group in obj.vertex_groups], ["A😀", "A"])
            self.assertIn("\"export_name\":\"A\"", armature["carnivores_reconstruct_bone_name_map"])
            exported = animation.io_utils.collect_bones_and_owners(obj, np.identity(4))
            self.assertEqual(exported[0], ["A", "A.1"])
            armature.data.bones[0].name = "Renamed"
            obj.vertex_groups[0].name = "Renamed"
            renamed_export = animation.io_utils.collect_bones_and_owners(obj, np.identity(4))
            self.assertEqual(renamed_export[0], ["Renamed", "A"])
            self.assertEqual(mesh.vertices[0].groups[0].group, 0)
            self.assertEqual(mesh.vertices[2].groups[0].group, 1)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_explicit_cleanup_removes_only_recorded_skipped_groups(self):
        mesh = bpy.data.meshes.new("CleanupSkippedMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3)],
            [],
        )
        obj = bpy.data.objects.new("CleanupSkippedObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 1, 1])
        owner_attr = mesh.attributes.new(name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT')
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            skipped = obj.vertex_groups.new(name="SkippedOwner")
            skipped.add([0], 0.25, 'REPLACE')
            armature["carnivores_reconstruct_skipped"] = "77"
            armature["carnivores_reconstruct_skipped_count"] = 1
            armature["carnivores_reconstruct_skipped_details"] = json.dumps([
                {
                    "compact_id": 2,
                    "raw_owner_id": 77,
                    "reason": "EXPLICIT_TEST",
                    "blender_name": "SkippedOwner",
                    "vertex_count": 1,
                }
            ])
            bpy.context.view_layer.objects.active = obj
            obj.select_set(True)
            result = bpy.ops.carnivores.cleanup_skipped_groups()
            self.assertEqual(result, {'FINISHED'})
            self.assertIsNone(obj.vertex_groups.get("SkippedOwner"))
            self.assertEqual(armature.get("carnivores_reconstruct_skipped_count"), 0)
            self.assertEqual(armature.get("carnivores_reconstruct_skipped_details"), "[]")
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_rig_policy_create_new_keeps_existing_generated_rig(self):
        """Phase 5 10.7: CREATE_NEW preserves an existing generated armature."""
        mesh = bpy.data.meshes.new("PolicyMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("PolicyObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        first_arm = None
        second_arm = None
        first_data = None
        second_data = None
        try:
            first_arm = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(first_arm)
            first_data = first_arm.data
            # Default policy is CREATE_NEW: second run creates a new armature.
            second_arm = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(second_arm)
            second_data = second_arm.data
            self.assertNotEqual(first_arm.name, second_arm.name)
            self.assertEqual(
                obj.get("carnivores_reconstruct_rig_policy", "CREATE_NEW"), "CREATE_NEW"
            )
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            for armature in (first_arm, second_arm):
                if armature is not None and armature.name in bpy.data.objects:
                    bpy.data.objects.remove(armature, do_unlink=True)
            for data in (first_data, second_data):
                if data is not None and data.name in bpy.data.armatures:
                    bpy.data.armatures.remove(data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_rig_policy_replace_generated_removes_old_and_creates_new(self):
        """Phase 5 10.7: REPLACE_GENERATED removes the old generated rig."""
        mesh = bpy.data.meshes.new("ReplacePolicyMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("ReplacePolicyObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        first_arm = None
        second_arm = None
        first_data = None
        second_data = None
        try:
            first_arm = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(first_arm)
            first_data = first_arm.data

            obj["carnivores_reconstruct_rig_policy"] = "REPLACE_GENERATED"
            second_arm = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(second_arm)
            second_data = second_arm.data
            # The old generated armature object was removed; Blender recycles the
            # name, so verify the old object identity is invalid, not the name.
            self.assertIsNot(second_arm, first_arm)
            try:
                first_removed = first_arm.name not in bpy.data.objects
            except ReferenceError:
                first_removed = True
            self.assertTrue(first_removed)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            for armature in (first_arm, second_arm):
                if armature is None:
                    continue
                try:
                    still_present = armature.name in bpy.data.objects
                except ReferenceError:
                    still_present = False
                if still_present:
                    bpy.data.objects.remove(armature, do_unlink=True)
            for data in (first_data, second_data):
                if data is None:
                    continue
                try:
                    still_present = data.name in bpy.data.armatures
                except ReferenceError:
                    still_present = False
                if still_present:
                    bpy.data.armatures.remove(data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_rig_policy_cancel_if_rigged_refuses_second_reconstruction(self):
        """Phase 5 10.7: CANCEL_IF_RIGGED returns None when a rig already exists."""
        mesh = bpy.data.meshes.new("CancelPolicyMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("CancelPolicyObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(
            name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT'
        )
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(
            name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT'
        )
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            self.assertIsNotNone(armature)
            armature_data = armature.data
            obj["carnivores_reconstruct_rig_policy"] = "CANCEL_IF_RIGGED"
            self.assertIsNone(animation._reconstruct_armature_topology(obj))
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_update_reuses_modifier_clears_action_and_restores_context(self):
        mesh = bpy.data.meshes.new("UpdatePolicyMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("UpdatePolicyObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT')
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT')
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        armature = None
        armature_data = None
        action = None
        try:
            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            action = bpy.data.actions.new("StaleRigAction")
            armature.animation_data_create().action = action
            obj["carnivores_reconstruct_rig_policy"] = "UPDATE_GENERATED"

            updated = animation._reconstruct_armature_topology(obj)
            self.assertIs(updated, armature)
            self.assertEqual(
                len([modifier for modifier in obj.modifiers if modifier.type == 'ARMATURE']),
                1,
            )
            self.assertIsNone(armature.animation_data)
            self.assertIs(bpy.context.view_layer.objects.active, obj)
            self.assertTrue(obj.select_get())
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None:
                try:
                    if armature_data.name in bpy.data.armatures:
                        bpy.data.armatures.remove(armature_data)
                except ReferenceError:
                    pass
            if action is not None and action.name in bpy.data.actions:
                bpy.data.actions.remove(action)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_update_refuses_shared_generated_armature(self):
        mesh = bpy.data.meshes.new("SharedUpdateMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("SharedUpdateObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT')
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        shared_mesh = bpy.data.meshes.new("SharedUpdateUserMesh")
        shared_mesh.from_pydata([(0, 0, 0), (1, 0, 0)], [(0, 1)], [])
        shared_obj = bpy.data.objects.new("SharedUpdateUserObject", shared_mesh)
        bpy.context.scene.collection.objects.link(shared_obj)
        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            shared_modifier = shared_obj.modifiers.new("SharedArmature", type='ARMATURE')
            shared_modifier.object = armature
            obj["carnivores_reconstruct_rig_policy"] = "UPDATE_GENERATED"
            self.assertIsNone(animation._reconstruct_armature_topology(obj))
            self.assertIs(armature.data, armature_data)
            self.assertIs(shared_modifier.object, armature)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.objects.remove(shared_obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)
            if shared_mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(shared_mesh)

    def test_update_failure_restores_old_data_action_and_constraints(self):
        mesh = bpy.data.meshes.new("UpdateFailureMesh")
        mesh.from_pydata(
            [(0, 0, 0), (0.8, 0, 0), (1, 0, 0), (1.8, 0, 0), (2, 0, 0), (2.8, 0, 0)],
            [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)],
            [],
        )
        obj = bpy.data.objects.new("UpdateFailureObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4, 9, 9])
        owner_attr = mesh.attributes.new(name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT')
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_semantic_naming"] = False

        armature = None
        old_data = None
        action = None
        original_assign = animation.io_utils.assign_armature_modifier
        try:
            armature = animation._reconstruct_armature_topology(obj)
            old_data = armature.data
            old_bone_names = [bone.name for bone in old_data.bones]
            action = bpy.data.actions.new("UpdateFailureAction")
            armature.animation_data_create().action = action
            armature.constraints.new(type='LIMIT_ROTATION')
            old_metadata = armature.get("carnivores_rig_algorithm")
            obj["carnivores_reconstruct_rig_policy"] = "UPDATE_GENERATED"

            def fail_assignment(*_args, **_kwargs):
                raise RuntimeError("injected modifier failure")

            animation.io_utils.assign_armature_modifier = fail_assignment
            with self.assertRaisesRegex(RuntimeError, "injected modifier failure"):
                animation._reconstruct_armature_topology(obj)

            self.assertIs(armature.data, old_data)
            self.assertEqual([bone.name for bone in armature.data.bones], old_bone_names)
            self.assertIsNotNone(armature.animation_data)
            self.assertIs(armature.animation_data.action, action)
            self.assertEqual(len(armature.constraints), 1)
            self.assertEqual(armature.get("carnivores_rig_algorithm"), old_metadata)
            self.assertTrue(any(
                modifier.type == 'ARMATURE' and modifier.object == armature
                for modifier in obj.modifiers
            ))
        finally:
            animation.io_utils.assign_armature_modifier = original_assign
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if old_data is not None and old_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(old_data)
            if action is not None and action.name in bpy.data.actions:
                bpy.data.actions.remove(action)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_hook_mapping_reports_no_group_fallback(self):
        mesh = bpy.data.meshes.new("Phase7HookMesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (2, 0, 0)], [(0, 1), (1, 2)], [])
        obj = bpy.data.objects.new("Phase7HookObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        hook = bpy.data.objects.new("Phase7Hook", None)
        bpy.context.scene.collection.objects.link(hook)
        group = obj.vertex_groups.new(name="Phase7Hook")
        group.add([0], 1.0, 'REPLACE')
        modifier = obj.modifiers.new("Phase7HookModifier", type='HOOK')
        modifier.object = hook
        modifier.vertex_group = group.name
        try:
            mapping = animation.io_utils.collect_export_mapping(obj, np.identity(4))
            self.assertEqual(mapping.source, "HOOKS")
            np.testing.assert_array_equal(mapping.vertex_owners, [0, 0, 0])
            self.assertEqual(mapping.no_group_vertices, (1, 2))
            self.assertEqual(mapping.fallback_to_root_vertices, (1, 2))
            self.assertFalse(mapping.errors)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if hook.name in bpy.data.objects:
                bpy.data.objects.remove(hook, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_dry_run_matches_legacy_export_tuple(self):
        obj, mesh = self._make_phase6_topology_object("Phase7DryRunObject")
        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            before_groups = [group.name for group in obj.vertex_groups]
            before_checksum = animation.generated_weight_checksum(obj)
            dry_run = animation.io_utils.collect_export_mapping(obj, np.identity(4))
            legacy = animation.io_utils.collect_bones_and_owners(obj, np.identity(4))
            self.assertEqual([bone.export_name for bone in dry_run.bones], legacy[0])
            np.testing.assert_array_equal(dry_run.vertex_owners, legacy[3])
            self.assertEqual(before_groups, [group.name for group in obj.vertex_groups])
            self.assertEqual(before_checksum, animation.generated_weight_checksum(obj))
            self.assertTrue(all("fallback" not in warning.lower() for warning in dry_run.warnings))
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_reconciliation_reports_exact_one_hot_pass(self):
        obj, mesh = self._make_phase6_topology_object("Phase7PassObject")
        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            owners = np.empty(len(mesh.vertices), dtype=np.int32)
            mesh.attributes[animation.OWNER_ATTR_NAME].data.foreach_get("value", owners)
            raw_ids = np.array([0, 4, 9], dtype=np.int32)
            result = animation._build_rig_export_reconciliation(
                obj, armature, owners, raw_ids
            )
            self.assertEqual(result.level, "PASS")
            self.assertEqual(result.counts["export_drift"], 0)
            self.assertEqual(result.counts["dominant_drift"], 0)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_smoothing_drift_is_expected_when_export_is_exact(self):
        obj, mesh = self._make_phase6_topology_object("Phase7SmoothObject")
        armature = None
        armature_data = None
        try:
            obj["carnivores_reconstruct_smooth_weights"] = True
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            source_group = obj.vertex_groups.get("CarBone_4")
            self.assertIsNotNone(source_group)
            source_group.add([0], 2.0, 'REPLACE')
            armature[animation.GENERATED_WEIGHT_CHECKSUM_PROPERTY] = animation.generated_weight_checksum(obj)
            owners = np.empty(len(mesh.vertices), dtype=np.int32)
            mesh.attributes[animation.OWNER_ATTR_NAME].data.foreach_get("value", owners)
            raw_ids = np.array([0, 4, 9], dtype=np.int32)
            result = animation._build_rig_export_reconciliation(
                obj, armature, owners, raw_ids
            )
            self.assertEqual(result.level, "EXPECTED_DRIFT")
            self.assertGreater(result.counts["dominant_drift"], 0)
            self.assertGreater(result.counts["export_drift"], 0)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_skipped_group_fallback_has_vertex_count(self):
        mesh = bpy.data.meshes.new("Phase7SkippedMesh")
        mesh.from_pydata(
            [(0, 0, 0), (1, 0, 0), (10, 0, 0), (11, 0, 0)],
            [(0, 1), (2, 3)],
            [],
        )
        obj = bpy.data.objects.new("Phase7SkippedObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0, 4, 4])
        owner_attr = mesh.attributes.new(name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT')
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        source_attr = mesh.attributes.new(name=animation.OWNER_SOURCE_ATTR_NAME, type='INT', domain='POINT')
        source_attr.data.foreach_set("value", mapping.raw_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)
        obj["carnivores_reconstruct_algorithm"] = "TOPOLOGY"
        obj["carnivores_reconstruct_component_policy"] = "SKIP"
        obj["carnivores_reconstruct_semantic_naming"] = False
        armature = None
        armature_data = None
        try:
            proposal, _checksum = animation.analyze_topology_proposal(obj)
            self.assertEqual(proposal.skipped_groups, (1,))
            armature = animation._reconstruct_armature_topology(obj, proposal=proposal)
            armature_data = armature.data
            owners = np.empty(len(mesh.vertices), dtype=np.int32)
            owner_attr.data.foreach_get("value", owners)
            result = animation._build_rig_export_reconciliation(
                obj, armature, owners, mapping.raw_by_compact,
                skipped_groups=proposal.skipped_groups, proposal=proposal,
            )
            self.assertEqual(result.level, "ERROR")
            self.assertEqual(result.counts["skipped_owner_vertices"], 2)
            self.assertEqual(result.counts["skipped_fallback_to_root_vertices"], 2)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_missing_deform_group_is_error(self):
        obj, mesh = self._make_phase6_topology_object("Phase7MissingDeformObject")
        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            removed = obj.vertex_groups.get("CarBone_4")
            self.assertIsNotNone(removed)
            obj.vertex_groups.remove(removed)
            owners = np.empty(len(mesh.vertices), dtype=np.int32)
            mesh.attributes[animation.OWNER_ATTR_NAME].data.foreach_get("value", owners)
            result = animation._build_rig_export_reconciliation(
                obj, armature, owners, np.array([0, 4, 9], dtype=np.int32)
            )
            self.assertEqual(result.level, "ERROR")
            self.assertGreater(result.counts["missing_deform_assignments"], 0)
            self.assertGreater(result.counts["no_deform_vertices"], 0)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_explicit_metadata_disables_fuzzy_group_matching(self):
        obj, mesh = self._make_phase6_topology_object("Phase7ExplicitMetadataObject")
        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            group = obj.vertex_groups.get("CarBone_0")
            self.assertIsNotNone(group)
            group.name = "CarBone_0.001"
            mapping = animation.io_utils.collect_export_mapping(obj, np.identity(4))
            self.assertFalse(mapping.fuzzy_matches)
            self.assertTrue(mapping.unmatched_generated_groups)
            self.assertTrue(mapping.unmatched_vertices)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_malformed_generated_name_map_is_error(self):
        obj, mesh = self._make_phase6_topology_object("Phase7MalformedMapObject")
        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            armature["carnivores_reconstruct_bone_name_map"] = "not-json"
            mapping = animation.io_utils.collect_export_mapping(obj, np.identity(4))
            self.assertTrue(mapping.errors)
            self.assertEqual(mapping.vertex_owners.shape[0], len(mesh.vertices))
            self.assertEqual(
                animation.io_utils.collect_bones_and_owners(obj, np.identity(4))[0],
                [bone.export_name for bone in mapping.bones],
            )
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_export_name_collision_is_reported(self):
        obj, mesh = self._make_phase6_topology_object("Phase7CollisionObject")
        armature = None
        armature_data = None
        try:
            armature = animation._reconstruct_armature_topology(obj)
            armature_data = armature.data
            entries = json.loads(armature["carnivores_reconstruct_bone_name_map"])
            for entry in entries:
                entry["export_name"] = "Duplicate"
            armature["carnivores_reconstruct_bone_name_map"] = json.dumps(entries)
            owners = np.empty(len(mesh.vertices), dtype=np.int32)
            mesh.attributes[animation.OWNER_ATTR_NAME].data.foreach_get("value", owners)
            result = animation._build_rig_export_reconciliation(
                obj, armature, owners, np.array([0, 4, 9], dtype=np.int32)
            )
            self.assertEqual(result.level, "WARNING")
            self.assertGreater(result.counts["name_collisions"], 0)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_phase7_forced_edge_is_reported_in_reconciliation(self):
        obj, mesh = self._make_phase6_topology_object("Phase7ForcedEdgeObject")
        armature = None
        armature_data = None
        try:
            proposal, _checksum = animation.analyze_topology_proposal(
                obj, forced_edges=[(0, 2)]
            )
            armature = animation._reconstruct_armature_topology(obj, proposal=proposal)
            armature_data = armature.data
            owners = np.empty(len(mesh.vertices), dtype=np.int32)
            mesh.attributes[animation.OWNER_ATTR_NAME].data.foreach_get("value", owners)
            result = animation._build_rig_export_reconciliation(
                obj, armature, owners, np.array([0, 4, 9], dtype=np.int32),
                proposal=proposal,
            )
            self.assertIn(result.level, {"WARNING", "ERROR"})
            self.assertTrue(any("forced" in warning.lower() for warning in result.warnings))
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if armature is not None and armature.name in bpy.data.objects:
                bpy.data.objects.remove(armature, do_unlink=True)
            if armature_data is not None and armature_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(armature_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_non_generated_armature_is_not_replaced(self):
        mesh = bpy.data.meshes.new("UserRigMesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0)], [(0, 1)], [])
        obj = bpy.data.objects.new("UserRigMeshObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        mapping = build_owner_mapping([0, 0])
        owner_attr = mesh.attributes.new(name=animation.OWNER_ATTR_NAME, type='INT', domain='POINT')
        owner_attr.data.foreach_set("value", mapping.compact_per_vertex)
        mesh[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(mapping)

        user_data = bpy.data.armatures.new("UserRigData")
        user_armature = bpy.data.objects.new("UserRigObject", user_data)
        bpy.context.scene.collection.objects.link(user_armature)
        bpy.context.view_layer.objects.active = user_armature
        user_armature.select_set(True)
        bpy.ops.object.mode_set(mode='EDIT')
        user_bone = user_data.edit_bones.new("UserBone")
        user_bone.head = (0, 0, 0)
        user_bone.tail = (0, 1, 0)
        bpy.ops.object.mode_set(mode='OBJECT')
        modifier = obj.modifiers.new("UserArmature", type='ARMATURE')
        modifier.object = user_armature
        obj["carnivores_reconstruct_rig_policy"] = "REPLACE_GENERATED"

        try:
            self.assertIsNone(animation._reconstruct_armature_topology(obj))
            self.assertIn(user_armature.name, bpy.data.objects)
            self.assertIn(modifier.name, obj.modifiers)
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if user_armature.name in bpy.data.objects:
                bpy.data.objects.remove(user_armature, do_unlink=True)
            if user_data.name in bpy.data.armatures:
                bpy.data.armatures.remove(user_data)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)


if __name__ == "__main__":
    unittest.main()
