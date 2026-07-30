from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
