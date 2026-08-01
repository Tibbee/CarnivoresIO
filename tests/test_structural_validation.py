from pathlib import Path
import sys
import tempfile
import unittest

import bpy
import numpy as np


PACKAGE_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PACKAGE_ROOT.parent))
from carnivores_io.core.core import CAR_HEADER_DTYPE, FACE_DTYPE, HEADER_DTYPE, VERTEX_DTYPE
from carnivores_io.parsers.parse_3df import parse_3df
from carnivores_io.parsers.parse_car import parse_car
from carnivores_io.parsers.export_car import export_car


class StructuralValidationTests(unittest.TestCase):
    def write_3df(self, path, face_indices):
        header = np.zeros(1, dtype=HEADER_DTYPE)
        header["vertex_count"] = 3
        header["face_count"] = 1
        header["bone_count"] = 0
        header["texture_size"] = 0

        faces = np.zeros(1, dtype=FACE_DTYPE)
        faces["v"][0] = face_indices

        vertices = np.zeros(3, dtype=VERTEX_DTYPE)
        vertices["owner"] = -1

        with open(path, "wb") as fixture:
            header.tofile(fixture)
            faces.tofile(fixture)
            vertices.tofile(fixture)

    def test_structural_checks_run_when_compatibility_checks_are_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = str(Path(temp_dir) / "invalid.3df")
            self.write_3df(fixture_path, [0, 1, 9])
            with self.assertRaisesRegex(ValueError, "face-vertex indices"):
                parse_3df(
                    fixture_path,
                    validate=False,
                    parse_texture=False,
                    flip_handedness=False,
                )

    def test_car_owner_zero_and_minus_one_reach_explicit_mapping(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = str(Path(temp_dir) / "owners.car")
            header = np.zeros(1, dtype=CAR_HEADER_DTYPE)
            header["model_name"] = b"owners msc: #"
            header["vertex_count"] = 3
            header["face_count"] = 1

            faces = np.zeros(1, dtype=FACE_DTYPE)
            faces["v"][0] = [0, 1, 2]
            vertices = np.zeros(3, dtype=VERTEX_DTYPE)
            vertices["owner"] = [-1, 0, 4]

            with open(fixture_path, "wb") as fixture:
                header.tofile(fixture)
                faces.tofile(fixture)
                vertices.tofile(fixture)
                np.full(64, -1, dtype="<i4").tofile(fixture)

            parsed = parse_car(
                fixture_path,
                validate=False,
                parse_texture=False,
                flip_handedness=False,
                import_sounds=False,
            )

        parsed_vertices, bone_names, owner_source = parsed[4], parsed[5], parsed[6]
        np.testing.assert_array_equal(parsed_vertices["owner"], [-1, 0, 4])
        np.testing.assert_array_equal(owner_source, [-1, 0, 4])
        np.testing.assert_array_equal(bone_names, ["CarBone_0", "CarBone_4"])

    def test_car_can_skip_animation_and_sound_payload_allocations(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = str(Path(temp_dir) / "optional-payloads.car")
            header = np.zeros(1, dtype=CAR_HEADER_DTYPE)
            header["model_name"] = b"optional msc: #"
            header["vertex_count"] = 3
            header["face_count"] = 1
            header["ani_count"] = 1
            header["sfx_count"] = 1

            faces = np.zeros(1, dtype=FACE_DTYPE)
            faces["v"][0] = [0, 1, 2]
            vertices = np.zeros(3, dtype=VERTEX_DTYPE)
            animation_positions = np.arange(9, dtype="<i2")
            sound_samples = np.array([100, -100, 200, -200], dtype="<i2")
            cross_ref = np.full(64, -1, dtype="<i4")
            cross_ref[0] = 0

            with open(fixture_path, "wb") as fixture:
                header.tofile(fixture)
                faces.tofile(fixture)
                vertices.tofile(fixture)
                fixture.write(b"Walk".ljust(32, b"\0"))
                np.array([15, 1], dtype="<i4").tofile(fixture)
                animation_positions.tofile(fixture)
                fixture.write(b"Step".ljust(32, b"\0"))
                np.array([sound_samples.nbytes], dtype="<i4").tofile(fixture)
                sound_samples.tofile(fixture)
                cross_ref.tofile(fixture)

            parsed = parse_car(
                fixture_path,
                validate=False,
                parse_texture=False,
                flip_handedness=False,
                import_sounds=False,
                parse_animations=False,
            )

        animations, sounds, parsed_cross_ref = parsed[10], parsed[11], parsed[12]
        self.assertEqual(len(animations), 1)
        self.assertIsNone(animations[0]["positions"])
        self.assertEqual(sounds, [])
        self.assertEqual(int(parsed_cross_ref[0]), 0)

    def test_car_export_preserves_zero_based_owner_zero(self):
        mesh = bpy.data.meshes.new("ValidationOwnerMesh")
        mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        obj = bpy.data.objects.new("ValidationOwnerObject", mesh)
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                fixture_path = str(Path(temp_dir) / "owner-export.car")
                export_car(
                    fixture_path,
                    obj,
                    np.identity(4),
                    export_textures=False,
                    flip_handedness=False,
                )
                parsed = parse_car(
                    fixture_path,
                    validate=False,
                    parse_texture=False,
                    flip_handedness=False,
                    import_sounds=False,
                )
            np.testing.assert_array_equal(parsed[4]["owner"], [0, 0, 0])
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)

    def test_signed_owner_minus_one_round_trips_through_parser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = str(Path(temp_dir) / "signed-owner.3df")
            self.write_3df(fixture_path, [0, 1, 2])
            _header, _faces, _uvs, vertices, *_rest = parse_3df(
                fixture_path,
                validate=False,
                parse_texture=False,
                flip_handedness=False,
            )

        np.testing.assert_array_equal(vertices["owner"], [-1, -1, -1])


if __name__ == "__main__":
    unittest.main()
