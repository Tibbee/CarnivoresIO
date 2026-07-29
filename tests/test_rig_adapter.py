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
