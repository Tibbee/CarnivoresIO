import io
import os
import shutil
from pathlib import Path
import sys
import tempfile
import unittest
import wave

import bpy
import numpy as np


PACKAGE_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PACKAGE_ROOT.parent))
from carnivores_io.parsers.export_car import (
    _animation_sample_count,
    _convert_sound_to_22khz_mono,
    _extract_pcm16_mono_22050_wav,
    _linear_fcurve_samples,
    export_car,
    gather_car_animations,
)
from carnivores_io.parsers.parse_car import parse_car
from carnivores_io.utils.animation import (
    cleanup_temp_sound_files,
    import_car_sounds,
    keyframe_shape_key_animation_as_action,
    push_shape_key_action_to_nla,
)
from carnivores_io.utils import io as io_utils


class _Point:
    def __init__(self, x, y, interpolation="LINEAR"):
        self.co = np.array((x, y), dtype=np.float32)
        self.interpolation = interpolation


class _FCurve:
    def __init__(self, points, modifiers=()):
        self.keyframe_points = points
        self.modifiers = modifiers


class PerformanceOptimizationTests(unittest.TestCase):
    def test_image_pixels_are_refreshed_after_packing(self):
        events = []

        class Pixels:
            def foreach_set(self, values):
                events.append(("pixels", np.asarray(values).copy()))

        class Image:
            pixels = Pixels()

            def update(self):
                events.append(("update", None))

            def pack(self):
                events.append(("pack", None))

            def reload(self):
                events.append(("reload", None))

        class Images:
            @staticmethod
            def new(**_kwargs):
                return Image()

        class Data:
            images = Images()

        class FakeBpy:
            data = Data()

        original_bpy = io_utils.bpy
        io_utils.bpy = FakeBpy()
        try:
            texture = np.zeros((2, 256, 4), dtype=np.float32)
            image = io_utils.create_image_texture(texture, 2, "Test")
        finally:
            io_utils.bpy = original_bpy

        self.assertIsInstance(image, Image)
        self.assertEqual(
            [event[0] for event in events],
            ["pixels", "update", "pack", "reload"],
        )

    @staticmethod
    def wav_bytes(samples, *, channels=1, sample_width=2, sample_rate=22050):
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav_file:
            wav_file.setnchannels(channels)
            wav_file.setsampwidth(sample_width)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(samples)
        return stream.getvalue()

    def test_compatible_wav_payload_is_preserved_exactly(self):
        payload = np.array([-32768, -1, 0, 1, 32767], dtype="<i2").tobytes()
        wav_data = self.wav_bytes(payload)
        self.assertEqual(_extract_pcm16_mono_22050_wav(wav_data), payload)

    def test_packed_blender_sound_uses_exact_pcm_payload(self):
        payload = np.array([-32768, -123, 0, 456, 32767], dtype="<i2").tobytes()
        handle, filepath = tempfile.mkstemp(suffix=".wav")
        os.close(handle)
        sound = None
        try:
            with open(filepath, "wb") as output:
                output.write(self.wav_bytes(payload))
            sound = bpy.data.sounds.load(filepath)
            sound.pack()
            os.remove(filepath)
            converted, length = _convert_sound_to_22khz_mono(sound)
            self.assertEqual(converted, payload)
            self.assertEqual(length, len(payload))
        finally:
            if sound is not None:
                bpy.data.sounds.remove(sound)
            if os.path.exists(filepath):
                os.remove(filepath)

    def test_sound_import_skips_unreferenced_table_entries(self):
        sounds = [
            {"name": "Unused", "data": np.array([1, 2], dtype=np.int16)},
            {"name": "Used", "data": np.array([3, 4], dtype=np.int16)},
        ]
        imported = []
        try:
            imported = import_car_sounds(
                None, sounds, "Model", bpy.context, referenced_indices={1}
            )
            self.assertEqual(len(imported), 2)
            self.assertIsNone(imported[0])
            self.assertIsNotNone(imported[1])
            self.assertAlmostEqual(
                imported[1]["carnivores_duration_seconds"], 2 / 22050.0
            )
        finally:
            for sound in imported:
                if sound is not None:
                    bpy.data.sounds.remove(sound)
            cleanup_temp_sound_files()

    def test_incompatible_wav_requires_conversion(self):
        stereo_payload = np.zeros(8, dtype="<i2").tobytes()
        self.assertIsNone(
            _extract_pcm16_mono_22050_wav(
                self.wav_bytes(stereo_payload, channels=2)
            )
        )
        self.assertIsNone(
            _extract_pcm16_mono_22050_wav(
                self.wav_bytes(stereo_payload, sample_rate=44100)
            )
        )

    def test_fractional_animation_range_preserves_terminal_sample(self):
        frame_step = 60.0 / 34.0
        end = 1.0 + 13 * frame_step
        self.assertEqual(_animation_sample_count(1.0, end, frame_step), 14)
        self.assertEqual(_animation_sample_count(1.0, int(end), frame_step), 13)

    def test_linear_fcurve_uses_numpy_for_safe_curves(self):
        curve = _FCurve([_Point(1.0, 10.0), _Point(3.0, 30.0)])
        values = _linear_fcurve_samples(curve, [1.0, 2.0, 3.0])
        np.testing.assert_allclose(values, [10.0, 20.0, 30.0])

    def test_non_linear_fcurve_falls_back(self):
        curve = _FCurve([_Point(1.0, 10.0, "BEZIER"), _Point(3.0, 30.0)])
        self.assertIsNone(_linear_fcurve_samples(curve, [1.0, 2.0, 3.0]))

    def test_fractional_absolute_action_exports_every_sample(self):
        mesh = bpy.data.meshes.new("FractionalRangeMesh")
        mesh.from_pydata([(0.0, 0.0, 0.0)], [], [])
        obj = bpy.data.objects.new("FractionalRangeObject", mesh)
        bpy.context.scene.collection.objects.link(obj)
        action = None
        original_fps = bpy.context.scene.render.fps
        try:
            obj.shape_key_add(name="Basis")
            for index in range(14):
                key = obj.shape_key_add(name=f"Fractional.Frame_{index + 1:03d}")
                key.data[0].co.x = index / 16.0
            mesh.shape_keys.use_relative = False
            bpy.context.scene.render.fps = 60
            action = keyframe_shape_key_animation_as_action(
                obj,
                "Fractional",
                kps=34,
                scene_fps=60,
                use_absolute=True,
                use_kps_timing=True,
            )
            push_shape_key_action_to_nla(obj, strip_name="Fractional")

            animations = gather_car_animations(obj, np.identity(4), 1)
            self.assertEqual(len(animations), 1)
            self.assertEqual(len(animations[0]["frames"]), 14)
        finally:
            bpy.context.scene.render.fps = original_fps
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(mesh)
            if action is not None and action.name in bpy.data.actions:
                bpy.data.actions.remove(action)

    def test_car_sound_round_trip_preserves_count_mapping_and_payloads(self):
        payloads = [
            np.array([-32768, -1, 0, 1, 32767], dtype="<i2").tobytes(),
            np.array([-5, -4, -3, -2, -1, 0, 1, 2], dtype="<i2").tobytes(),
        ]
        sounds_data = [
            {"name": "Growl", "data": np.frombuffer(payloads[0], dtype="<i2")},
            {"name": "Roar", "data": np.frombuffer(payloads[1], dtype="<i2")},
        ]
        imported = []
        mesh = None
        obj = None
        action = None
        filepath = None
        temp_dir = None
        original_fps = bpy.context.scene.render.fps
        try:
            mesh = bpy.data.meshes.new("RoundTripMesh")
            mesh.from_pydata(
                [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
                [],
                [(0, 1, 2, 3)],
            )
            obj = bpy.data.objects.new("RoundTripObject", mesh)
            bpy.context.scene.collection.objects.link(obj)

            obj.shape_key_add(name="Basis")
            for index in range(3):
                key = obj.shape_key_add(name=f"Walk.Frame_{index + 1:03d}")
                key.data[0].co.x = index / 16.0
            mesh.shape_keys.use_relative = False
            bpy.context.scene.render.fps = 60
            action = keyframe_shape_key_animation_as_action(
                obj,
                "Walk",
                kps=30,
                scene_fps=60,
                use_absolute=True,
                use_kps_timing=True,
            )
            push_shape_key_action_to_nla(obj, strip_name="Walk")

            imported = import_car_sounds(
                None, sounds_data, "Model", bpy.context, referenced_indices={0, 1}
            )
            self.assertEqual(len(imported), 2)
            self.assertTrue(all(sound is not None for sound in imported))
            action.carnivores_sound_ptr = imported[0]

            temp_dir = tempfile.mkdtemp(prefix="carnivores_rt_")
            filepath = os.path.join(temp_dir, "roundtrip.car")
            export_car(filepath, obj, np.identity(4), export_textures=False)

            (header, _model_name, _faces, _uvs, _vertices, _bones,
             _owners, _texture, _texture_height, _warnings, animations,
             sounds, cross_ref) = parse_car(
                filepath, parse_texture=False, import_sounds=True
            )

            self.assertEqual(int(header["sfx_count"]), 1)
            self.assertEqual(len(sounds), 1)
            self.assertEqual(sounds[0]["name"], "Growl")
            self.assertEqual(sounds[0]["length_bytes"], len(payloads[0]))
            self.assertEqual(sounds[0]["data"].tobytes(), payloads[0])

            self.assertEqual(len(animations), 1)
            self.assertEqual(animations[0]["name"], "Walk")
            self.assertEqual(cross_ref[0], 0)
            self.assertEqual(list(cross_ref[1:]), [-1] * 63)
        finally:
            bpy.context.scene.render.fps = original_fps
            if action is not None and action.name in bpy.data.actions:
                bpy.data.actions.remove(action)
            if obj is not None:
                bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None:
                bpy.data.meshes.remove(mesh)
            for sound in imported:
                if sound is not None and sound.name in bpy.data.sounds:
                    bpy.data.sounds.remove(sound)
            cleanup_temp_sound_files()
            if temp_dir and os.path.isdir(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
