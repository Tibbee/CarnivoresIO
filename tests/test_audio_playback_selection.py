from pathlib import Path
import sys
import unittest


PACKAGE_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PACKAGE_ROOT.parent))
from carnivores_io.operators import animation as animation_ops


class _Dummy:
    pass


class _TrackCollection(list):
    def get(self, name):
        return next((track for track in self if track.name == name), None)


class SelectedTrackAudioTests(unittest.TestCase):
    def setUp(self):
        self.original_get_anim_data = animation_ops.anim_utils.get_active_animation_data
        self.original_resolve_sound = animation_ops.anim_utils.resolve_action_sound
        self.original_preview_state = animation_ops._preview_restore_state
        animation_ops._preview_restore_state = None

    def tearDown(self):
        animation_ops.anim_utils.get_active_animation_data = self.original_get_anim_data
        animation_ops.anim_utils.resolve_action_sound = self.original_resolve_sound
        animation_ops._preview_restore_state = self.original_preview_state

    @staticmethod
    def make_source(*, selected_index=1, selected_muted=False):
        sound_a = _Dummy()
        sound_a.name = "SoundA"
        sound_b = _Dummy()
        sound_b.name = "SoundB"

        action_a = _Dummy()
        action_a.name = "ActionA"
        action_a.sound = sound_a
        action_b = _Dummy()
        action_b.name = "ActionB"
        action_b.sound = sound_b

        strip_a = _Dummy()
        strip_a.name = "StripA"
        strip_a.action = action_a
        strip_a.frame_start = 1.0
        strip_a.frame_end = 20.0
        strip_a.action_frame_start = 1.0
        strip_a.action_frame_end = 20.0
        strip_a.scale = 1.0
        strip_a.repeat = 1.0
        strip_a.use_reverse = False

        strip_b = _Dummy()
        strip_b.name = "StripB"
        strip_b.action = action_b
        strip_b.frame_start = 1.0
        strip_b.frame_end = 20.0
        strip_b.action_frame_start = 1.0
        strip_b.action_frame_end = 20.0
        strip_b.scale = 1.0
        strip_b.repeat = 1.0
        strip_b.use_reverse = False

        track_a = _Dummy()
        track_a.mute = False
        track_a.strips = [strip_a]
        track_b = _Dummy()
        track_b.mute = selected_muted
        track_b.strips = [strip_b]

        anim_data = _Dummy()
        anim_data.nla_tracks = [track_a, track_b]
        anim_data.action = None

        obj = _Dummy()
        obj.carnivores_active_nla_index = selected_index

        scene = _Dummy()
        scene.is_nla_tweakmode = False
        scene.frame_current = 5.0
        scene.render = _Dummy()
        scene.render.fps = 30
        scene.render.fps_base = 1.0

        return obj, scene, anim_data, action_b, sound_b

    def test_normal_playback_uses_selected_carnivores_track(self):
        obj, scene, anim_data, action, sound = self.make_source()
        animation_ops.anim_utils.get_active_animation_data = lambda candidate: anim_data
        animation_ops.anim_utils.resolve_action_sound = lambda candidate: candidate.sound

        manager = animation_ops.AudioManager()
        key, info = manager._resolve_active_source(obj, scene)

        self.assertIsNotNone(key)
        self.assertIs(info[1], action)
        self.assertIs(info[2], sound)
        self.assertEqual(key[2], "StripB")

    def test_muted_selected_track_stays_silent(self):
        obj, scene, anim_data, _action, _sound = self.make_source(selected_muted=True)
        animation_ops.anim_utils.get_active_animation_data = lambda candidate: anim_data
        animation_ops.anim_utils.resolve_action_sound = lambda candidate: candidate.sound

        key, info = animation_ops.AudioManager()._resolve_active_source(obj, scene)

        self.assertIsNone(key)
        self.assertIsNone(info)

    def test_preview_restores_shape_key_track_mutes(self):
        first = _Dummy()
        first.name = "First"
        first.mute = True
        second = _Dummy()
        second.name = "Second"
        second.mute = False
        anim_data = _Dummy()
        anim_data.nla_tracks = _TrackCollection([first, second])

        obj = _Dummy()
        obj.name = "ShapeKeyObject"
        obj.animation_data = None
        obj.carnivores_active_nla_index = 1

        scene = _Dummy()
        scene.frame_start = 10
        scene.frame_end = 20
        scene.frame_current = 15
        scene.frame_subframe = 0.0
        scene.use_preview_range = False
        scene.frame_preview_start = 10
        scene.frame_preview_end = 20
        scene.carnivores_nla_sound_enabled = True

        # Preview soloing changed both tracks away from their original states.
        first.mute = False
        second.mute = True
        animation_ops._preview_restore_state = {
            "obj": obj,
            "anim_data": anim_data,
            "action_name": "Action",
            "original_frame": 4,
            "original_subframe": 0.25,
            "original_start": 1,
            "original_end": 100,
            "original_use_preview_range": True,
            "original_preview_start": 3,
            "original_preview_end": 30,
            "original_active_index": 0,
            "original_sound_enabled": True,
            "track_mutes": {"First": True, "Second": False},
        }

        self.assertTrue(animation_ops._restore_preview_state(scene))
        self.assertTrue(first.mute)
        self.assertFalse(second.mute)
        self.assertEqual(obj.carnivores_active_nla_index, 0)


if __name__ == "__main__":
    unittest.main()
