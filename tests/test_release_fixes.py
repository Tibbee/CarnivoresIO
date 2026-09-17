"""Regression tests for the import/export robustness fixes.

Covers the IMPROVEMENTS.md section 3 audit items that were implemented:
silent CAR animation drops, absolute shape-key resync, the shape-key fast
path modifier rule, empty-texture import, unified name sanitization,
atomic export writes, and the 3DN bone-count guard.
"""

import io
import os
import tempfile
import unittest
from pathlib import Path

import bpy
import numpy as np

from carnivores_io.core.core import BONE_DTYPE, FACE_DTYPE, HEADER_DTYPE, VERTEX_DTYPE
from carnivores_io.parsers.validate import (
    SERIALIZED_NAME_BYTES,
    decode_serialized_name,
    serialize_name,
    truncate_serialized_name,
)
from carnivores_io.parsers.parse_3df import ParserContext, parse_3df, parse_3df_bones
from carnivores_io.parsers.export_car import gather_car_animations, export_car
from carnivores_io.utils.common import atomic_output_file
from carnivores_io.utils.animation import (
    can_use_shape_key_fast_path,
    keyframe_shape_key_animation_as_action,
    push_shape_key_action_to_nla,
)


class NameSanitizationTests(unittest.TestCase):
    """The single serialization rule shared by parsers, validation, and exporters."""

    def test_decode_splits_at_first_nul(self):
        self.assertEqual(decode_serialized_name(b"Root\x00junk" + b"\x00" * 24), "Root")
        self.assertEqual(decode_serialized_name(b"Root" + b"\x00" * 28), "Root")
        self.assertEqual(decode_serialized_name(b"\x00" * 32), "")

    def test_truncate_is_byte_aware(self):
        truncated, was_truncated = truncate_serialized_name("A" * 40)
        self.assertTrue(was_truncated)
        self.assertEqual(len(truncated), SERIALIZED_NAME_BYTES)
        self.assertEqual(len(truncated.encode('ascii')), SERIALIZED_NAME_BYTES)

        kept, was_truncated = truncate_serialized_name("Short")
        self.assertFalse(was_truncated)
        self.assertEqual(kept, "Short")

    def test_serialize_name_pads_and_limits(self):
        encoded = serialize_name("abc")
        self.assertEqual(encoded, b"abc" + b"\x00" * 29)
        self.assertEqual(len(serialize_name("A" * 40)), SERIALIZED_NAME_BYTES)


class ParseBoneNameTests(unittest.TestCase):
    def _bone_file(self, name_bytes):
        bone = np.zeros(1, dtype=BONE_DTYPE)
        bone["name"][0] = name_bytes
        handle = tempfile.NamedTemporaryFile(suffix=".bones", delete=False)
        handle.write(bone.tobytes())
        handle.close()
        self.addCleanup(os.remove, handle.name)
        return handle.name

    def test_embedded_nul_does_not_survive_import(self):
        from carnivores_io.parsers.parse_3df import ParserContext

        context = ParserContext()
        _bones, bone_names = parse_3df_bones(
            open(self._bone_file(b"Root\x00junk"), "rb"), 1, context=context
        )
        self.assertEqual(bone_names[0], "Root")

    def test_empty_name_uses_placeholder(self):
        from carnivores_io.parsers.parse_3df import ParserContext

        context = ParserContext()
        _bones, bone_names = parse_3df_bones(
            open(self._bone_file(b"\x00" * 32), "rb"), 1, context=context
        )
        self.assertEqual(bone_names[0], "Bone_0")


class AtomicWriteTests(unittest.TestCase):
    def test_success_replaces_and_leaves_no_temp_files(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "model.car"
            path.write_bytes(b"OLD")
            with atomic_output_file(str(path)) as f:
                f.write(b"NEW")
            self.assertEqual(path.read_bytes(), b"NEW")
            leftovers = [p for p in Path(temp_dir).iterdir() if p.name != "model.car"]
            self.assertEqual(leftovers, [])

    def test_failure_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "model.car"
            path.write_bytes(b"OLD")
            with self.assertRaises(RuntimeError):
                with atomic_output_file(str(path)) as f:
                    f.write(b"BAD")
                    raise RuntimeError("injected disk failure")
            self.assertEqual(path.read_bytes(), b"OLD")
            leftovers = [p for p in Path(temp_dir).iterdir() if p.name != "model.car"]
            self.assertEqual(leftovers, [])


class FastPathModifierTests(unittest.TestCase):
    def _shape_key_object(self, name):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [],
            [(0, 1, 2, 3)],
        )
        obj = bpy.data.objects.new(name + "Obj", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.shape_key_add(name="Basis")
        return obj, mesh

    def _remove(self, obj, mesh):
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)

    def test_no_modifiers_uses_fast_path(self):
        obj, mesh = self._shape_key_object("FastPathClean")
        try:
            self.assertTrue(can_use_shape_key_fast_path(obj))
        finally:
            self._remove(obj, mesh)

    def test_position_changing_modifiers_block_fast_path(self):
        for modifier_type in ("MIRROR", "SUBSURF", "DECIMATE", "BEVEL", "LATTICE"):
            obj, mesh = self._shape_key_object(f"FastPath{modifier_type}")
            try:
                obj.modifiers.new(f"Mod_{modifier_type}", modifier_type)
                self.assertFalse(can_use_shape_key_fast_path(obj), modifier_type)
            finally:
                self._remove(obj, mesh)

    def test_hidden_modifiers_do_not_block_fast_path(self):
        obj, mesh = self._shape_key_object("FastPathHiddenMirror")
        try:
            modifier = obj.modifiers.new("Mirror", "MIRROR")
            modifier.show_viewport = False
            self.assertTrue(can_use_shape_key_fast_path(obj))
        finally:
            self._remove(obj, mesh)

    def test_position_preserving_modifiers_keep_fast_path(self):
        obj, mesh = self._shape_key_object("FastPathUvOnly")
        try:
            obj.modifiers.new("UVWarp", "UV_WARP")
            obj.modifiers.new("Weighted", "WEIGHTED_NORMAL")
            self.assertTrue(can_use_shape_key_fast_path(obj))
        finally:
            self._remove(obj, mesh)

    def test_object_without_shape_keys_is_not_a_fast_path_candidate(self):
        mesh = bpy.data.meshes.new("FastPathNoKeys")
        mesh.from_pydata([(0, 0, 0)], [], [])
        obj = bpy.data.objects.new("FastPathNoKeysObj", mesh)
        bpy.context.scene.collection.objects.link(obj)
        try:
            self.assertFalse(can_use_shape_key_fast_path(obj))
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)


class EmptyTextureImportTests(unittest.TestCase):
    def write_3df(self, path):
        header = np.zeros(1, dtype=HEADER_DTYPE)
        header["vertex_count"] = 3
        header["face_count"] = 1
        header["bone_count"] = 0
        header["texture_size"] = 0

        faces = np.zeros(1, dtype=FACE_DTYPE)
        faces["v"][0] = [0, 1, 2]
        vertices = np.zeros(3, dtype=VERTEX_DTYPE)
        vertices["owner"] = -1

        with open(path, "wb") as fixture:
            header.tofile(fixture)
            faces.tofile(fixture)
            vertices.tofile(fixture)

    def test_empty_texture_returns_none_with_warning(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            fixture_path = str(Path(temp_dir) / "untextured.3df")
            self.write_3df(fixture_path)
            result = parse_3df(
                fixture_path,
                validate=False,
                parse_texture=True,
                flip_handedness=False,
            )
            texture = result[6]
            warnings = result[8]
            self.assertIsNone(texture)
            self.assertTrue(
                any("texture_size = 0" in warning for warning in warnings),
                f"Expected an empty-texture warning, got: {warnings}",
            )


class CarAnimationFailureTests(unittest.TestCase):
    def _animated_mesh_object(self, name, absolute=True):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [],
            [(0, 1, 2, 3)],
        )
        obj = bpy.data.objects.new(name + "Obj", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.shape_key_add(name="Basis")
        for index in range(3):
            key = obj.shape_key_add(name=f"Walk.Frame_{index + 1:03d}")
            key.data[0].co.x = index / 16.0
        mesh.shape_keys.use_relative = not absolute
        return obj, mesh

    def test_bake_vertex_count_mismatch_raises_instead_of_silently_skipping(self):
        obj, mesh = self._animated_mesh_object("CarMismatch")
        action = None
        try:
            # An ARMATURE modifier disables the fast path, forcing the
            # evaluated-mesh bake where the vertex-count guard is active.
            obj.modifiers.new("Armature", 'ARMATURE')
            bpy.context.scene.render.fps = 30
            action = keyframe_shape_key_animation_as_action(
                obj, "Walk", kps=30, scene_fps=30, use_absolute=True, use_kps_timing=True
            )
            push_shape_key_action_to_nla(obj, strip_name="Walk")

            # A mismatching expected vertex count must raise (loud failure),
            # never silently skip the animation while reporting success.
            with self.assertRaisesRegex(ValueError, "expected 999"):
                gather_car_animations(obj, np.identity(4), 999)

            # The gather restores the scene state after the raise.
            self.assertTrue(
                all(track.mute is False for track in obj.data.shape_keys.animation_data.nla_tracks)
            )
        finally:
            bpy.context.scene.frame_set(1)
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            if action is not None and action.name in bpy.data.actions:
                bpy.data.actions.remove(action)

    def test_export_car_surfaces_diagnostics(self):
        obj, mesh = self._animated_mesh_object("CarDiagnostics")
        action = None
        filepath = None
        temp_dir = None
        try:
            bpy.context.scene.render.fps = 30
            action = keyframe_shape_key_animation_as_action(
                obj, "Walk", kps=30, scene_fps=30, use_absolute=True, use_kps_timing=True
            )
            push_shape_key_action_to_nla(obj, strip_name="Walk")

            temp_dir = tempfile.mkdtemp(prefix="carnivores_diag_")
            filepath = os.path.join(temp_dir, "diagnostics.car")
            diagnostics = []
            export_car(filepath, obj, np.identity(4), export_textures=False, diagnostics=diagnostics)
            self.assertTrue(os.path.exists(filepath))
            # A clean round trip produces no diagnostics.
            self.assertEqual(diagnostics, [])
        finally:
            bpy.context.scene.frame_set(1)
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            if action is not None and action.name in bpy.data.actions:
                bpy.data.actions.remove(action)
            if filepath and os.path.exists(filepath):
                os.remove(filepath)


class ResyncAbsoluteModeTests(unittest.TestCase):
    def test_resync_operator_preserves_absolute_shape_keys(self):
        obj = None
        mesh = None
        try:
            mesh = bpy.data.meshes.new("ResyncAbsoluteMesh")
            mesh.from_pydata(
                [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
                [],
                [(0, 1, 2, 3)],
            )
            obj = bpy.data.objects.new("ResyncAbsoluteObj", mesh)
            bpy.context.scene.collection.objects.link(obj)
            obj.shape_key_add(name="Basis")
            for index in range(3):
                key = obj.shape_key_add(name=f"Walk.Frame_{index + 1:03d}")
                key.data[0].co.x = index / 16.0
            mesh.shape_keys.use_relative = False
            bpy.context.scene.render.fps = 30

            action = keyframe_shape_key_animation_as_action(
                obj, "Walk", kps=30, scene_fps=30, use_absolute=True, use_kps_timing=True
            )
            push_shape_key_action_to_nla(obj, strip_name="Walk")

            sk_data = mesh.shape_keys
            sk_data.animation_data.action = action

            bpy.ops.object.select_all(action='DESELECT')
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj

            result = bpy.ops.carnivores.resync_animation(action_name=action.name)
            self.assertEqual(result, {'FINISHED'})

            # The rebuilt action must still animate through eval_time;
            # a relative re-bake would export the basis pose for every frame.
            fcurves = []
            import bpy_extras.anim_utils
            if hasattr(action, "slots") and bpy.app.version >= (5, 0, 0):
                for slot in action.slots:
                    bag = bpy_extras.anim_utils.action_get_channelbag_for_slot(action, slot)
                    if bag:
                        fcurves.extend(bag.fcurves)
            else:
                fcurves = list(action.fcurves)
            paths = {fc.data_path for fc in fcurves}
            self.assertIn("eval_time", paths)
            self.assertFalse(any(path.startswith("key_blocks") for path in paths))
        finally:
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.name in bpy.data.meshes:
                bpy.data.meshes.remove(mesh)
            for action in list(bpy.data.actions):
                if action.name.startswith("Walk_Action"):
                    bpy.data.actions.remove(action)


if __name__ == "__main__":
    unittest.main()
