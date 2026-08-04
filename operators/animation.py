import bpy
import bpy_extras.io_utils
import ast
import json
import aud
import math
import os
import time
import numpy as np
from ..utils import animation as anim_utils
from ..utils import io as io_utils
from ..utils import common
from ..utils.logger import info, debug, warn, error

# NLA preview UI restoration state (kept outside the audio manager per design)
_preview_restore_state = None


def _poll_message(cls, message):
    """Set a Blender operator poll explanation when available."""
    try:
        cls.poll_message_set(message)
    except (AttributeError, TypeError, RuntimeError):
        pass
    return False


class CARNIVORES_PG_rig_proposal_edge(bpy.types.PropertyGroup):
    compact_a: bpy.props.IntProperty()
    compact_b: bpy.props.IntProperty()
    raw_a: bpy.props.IntProperty()
    raw_b: bpy.props.IntProperty()
    reason: bpy.props.StringProperty()
    confidence: bpy.props.FloatProperty()
    cost: bpy.props.FloatProperty()
    accepted: bpy.props.BoolProperty()
    action: bpy.props.EnumProperty(
        items=[
            ('AUTO', "Auto", "Use the deterministic proposal decision."),
            ('FORCE', "Force", "Force this candidate into the proposal forest."),
            ('REJECT', "Reject", "Reject this candidate from the proposal forest."),
        ],
        default='AUTO',
    )


class CARNIVORES_UL_rig_proposal_edges(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        row.prop(item, "action", text="")
        row.label(text=f"c{item.compact_a}/r{item.raw_a} <-> c{item.compact_b}/r{item.raw_b}")
        row.label(text=f"{item.confidence:.2f}")
        row.label(text=item.reason or "candidate")


# ---------------------------------------------------------------------------
# AudioManager — owns all runtime audio state
# ---------------------------------------------------------------------------

class AudioManager:
    """Owns the aud.Device, active playback sources, retry state, and cleanup."""

    _RETRY_BACKOFF = (5.0, 10.0, 20.0, 40.0, 60.0)

    def __init__(self, device_factory=None, monotonic_clock=None):
        self._device = None
        self._playback_active = False
        self._legacy_sounds = {}       # one-time migration from old _playing_sounds
        self._sources = {}             # source_key -> SoundInfo dict
        self._strip_cycles = {}        # (obj, strip_name) -> (cycle, last_frame)
        self._retry = {}               # sound_name -> {attempts, next_retry, category, last_error}
        self._reset_cooldown = 0.0     # monotonic timestamp
        self._device_retry_after = 0.0 # backoff timestamp for device creation failures
        self._device_factory = device_factory or (lambda: aud.Device())
        self._clock = monotonic_clock or time.monotonic
        self._factory_cache = {}       # fallback source key -> aud.Sound factory

    # --- Device ---

    def get_device(self):
        if self._device is None:
            now = self._clock()
            if now < self._device_retry_after:
                return None

            debug("AUDIO: Creating new aud.Device()")
            try:
                self._device = self._device_factory()
                self._device_retry_after = 0.0
                self._retry.pop("__device__", None)
            except Exception as e:
                backoff = self._RETRY_BACKOFF[min(
                    self._retry.get("__device__", {}).get("attempts", 0),
                    len(self._RETRY_BACKOFF) - 1
                )]
                self._device_retry_after = now + backoff
                self._retry["__device__"] = {
                    "attempts": self._retry.get("__device__", {}).get("attempts", 0) + 1,
                    "next_retry": self._device_retry_after,
                    "category": "device",
                    "last_error": str(e),
                }
                error(f"AUDIO: Failed to create aud.Device(): {e} (retry in {backoff:.0f}s)")
        return self._device

    # --- State flags ---

    @property
    def is_playback_active(self):
        return self._playback_active

    def on_playback_start(self):
        self._playback_active = True
        debug("Playback STARTED. is_playback_active = True")

    def on_playback_stop(self):
        self._playback_active = False
        debug("Playback STOPPED. is_playback_active = False")
        self._migrate_legacy()
        self._clear_playback_state()

    def on_stop_all(self):
        """Stop every handle and clear runtime records (toggle-off, disable)."""
        self._migrate_legacy()
        self._clear_playback_state()
        debug("All playing sounds stopped and cleared.")

    def update_volumes(self):
        """Re-apply scene and action volumes to all active handles without restart."""
        try:
            scene = bpy.context.scene
        except Exception:
            return
        scene_vol = getattr(scene, 'carnivores_nla_sound_volume', 1.0)
        for snd_info in self._sources.values():
            if snd_info['state'] != 'playing':
                continue
            action = snd_info.get('action')
            action_vol = getattr(action, 'carnivores_sound_volume', 1.0)
            try:
                snd_info['handle'].volume = action_vol * scene_vol
            except Exception:
                pass

    # --- File load ---

    def on_file_load(self):
        debug("AUDIO: New file loaded — resetting audio system")
        self._migrate_legacy()
        self._clear_playback_state()
        self._playback_active = False
        self._retry.clear()
        self._reset_cooldown = 0.0
        self._device_retry_after = 0.0
        self._factory_cache.clear()

        if self._device is not None:
            try:
                debug("AUDIO: Stopping aud device...")
                self._device.stopAll()
            except Exception as e:
                warn(f"Error stopping aud device on new file load: {e}")
            self._device = None

    # --- Per-frame sync (called by the frame_change_post handler) ---

    def start_or_sync(self, scene):
        if not self._playback_active:
            return
        if not scene.carnivores_nla_sound_enabled:
            return

        self._migrate_legacy()

        self._mark_completed_handles()
        self._prune_deleted_objects(scene)

        # Resolve desired sources
        desired = {}  # source_key -> (obj, action, snd, strip, cycle, offset)
        if _preview_restore_state or scene.is_nla_tweakmode:
            candidate_objects = scene.objects
        else:
            # Ordinary playback follows only the active object's selected
            # Carnivores track, avoiding multiple overlapping track sounds.
            active_object = getattr(bpy.context.view_layer.objects, "active", None)
            is_scene_object = bool(active_object and scene.objects.get(active_object.name) == active_object)
            candidate_objects = (active_object,) if is_scene_object else ()
        for obj in candidate_objects:
            key, info = self._resolve_active_source(obj, scene)
            if info:
                desired[key] = info

        # Stop stale sources
        for src_key in list(self._sources.keys()):
            if src_key not in desired:
                snd_info = self._sources.pop(src_key)
                try:
                    snd_info['handle'].stop()
                except Exception as e:
                    warn(f"AUDIO: Error stopping stale sound: {e}")

        if not desired:
            return

        device = self.get_device()
        if not device:
            return

        # Start or restart needed sources
        for src_key, (obj, action, snd, strip, cycle, offset) in desired.items():
            if src_key in self._sources:
                snd_info = self._sources[src_key]
                if snd_info['state'] == 'completed':
                    if snd_info.get('sound') == snd:
                        continue  # completed, same sound — stay quiet
                    try:
                        snd_info['handle'].stop()
                    except Exception:
                        pass
                    del self._sources[src_key]
                else:
                    continue  # already playing for this source

            # Retry check
            if snd.name in self._retry:
                retry_info = self._retry[snd.name]
                if self._clock() < retry_info['next_retry']:
                    continue
                # Expired — will retry below

            # Blender already caches its native factory. Cache only fallback
            # factories, whose loading may create a temporary packed WAV.
            try:
                factory = snd.factory
            except Exception:
                factory = None

            if not factory:
                cache_key = _fallback_factory_cache_key(snd)
                factory = self._factory_cache.get(cache_key)
                if factory is None:
                    factory = _load_sound_factory(snd)
                    if factory:
                        # Drop stale fallback entries for this datablock when
                        # its path or packed source metadata changes.
                        sound_identity = cache_key[0]
                        stale_keys = [
                            key for key in self._factory_cache
                            if key[0] == sound_identity and key != cache_key
                        ]
                        for key in stale_keys:
                            del self._factory_cache[key]
                        self._factory_cache[cache_key] = factory
            if not factory:
                self._record_retry(snd.name, 'sound', f"Could not load audio factory for '{snd.name}'")
                continue

            try:
                debug(f"AUDIO: Triggering '{snd.name}' for {obj.name} (cycle {cycle}, offset {offset:.3f}s)")
                handle = device.play(factory)
                if offset > 0.0:
                    handle.position = offset
                # Apply volume
                action_vol = getattr(action, 'carnivores_sound_volume', 1.0)
                scene_vol = getattr(scene, 'carnivores_nla_sound_volume', 1.0)
                handle.volume = action_vol * scene_vol
                self._sources[src_key] = {
                    'sound': snd,
                    'handle': handle,
                    'factory': factory,
                    'action': action,
                    'state': 'playing',
                }
                # Clear retry on successful playback
                self._retry.pop(snd.name, None)
            except Exception as e:
                error(f"NLA Sound Error: Could not play '{snd.name}': {e}")
                self._record_retry(snd.name, 'device', str(e))
                self._handle_critical_recovery(e)

    # --- Reset (critical recovery) ---

    def reset(self):
        """Attempt graceful teardown of all handles and device, then clear state."""
        for snd_info in list(self._sources.values()):
            try:
                snd_info['handle'].stop()
            except Exception:
                pass
        self._sources.clear()
        self._strip_cycles.clear()

        if self._device is not None:
            try:
                self._device.stopAll()
            except Exception:
                pass
            self._device = None

        self._legacy_sounds.clear()
        self._retry.clear()
        self._reset_cooldown = 0.0
        self._device_retry_after = 0.0
        self._factory_cache.clear()

    def _handle_critical_recovery(self, exc):
        err_str = str(exc)
        if "Buffer" not in err_str and "OpenAL" not in err_str:
            return
        now = self._clock()
        if now <= self._reset_cooldown + 5.0:
            warn("AUDIO: Skipping device reset (cooldown active).")
            return
        error("AUDIO: Critical OpenAL Error detected. Resetting audio device to recover...")
        self.reset()
        self._reset_cooldown = now

    # --- Retry / backoff ---

    def _record_retry(self, sound_name, category, message):
        """Record a failure for a sound, applying bounded exponential backoff.

        category: 'sound' (missing datablock, bad file, factory failure)
                  or 'device' (OpenAL buffer errors, handle creation failure)
        """
        existing = self._retry.get(sound_name, {})
        attempts = existing.get('attempts', 0)
        backoff_idx = min(attempts, len(self._RETRY_BACKOFF) - 1)
        delay = self._RETRY_BACKOFF[backoff_idx]

        self._retry[sound_name] = {
            'attempts': attempts + 1,
            'next_retry': self._clock() + delay,
            'category': category,
            'last_error': message,
        }

        # Log only on first failure and when delay increases
        if attempts == 0:
            warn(f"AUDIO: [{category}] {message}. Next retry in {delay:.0f}s.")
        elif backoff_idx != min(attempts - 1, len(self._RETRY_BACKOFF) - 1):
            warn(f"AUDIO: [{category}] '{sound_name}' still failing (attempt {attempts + 1}). Next retry in {delay:.0f}s.")

    # --- Source resolution helpers ---

    def _resolve_active_source(self, obj, scene):
        """Priority: preview > tweak mode > selected Carnivores NLA track."""
        global _preview_restore_state

        # 1. Preview mode
        if _preview_restore_state and _preview_restore_state.get('obj') == obj:
            action_name = _preview_restore_state.get('action_name')
            if action_name:
                action = bpy.data.actions.get(action_name)
                snd = anim_utils.resolve_action_sound(action) if action else None
                if snd:
                    key = (obj, action.name if action else "preview", "preview", 0)
                    return key, (obj, action, snd, None, 0, 0.0)

        anim_data = anim_utils.get_active_animation_data(obj)
        if not anim_data or not anim_data.nla_tracks:
            return None, None

        # 2. Tweak mode — only focused strip produces audio
        if scene.is_nla_tweakmode:
            active_action = anim_data.action
            if active_action:
                # Iterate tracks in reverse: top-of-stack (last) = highest priority
                for track in reversed(anim_data.nla_tracks):
                    if track.mute:
                        continue
                    for strip in track.strips:
                        if strip.action != active_action:
                            continue
                        if strip.frame_start <= scene.frame_current < strip.frame_end:
                            snd = anim_utils.resolve_action_sound(active_action)
                            if snd:
                                cycle = self._compute_strip_cycle(obj, strip, scene.frame_current)
                                offset = _compute_audio_offset(strip, scene)
                                key = (obj, active_action.name, strip.name, cycle)
                                return key, (obj, active_action, snd, strip, cycle, offset)
            return None, None  # tweak mode — no audio outside the tweaked strip

        # 3. Ordinary timeline playback — use the track selected in the
        # Carnivores panel. This keeps playback deterministic when imported
        # tracks overlap while allowing audio without entering tweak mode.
        track_index = int(getattr(obj, "carnivores_active_nla_index", -1))
        if not (0 <= track_index < len(anim_data.nla_tracks)):
            return None, None
        track = anim_data.nla_tracks[track_index]
        if track.mute:
            return None, None
        for strip in track.strips:
            if not strip.action or not (strip.frame_start <= scene.frame_current < strip.frame_end):
                continue
            action = strip.action
            snd = anim_utils.resolve_action_sound(action)
            if not snd:
                return None, None
            cycle = self._compute_strip_cycle(obj, strip, scene.frame_current)
            offset = _compute_audio_offset(strip, scene)
            key = (obj, action.name, strip.name, cycle)
            return key, (obj, action, snd, strip, cycle, offset)
        return None, None

    def _compute_strip_cycle(self, obj, strip, current_frame):
        key = (obj, strip.name)
        prev = self._strip_cycles.get(key)
        prev_cycle, last_frame = prev if isinstance(prev, tuple) else (0, None)

        new_cycle = prev_cycle

        # Timeline loop detection (playhead jumps backward)
        if last_frame is not None and current_frame < last_frame:
            new_cycle += 1

        # Best-effort strip repeat handling. Audio remains at its authored
        # speed, but restarts when the focused animation enters a visual cycle.
        repeat = max(1.0, float(getattr(strip, 'repeat', 1.0)))
        action_length = strip.action_frame_end - strip.action_frame_start
        scale = abs(float(getattr(strip, 'scale', 1.0)))
        cycle_length = action_length * scale
        if last_frame is not None and current_frame >= last_frame and repeat > 1.0 and cycle_length > 0.0:
            max_repeat_idx = max(0, math.ceil(repeat) - 1)

            def repeat_index(frame):
                elapsed = max(0.0, frame - strip.frame_start)
                return min(int(elapsed // cycle_length), max_repeat_idx)

            if repeat_index(current_frame) != repeat_index(last_frame):
                new_cycle += 1

        self._strip_cycles[key] = (new_cycle, current_frame)
        return new_cycle

    def get_preview_info(self, obj, action_name):
        """Look up active source info for a preview key. Used by the loop handler."""
        key = (obj, action_name, "preview", 0)
        return self._sources.get(key)

    def remove_preview_source(self, obj, action_name):
        """Remove and stop a preview source entry."""
        key = (obj, action_name, "preview", 0)
        snd_info = self._sources.pop(key, None)
        if snd_info:
            try:
                snd_info['handle'].stop()
            except Exception:
                pass

    # --- Internal helpers ---

    def _prune_deleted_objects(self, scene):
        """Remove sources referencing objects no longer in the scene."""
        live_objs = set(scene.objects)
        stale = []
        for src_key in list(self._sources.keys()):
            try:
                if src_key[0] not in live_objs:
                    stale.append(src_key)
            except ReferenceError:
                stale.append(src_key)
        for src_key in stale:
            snd_info = self._sources.pop(src_key, None)
            if snd_info:
                try:
                    snd_info['handle'].stop()
                except Exception:
                    pass
        # Clean cycle records too
        for cycle_key in list(self._strip_cycles.keys()):
            try:
                if cycle_key[0] not in live_objs:
                    del self._strip_cycles[cycle_key]
            except ReferenceError:
                del self._strip_cycles[cycle_key]

    def _mark_completed_handles(self):
        for snd_info in list(self._sources.values()):
            if snd_info['state'] != 'playing':
                continue
            try:
                status = snd_info['handle'].status
                if status in (aud.STATUS_STOPPED, aud.STATUS_INVALID):
                    snd_info['state'] = 'completed'
                elif status == aud.STATUS_PLAYING:
                    if (hasattr(snd_info['handle'], 'position') and
                        hasattr(snd_info['handle'], 'length') and
                        snd_info['handle'].length > 0 and
                        snd_info['handle'].position >= snd_info['handle'].length):
                        snd_info['state'] = 'completed'
            except Exception:
                snd_info['state'] = 'completed'

    def _clear_playback_state(self):
        for snd_info in self._sources.values():
            try:
                snd_info['handle'].stop()
            except Exception:
                pass
        self._sources.clear()
        self._strip_cycles.clear()

    def _migrate_legacy(self):
        for (_, handle, _) in list(self._legacy_sounds.values()):
            try:
                handle.stop()
            except Exception:
                pass
        self._legacy_sounds.clear()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_audio_manager = AudioManager()


def update_audio_volumes():
    """Public callback entry point for applying RNA volume changes."""
    _audio_manager.update_volumes()


def set_nla_sound_enabled(enabled):
    """Apply the authoritative preview-audio toggle immediately."""
    if not enabled:
        _audio_manager.on_stop_all()


# ---------------------------------------------------------------------------
# Pure helpers (no mutable state)
# ---------------------------------------------------------------------------

def _compute_audio_offset(strip, scene):
    """Audio playback offset in seconds for the current frame within a strip."""
    fps = scene.render.fps
    if fps <= 0:
        return 0.0

    current = scene.frame_current
    action_offset_frames = (current - strip.frame_start) + strip.action_frame_start

    if strip.use_reverse:
        action_length = strip.action_frame_end - strip.action_frame_start
        action_offset_frames = (strip.action_frame_start + action_length) - action_offset_frames

    return max(0.0, action_offset_frames / fps)


def _fallback_factory_cache_key(sound_datablock):
    """Return a cache key tied to datablock identity and fallback source state."""
    try:
        identity = sound_datablock.as_pointer()
    except Exception:
        identity = id(sound_datablock)

    try:
        resolved_path = bpy.path.abspath(sound_datablock.filepath)
    except Exception:
        resolved_path = ""

    source_mtime_ns = 0
    if resolved_path:
        try:
            source_mtime_ns = os.stat(resolved_path).st_mtime_ns
        except OSError:
            pass

    packed_file = getattr(sound_datablock, 'packed_file', None)
    packed_size = 0
    if packed_file:
        try:
            packed_size = int(packed_file.size)
        except Exception:
            packed_size = -1

    return identity, resolved_path, source_mtime_ns, packed_size


def _load_sound_factory(sound_datablock):
    """Return an aud.Sound factory for a Blender Sound datablock, or None."""
    return anim_utils.sound_datablock_to_factory(sound_datablock)


def get_aud_device():
    """Legacy accessor — routes through AudioManager."""
    return _audio_manager.get_device()


def register_audio_handlers():
    """Idempotently register all audio-related Blender handlers."""
    h = bpy.app.handlers
    if carnivores_nla_sound_handler not in h.frame_change_post:
        h.frame_change_post.append(carnivores_nla_sound_handler)
    if playback_started_handler not in h.animation_playback_pre:
        h.animation_playback_pre.append(playback_started_handler)
    if playback_stopped_handler not in h.animation_playback_post:
        h.animation_playback_post.append(playback_stopped_handler)
    if clear_aud_device_on_new_file not in h.load_post:
        h.load_post.append(clear_aud_device_on_new_file)


def unregister_audio_handlers():
    """Remove all audio handlers and stop audio resources."""
    h = bpy.app.handlers
    if carnivores_nla_sound_handler in h.frame_change_post:
        h.frame_change_post.remove(carnivores_nla_sound_handler)
    if playback_started_handler in h.animation_playback_pre:
        h.animation_playback_pre.remove(playback_started_handler)
    if playback_stopped_handler in h.animation_playback_post:
        h.animation_playback_post.remove(playback_stopped_handler)
    if clear_aud_device_on_new_file in h.load_post:
        h.load_post.remove(clear_aud_device_on_new_file)

    _audio_manager.on_stop_all()
    _audio_manager.reset()

class CARNIVORES_OT_play_linked_sound(bpy.types.Operator):
    """Plays the sound linked to the active object's active animation by adding it to the sequencer"""
    bl_idname = "carnivores.play_linked_sound"
    bl_label = "Play Linked Sound"
    bl_description = "Add the active animation's linked sound as a VSE strip at the current frame; this does not start playback."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        if not obj or not obj.animation_data or not obj.animation_data.action:
            return _poll_message(cls, "The active object needs an active animation action.")
        return True

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No active object selected.")
            return {'CANCELLED'}

        if not obj.animation_data or not obj.animation_data.action:
            self.report({'ERROR'}, "Active object has no active animation action.")
            return {'CANCELLED'}

        action = obj.animation_data.action
        linked_sound = anim_utils.resolve_action_sound(action)
        if not linked_sound:
            self.report({'ERROR'}, f"Animation '{action.name}' has no linked sound.")
            return {'CANCELLED'}

        # Ensure sequence editor exists
        if not context.scene.sequence_editor:
            context.scene.sequence_editor_create()

        # Add sound strip to sequencer
        # We'll place it on channel 1 and start it at the current frame
        # The name of the strip will be the sound's name
        try:
            debug(f"Sound data block exists. Sound name: {linked_sound.name}")
            debug(f"Attempting to play new sound '{linked_sound.name}' for {obj.name}.")

            # Check if a strip with the same name already exists to avoid duplicates
            existing_strip = context.scene.sequence_editor.sequences.get(linked_sound.name)
            if existing_strip:
                self.report({'INFO'}, f"Sound '{linked_sound.name}' already in sequencer. Skipping addition.")
                return {'FINISHED'}

            # Create a new sound strip and link the existing sound data block
            sound_strip = context.scene.sequence_editor.sequences.new(
                name=linked_sound.name,
                type='SOUND',
                channel=1,
                frame_start=context.scene.frame_current
            )
            sound_strip.sound = linked_sound  # Link the actual sound datablock

            self.report({'INFO'}, f"Added sound '{linked_sound.name}' to sequencer at frame {context.scene.frame_current}.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to add sound to sequencer: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

@bpy.app.handlers.persistent
def playback_started_handler(scene):
    _audio_manager.on_playback_start()

@bpy.app.handlers.persistent
def playback_stopped_handler(scene):
    _audio_manager.on_playback_stop()
    if _preview_restore_state:
        _restore_preview_state(scene)

@bpy.app.handlers.persistent
def carnivores_nla_sound_handler(scene):
    _audio_manager.start_or_sync(scene)

class CARNIVORES_OT_import_sound_for_action(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Import a sound file and link it to the specified Action"""
    bl_idname = "carnivores.import_sound_for_action"
    bl_label = "Import Sound"
    bl_description = "Load a sound file and link it to the selected animation Action for preview and CAR export."
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: bpy.props.StringProperty(
        default="*.wav;*.mp3;*.ogg;*.flac",
        options={'HIDDEN'},
    )

    action_name: bpy.props.StringProperty(
        name="Action Name",
        description="Animation Action that receives the imported sound. Set automatically by the Animation panel.",
    )

    def execute(self, context):
        if not self.action_name:
            self.report({'ERROR'}, "No action specified.")
            return {'CANCELLED'}
        
        action = bpy.data.actions.get(self.action_name)
        if not action:
            self.report({'ERROR'}, f"Action '{self.action_name}' not found.")
            return {'CANCELLED'}

        filepath = self.filepath
        if not os.path.isfile(filepath):
            self.report({'ERROR'}, "File not found.")
            return {'CANCELLED'}

        try:
            sound = bpy.data.sounds.load(filepath)
            # sound.pack() # Disabled packing to ensure immediate playback reliability
            action.carnivores_sound_ptr = sound
            self.report({'INFO'}, f"Imported '{sound.name}' and linked to '{action.name}'")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load sound: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

class CARNIVORES_OT_clear_action_sound(bpy.types.Operator):
    """Clear the linked sound from an Action without deleting the Sound datablock."""
    bl_idname = "carnivores.clear_action_sound"
    bl_label = "Clear Sound Link"
    bl_description = "Remove the selected Action's sound link; the Sound datablock itself is preserved."
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty(
        name="Action Name",
        description="Animation Action whose sound link will be cleared.",
    )

    @classmethod
    def poll(cls, context):
        if not getattr(context, "active_object", None):
            return _poll_message(cls, "Select an animated object before clearing a sound link.")
        return True

    def execute(self, context):
        action = bpy.data.actions.get(self.action_name)
        if not action:
            self.report({'ERROR'}, f"Action '{self.action_name}' not found.")
            return {'CANCELLED'}
        action.carnivores_sound_ptr = None
        if "carnivores_sound" in action:
            del action["carnivores_sound"]
        self.report({'INFO'}, f"Cleared sound link from '{action.name}'.")
        return {'FINISHED'}


class CARNIVORES_OT_toggle_nla_sound_playback(bpy.types.Operator):
    bl_idname = "carnivores.toggle_nla_sound_playback"
    bl_label = "Toggle NLA Sound Playback"
    bl_description = "Enable or disable linked sound playback for previews and the selected Carnivores NLA track."
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        scene.carnivores_nla_sound_enabled = not scene.carnivores_nla_sound_enabled
        state = "enabled" if scene.carnivores_nla_sound_enabled else "disabled"
        self.report({'INFO'}, f"NLA preview audio {state}.")
        return {'FINISHED'}

def get_kps_mode(self):
    return 1 if "carnivores_kps" in self else 0

def set_kps_mode(self, value):
    if value == 1: # OVERRIDE
        if "carnivores_kps" not in self:
             self["carnivores_kps"] = int(bpy.context.scene.render.fps)
    else: # AUTO
        if "carnivores_kps" in self:
            del self["carnivores_kps"]

# Property registration moved to __init__ generally, but can stay here if imported
# We will register it in __init__ or ensure this file runs.

def _set_if_writable(target, name, value):
    try:
        if hasattr(target, name):
            setattr(target, name, value)
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass


def _reorder_nla_tracks(anim_data, source_index, target_index):
    """Rebuild NLA tracks in a requested order on Blender versions without move()."""
    tracks = list(anim_data.nla_tracks)
    if not (0 <= source_index < len(tracks) and 0 <= target_index < len(tracks)):
        return False
    if source_index == target_index:
        return True

    snapshots = []
    for track in tracks:
        strips = []
        for strip in track.strips:
            action = strip.action
            if action is None:
                warn(f"Cannot reorder NLA track '{track.name}' because it contains a strip without an Action.")
                return False
            strip_state = {
                "name": strip.name,
                "action": action,
            }
            for prop_name in (
                "frame_start", "frame_end", "action_frame_start", "action_frame_end",
                "scale", "repeat", "blend_type", "extrapolation", "use_reverse",
                "use_sync_length", "strip_time", "use_animated_time", "influence",
                "mute", "select",
            ):
                try:
                    if hasattr(strip, prop_name):
                        strip_state[prop_name] = getattr(strip, prop_name)
                except (ReferenceError, RuntimeError):
                    pass
            strips.append(strip_state)
        snapshots.append({
            "name": track.name,
            "mute": track.mute,
            "select": getattr(track, "select", True),
            "lock": getattr(track, "lock", False),
            "strips": strips,
        })

    reordered = list(snapshots)
    moved = reordered.pop(source_index)
    reordered.insert(target_index, moved)

    def recreate(order):
        for existing in list(anim_data.nla_tracks):
            anim_data.nla_tracks.remove(existing)
        for snapshot in order:
            new_track = anim_data.nla_tracks.new()
            new_track.name = snapshot["name"]
            _set_if_writable(new_track, "mute", snapshot["mute"])
            _set_if_writable(new_track, "select", snapshot["select"])
            _set_if_writable(new_track, "lock", snapshot["lock"])
            for strip_state in snapshot["strips"]:
                new_strip = new_track.strips.new(
                    strip_state["name"],
                    int(round(strip_state.get("frame_start", 1.0))),
                    strip_state["action"],
                )
                for prop_name, value in strip_state.items():
                    if prop_name not in {"name", "action", "frame_start"}:
                        _set_if_writable(new_strip, prop_name, value)

    try:
        recreate(reordered)
    except (RuntimeError, ReferenceError, TypeError, ValueError) as exc:
        error(f"NLA track reorder failed: {exc}; restoring the original order.")
        try:
            recreate(snapshots)
        except (RuntimeError, ReferenceError, TypeError, ValueError) as restore_exc:
            error(f"NLA track reorder rollback failed: {restore_exc}")
        return False
    return True


class CARNIVORES_OT_move_nla_track(bpy.types.Operator):
    """Move a track earlier or later in the actual CAR export order."""
    bl_idname = "carnivores.move_nla_track"
    bl_label = "Move NLA Track"
    bl_description = "Move the selected NLA track earlier or later in CAR export order; all strips on the track move together."
    bl_options = {'REGISTER', 'UNDO'}

    direction: bpy.props.EnumProperty(
        name="Direction",
        description="Direction in the displayed export order (the top row exports first).",
        items=[
            ('UP', "Earlier", "Move this track earlier in CAR export order."),
            ('DOWN', "Later", "Move this track later in CAR export order."),
        ],
        default='UP',
    )

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        anim_data = anim_utils.get_active_animation_data(obj) if obj else None
        if not obj or not anim_data or len(anim_data.nla_tracks) < 2:
            return _poll_message(cls, "Select an object with at least two NLA tracks to reorder export order.")
        return True

    def execute(self, context):
        obj = context.active_object
        anim_data = anim_utils.get_active_animation_data(obj)
        index = int(getattr(obj, "carnivores_active_nla_index", 0))
        # NLA stores bottom-to-top; CAR exports reversed, so data index +1 is
        # earlier in the displayed/export order.
        target = index + 1 if self.direction == 'UP' else index - 1
        if target < 0 or target >= len(anim_data.nla_tracks):
            self.report({'INFO'}, "The selected track is already at that export-order boundary.")
            return {'CANCELLED'}
        track_name = anim_data.nla_tracks[index].name
        if not _reorder_nla_tracks(anim_data, index, target):
            self.report({'ERROR'}, "Could not reorder the selected NLA track; see the system console for details.")
            return {'CANCELLED'}
        obj.carnivores_active_nla_index = target
        position = len(anim_data.nla_tracks) - target
        self.report({'INFO'}, f"Moved '{track_name}' to export position {position}.")
        return {'FINISHED'}


class CARNIVORES_UL_animation_list(bpy.types.UIList):
    """UIList for displaying NLA tracks in the actual CAR export order."""
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        track = item
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            export_index = max(1, len(data.nla_tracks) - index)
            row.label(text=f"{export_index:02d}")
            icon = 'HIDE_OFF' if not track.mute else 'HIDE_ON'
            row.prop(track, "mute", text="", icon=icon, emboss=False)
            row.prop(track, "name", text="", emboss=False)
            if len(track.strips) > 1:
                row.label(text=f"{len(track.strips)} strips", icon='NLA')
            if track.strips:
                strip = track.strips[0]
                if strip.action:
                    global _preview_restore_state
                    is_previewing = bool(
                        _preview_restore_state
                        and _preview_restore_state.get('action_name') == strip.action.name
                    )
                    op = row.operator(
                        "carnivores.play_track_preview",
                        text="Stop" if is_previewing else "Preview",
                        icon='PAUSE' if is_previewing else 'PLAY',
                    )
                    op.action_name = strip.action.name
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon='NLA')

    def filter_items(self, context, data, propname):
        tracks = getattr(data, propname)
        if not tracks:
            return [], []
        
        # Default flags (all visible)
        flt_flags = [self.bitflag_filter_item] * len(tracks)
        
        # Reverse order: Visual index 0 -> Data index N-1
        flt_neworder = list(range(len(tracks) - 1, -1, -1))
        
        return flt_flags, flt_neworder

class CARNIVORES_OT_set_kps(bpy.types.Operator):
    """Set a custom Keys Per Second (KPS) override for this animation"""
    bl_idname = "carnivores.set_kps"
    bl_label = "Set KPS"
    bl_description = "Store a custom Keys Per Second value on the Action instead of using the scene frame rate."
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty(
        name="Action Name",
        description="Action receiving the KPS override.",
    )
    default_value: bpy.props.IntProperty(
        name="KPS",
        description="Keys per second to store on the Action.",
        default=30,
    )

    def execute(self, context):
        action = bpy.data.actions.get(self.action_name)
        if action:
            action["carnivores_kps"] = self.default_value
            return {'FINISHED'}
        return {'CANCELLED'}

class CARNIVORES_OT_reset_kps(bpy.types.Operator):
    """Remove the KPS override and use Scene FPS (Auto)"""
    bl_idname = "carnivores.reset_kps"
    bl_label = "Reset KPS to Auto"
    bl_description = "Remove the Action's custom KPS override so timing follows the scene frame rate."
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty(
        name="Action Name",
        description="Action whose KPS override will be removed.",
    )

    def execute(self, context):
        action = bpy.data.actions.get(self.action_name)
        if action and "carnivores_kps" in action:
            del action["carnivores_kps"]
            return {'FINISHED'}
        return {'CANCELLED'}

def preview_loop_handler(scene):
    """Loops playback within the preview range"""
    global _preview_restore_state
    if not _preview_restore_state:
        return

    start = _preview_restore_state['preview_start']
    end = _preview_restore_state['preview_end']
    last = _preview_restore_state.get('last_frame', start)
    current = scene.frame_current
    
    should_restart = False

    if current > end:
        scene.frame_set(int(start))
        current = int(start)
        should_restart = True
    elif current < last:
        should_restart = True
        
    _preview_restore_state['last_frame'] = current

    if should_restart:
        if not scene.carnivores_nla_sound_enabled:
            return
        obj = _preview_restore_state.get('obj')
        action_name = _preview_restore_state.get('action_name')
        if obj and action_name:
            snd_info = _audio_manager.get_preview_info(obj, action_name)
            if snd_info:
                try:
                    if snd_info['handle'].status == aud.STATUS_PLAYING:
                        snd_info['handle'].position = 0.0
                    else:
                        snd_info['handle'].position = 0.0
                        snd_info['handle'].resume()
                except Exception:
                    _audio_manager.remove_preview_source(obj, action_name)

@bpy.app.handlers.persistent
def clear_aud_device_on_new_file(scene):
    _audio_manager.on_file_load()

    # Clean up temp files from previous session's packed-sound playback
    anim_utils.cleanup_temp_sound_files()

    # Defensive re-registration
    register_audio_handlers()

    debug("AUDIO: Audio system reset complete.")

def _restore_preview_state(scene, context=None):
    """Restore preview mutations from both operator and playback-stop paths."""
    global _preview_restore_state
    state = _preview_restore_state
    if not state:
        return False
    # Clear first so animation_cancel callbacks cannot recursively restore it.
    _preview_restore_state = None

    obj = state.get('obj')
    is_obj_valid = False
    try:
        is_obj_valid = bool(obj and obj.name)
    except ReferenceError:
        pass
    # Restore the exact animation-data owner used by the preview. CAR imports
    # normally store NLA tracks on mesh shape keys, not on obj.animation_data.
    anim_data = state.get('anim_data')
    try:
        anim_data_valid = bool(anim_data and anim_data.nla_tracks is not None)
    except (ReferenceError, RuntimeError):
        anim_data_valid = False
    if not anim_data_valid and is_obj_valid:
        anim_data = anim_utils.get_active_animation_data(obj)
        anim_data_valid = anim_data is not None

    if anim_data_valid:
        for track_name, mute_state in state['track_mutes'].items():
            track = anim_data.nla_tracks.get(track_name)
            if track:
                track.mute = mute_state
    if is_obj_valid and hasattr(obj, "carnivores_active_nla_index"):
        obj.carnivores_active_nla_index = state.get("original_active_index", 0)

    scene.frame_start = state['original_start']
    scene.frame_end = state['original_end']
    scene.frame_current = state['original_frame']
    if hasattr(scene, 'frame_subframe'):
        scene.frame_subframe = state.get('original_subframe', 0.0)
    if hasattr(scene, 'use_preview_range'):
        scene.use_preview_range = state.get('original_use_preview_range', scene.use_preview_range)
        scene.frame_preview_start = state.get('original_preview_start', scene.frame_preview_start)
        scene.frame_preview_end = state.get('original_preview_end', scene.frame_preview_end)
    scene.carnivores_nla_sound_enabled = state['original_sound_enabled']

    if preview_loop_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(preview_loop_handler)
    try:
        _audio_manager.remove_preview_source(obj, state.get('action_name'))
    except (ReferenceError, RuntimeError):
        pass

    screen = getattr(context, 'screen', None) if context else None
    if screen and screen.is_animation_playing:
        try:
            bpy.ops.screen.animation_cancel(restore_frame=False)
        except RuntimeError as exc:
            warn(f"Could not cancel Blender playback after preview: {exc}")
    return True


class CARNIVORES_OT_play_track_preview(bpy.types.Operator):
    """Solo this track and play it in a loop with sound. Stops when you pause playback."""
    bl_idname = "carnivores.play_track_preview"
    bl_label = "Play Preview"
    bl_description = "Solo the selected NLA track, play it in a loop with its linked sound, and restore the previous timeline state when stopped."
    bl_options = {'REGISTER'}

    action_name: bpy.props.StringProperty(
        name="Action Name",
        description="NLA Action to solo and preview. Set automatically by the animation list.",
    )

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        if not obj:
            return _poll_message(cls, "Select an object with animation data to preview a track.")
        if not anim_utils.get_active_animation_data(obj):
            return _poll_message(cls, "The active object has no animation data to preview.")
        return True

    # Keep the public stop method on the shared restoration path used by the
    # animation playback-stop handler.  This definition intentionally follows
    # the legacy inline implementation above for compatibility with old files.
    def stop_preview(self, context):
        if _restore_preview_state(context.scene, context):
            self.report({'INFO'}, "Preview stopped and scene/NLA state restored.")

    def execute(self, context):
        global _preview_restore_state
        
        # Check if we are already previewing
        if _preview_restore_state:
            if _preview_restore_state.get('action_name') == self.action_name:
                # Toggle OFF (Stop)
                self.stop_preview(context)
                return {'FINISHED'}
            else:
                # Switch Preview (Stop current, Start new)
                self.stop_preview(context)
        
        obj = context.active_object
        if not obj:
            return {'CANCELLED'}
            
        # Find the track associated with this action
        target_track = None
        target_strip = None
        
        anim_data = anim_utils.get_active_animation_data(obj)
            
        if not anim_data:
            self.report({'ERROR'}, "No animation data.")
            return {'CANCELLED'}
            
        action = bpy.data.actions.get(self.action_name)
        if not action:
            return {'CANCELLED'}

        for track in anim_data.nla_tracks:
            for strip in track.strips:
                if strip.action == action:
                    target_track = track
                    target_strip = strip
                    break
            if target_track:
                break
        
        if not target_track:
            self.report({'ERROR'}, "Could not find NLA track for this action.")
            return {'CANCELLED'}

        start_frame = target_strip.frame_start
        end_frame = target_strip.frame_end
        
        kps = action.get("carnivores_kps", context.scene.render.fps)
        # Store State
        _preview_restore_state = {
            'obj': obj,
            'anim_data': anim_data,
            'action_name': self.action_name,
            'original_frame': context.scene.frame_current,
            'original_subframe': getattr(context.scene, 'frame_subframe', 0.0),
            'original_start': context.scene.frame_start,
            'original_end': context.scene.frame_end,
            'original_use_preview_range': getattr(context.scene, 'use_preview_range', False),
            'original_preview_start': getattr(context.scene, 'frame_preview_start', context.scene.frame_start),
            'original_preview_end': getattr(context.scene, 'frame_preview_end', context.scene.frame_end),
            'original_active_index': getattr(obj, 'carnivores_active_nla_index', 0),
            'original_sound_enabled': context.scene.carnivores_nla_sound_enabled,
            'track_mutes': {t.name: t.mute for t in anim_data.nla_tracks},
            'preview_start': start_frame,
            'preview_end': int(math.ceil(end_frame)),
            'last_frame': int(start_frame),
        }

        try:
            obj.carnivores_active_nla_index = list(anim_data.nla_tracks).index(target_track)
            for track in anim_data.nla_tracks:
                track.mute = (track != target_track)

            context.scene.frame_start = int(start_frame)
            context.scene.frame_end = int(math.ceil(end_frame))
            context.scene.frame_current = int(start_frame)

            if preview_loop_handler not in bpy.app.handlers.frame_change_post:
                bpy.app.handlers.frame_change_post.insert(0, preview_loop_handler)

            context.scene.carnivores_nla_sound_enabled = True
            if not getattr(context.screen, "is_animation_playing", False):
                bpy.ops.screen.animation_play()
        except (RuntimeError, ReferenceError, TypeError) as exc:
            self.stop_preview(context)
            self.report({'ERROR'}, f"Could not start preview: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Previewing '{action.name}' ({kps} KPS). Use Stop Preview to restore the scene.")
        return {'FINISHED'}

class CARNIVORES_OT_resync_animation(bpy.types.Operator):
    """Re-calculate keyframes for this animation based on current KPS and Scene FPS"""
    bl_idname = "carnivores.resync_animation"
    bl_label = "Re-Sync Timing"
    bl_description = "Rebuild or rescale this Action's keyframes using its KPS and the current scene frame rate, then update NLA strips."
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty(
        name="Action Name",
        description="Action whose keyframes and NLA timing will be rebuilt.",
    )

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        if not obj:
            return _poll_message(cls, "Select an animated object before re-syncing timing.")
        if not anim_utils.get_active_animation_data(obj):
            return _poll_message(cls, "The active object has no animation data to re-sync.")
        return True

    def get_anim_data(self, obj):
        # Helper to find where this action is used (ShapeKey or Object)
        datas = []
        if obj.type == 'MESH' and obj.data and obj.data.shape_keys and obj.data.shape_keys.animation_data:
            datas.append(obj.data.shape_keys.animation_data)
        if obj.animation_data:
            datas.append(obj.animation_data)
        return datas

    def execute(self, context):
        obj = context.active_object
        action = bpy.data.actions.get(self.action_name)
        if not action:
            self.report({'ERROR'}, f"Action '{self.action_name}' not found.")
            return {'CANCELLED'}
        
        # Get KPS
        if "carnivores_kps" in action:
            kps = action["carnivores_kps"]
        else:
            kps = context.scene.render.fps
            
        # Determine Type: Shape Key vs Standard
        is_shape_key = False
        
        # Compatibility: Get all fcurves regardless of Blender version
        all_fcurves = list(anim_utils.iter_action_fcurves(action))
        
        if all_fcurves:
            # Check first curve path
            path = all_fcurves[0].data_path
            if path.startswith("key_blocks"):
                is_shape_key = True
        
        # Fallback for empty actions: Check if matching shape keys exist
        if not all_fcurves and obj.type == 'MESH' and obj.data.shape_keys:
            if action.name.endswith("_Action"):
                base = action.name[:-7]
            else:
                base = action.name
            # Simple check without complex regex for fallback
            # utils.keyframe... uses regex, here we just guess
            pattern = f"{base}.Frame_"
            if any(pattern in kb.name for kb in obj.data.shape_keys.key_blocks):
                is_shape_key = True

        if is_shape_key:
            # SHAPE KEY LOGIC
            if action.name.endswith("_Action"):
                anim_base_name = action.name[:-7]
            else:
                anim_base_name = action.name
            
            anim_utils.keyframe_shape_key_animation_as_action(
                obj, 
                anim_base_name, 
                frame_start=1, 
                kps=kps, 
                scene_fps=context.scene.render.fps
            )
        else:
            # STANDARD LOGIC
            anim_utils.rescale_standard_action(action, kps, context.scene.render.fps)
        
        # Update NLA Strips
        strip_updated = self.update_nla_strip(obj, action)
        
        # Clear Active Action if it matches (to prevent double-transform)
        for ad in self.get_anim_data(obj):
            if strip_updated and ad.action == action:
                try:
                    ad.action = None
                except AttributeError:
                    self.report({'WARNING'}, f"Could not clear active action for '{action.name}' (likely NLA controlled).")
            
        self.report({'INFO'}, f"Resynced '{action.name}' at {kps} KPS.")
        return {'FINISHED'}
        
    def update_nla_strip(self, obj, action):
        updated = False
        datas = self.get_anim_data(obj)
        for anim_data in datas:
            if anim_data.nla_tracks:
                for track in anim_data.nla_tracks:
                    for strip in track.strips:
                        if strip.action == action:
                            start, end = anim_utils.get_action_frame_range(action)
                            # Update Strip
                            strip.action_frame_start = start
                            strip.action_frame_end = end
                            strip.frame_end = strip.frame_start + (end - start)
                            strip.use_sync_length = True
                            updated = True
        return updated

def _proposal_edge_decisions(obj):
    forced = []
    rejected = []
    for item in getattr(obj, "carnivores_rig_proposal_edges", ()):
        pair = (int(item.compact_a), int(item.compact_b))
        if item.action == 'FORCE':
            forced.append(pair)
        elif item.action == 'REJECT':
            rejected.append(pair)
    return tuple(sorted(forced)), tuple(sorted(rejected))


def _populate_proposal_edges(obj, proposal):
    items = obj.carnivores_rig_proposal_edges
    items.clear()
    raw_by_compact = {
        int(group.compact_id): int(group.raw_owner_id) for group in proposal.groups
    }
    forced = {
        tuple(edge) for edge in proposal.settings.get("forced_edges", [])
    }
    rejected = {
        tuple(edge) for edge in proposal.settings.get("rejected_edges", [])
    }
    for edge in proposal.edge_candidates:
        item = items.add()
        item.compact_a = int(edge.group_a)
        item.compact_b = int(edge.group_b)
        item.raw_a = raw_by_compact.get(int(edge.group_a), -1)
        item.raw_b = raw_by_compact.get(int(edge.group_b), -1)
        item.reason = "+".join(edge.reason_codes)
        item.confidence = float(edge.confidence)
        item.cost = float(edge.total_cost)
        item.accepted = tuple(sorted((edge.group_a, edge.group_b))) in {
            tuple(sorted(pair)) for pair in proposal.accepted_edges
        }
        pair = tuple(sorted((edge.group_a, edge.group_b)))
        item.action = 'FORCE' if pair in forced else ('REJECT' if pair in rejected else 'AUTO')


class CARNIVORES_OT_analyze_rig_proposal(bpy.types.Operator):
    bl_idname = "carnivores.analyze_rig_proposal"
    bl_label = "Analyze Rig Proposal"
    bl_description = "Analyze topology reconstruction without creating or changing an armature"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        if not obj or obj.type != 'MESH':
            return _poll_message(cls, "Select a mesh with imported owner data or vertex groups.")
        if anim_utils._get_reconstruction_setting(
            obj, "carnivores_reconstruct_algorithm", "LEGACY"
        ) != 'TOPOLOGY':
            return _poll_message(cls, "Set Reconstruction Algorithm to Topology first.")
        return bool(obj.vertex_groups) or bool(obj.data.attributes.get('carnivores_owner_index'))

    def execute(self, context):
        obj = context.active_object
        forced, rejected = _proposal_edge_decisions(obj)
        try:
            proposal, checksum = anim_utils.analyze_topology_proposal(
                obj,
                root_override_idx=int(anim_utils._get_reconstruction_setting(
                    obj, "carnivores_reconstruct_root_override", -1
                )),
                forced_edges=forced,
                rejected_edges=rejected,
            )
            anim_utils.store_topology_proposal(obj, proposal, checksum)
            _populate_proposal_edges(obj, proposal)
            anim_utils.create_topology_preview(obj, proposal)
            obj.data["carnivores_rig_proposal_status"] = "ANALYZED"
            self.report(
                {'WARNING'} if proposal.warnings else {'INFO'},
                f"Proposal analyzed: {len(proposal.groups) - len(proposal.skipped_groups)} active groups, "
                f"{len(proposal.accepted_edges)} accepted edges, checksum {checksum[:12]}.",
            )
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, f"Proposal analysis failed: {exc}")
            error(f"Rig proposal analysis failed for '{obj.name}': {exc}")
            return {'CANCELLED'}


class CARNIVORES_OT_apply_rig_proposal(bpy.types.Operator):
    bl_idname = "carnivores.apply_rig_proposal"
    bl_label = "Apply Rig Proposal"
    bl_description = "Apply the currently analyzed topology proposal after validating its source mesh"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return bool(
            obj and obj.type == 'MESH'
            and obj.data.get("carnivores_rig_proposal")
        )

    def execute(self, context):
        obj = context.active_object
        try:
            if anim_utils._get_reconstruction_setting(
                obj, "carnivores_reconstruct_algorithm", "LEGACY"
            ) != 'TOPOLOGY':
                raise ValueError("Set Reconstruction Algorithm to Topology before Apply.")
            # Apply must consume the proposal that Analyze stored. Re-running
            # analysis here would silently accept mesh edits and defeat the
            # stale-proposal guard.
            proposal, _checksum = anim_utils.load_topology_proposal(obj)
            if not anim_utils.topology_proposal_settings_match(obj, proposal):
                raise ValueError(
                    "Proposal settings changed after Analyze; analyze the mesh again."
                )
            forced, rejected = _proposal_edge_decisions(obj)
            stored_forced = {
                tuple(edge) for edge in proposal.settings.get("forced_edges", [])
            }
            stored_rejected = {
                tuple(edge) for edge in proposal.settings.get("rejected_edges", [])
            }
            if set(forced) != stored_forced or set(rejected) != stored_rejected:
                raise ValueError(
                    "Edge decisions changed after Analyze; analyze the proposal again."
                )
            armature = anim_utils.apply_topology_proposal(obj, proposal)
            if armature is None:
                self.report({'ERROR'}, "Proposal application produced no armature.")
                return {'CANCELLED'}
            anim_utils.clear_topology_preview(obj)
            obj.data["carnivores_rig_proposal_status"] = "APPLIED"
            self.report({'INFO'}, "Rig proposal applied and preview cleared.")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, f"Proposal application failed: {exc}")
            error(f"Rig proposal application failed for '{obj.name}': {exc}")
            return {'CANCELLED'}


class CARNIVORES_OT_clear_rig_preview(bpy.types.Operator):
    bl_idname = "carnivores.clear_rig_preview"
    bl_label = "Clear Rig Preview"
    bl_description = "Remove the generated rig proposal preview and stored proposal metadata"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return bool(obj and obj.type == 'MESH')

    def execute(self, context):
        obj = context.active_object
        anim_utils.clear_topology_preview(obj)
        anim_utils.clear_topology_proposal(obj)
        obj.carnivores_rig_proposal_edges.clear()
        self.report({'INFO'}, "Rig proposal preview and stored proposal cleared.")
        return {'FINISHED'}


class CARNIVORES_OT_validate_rig_round_trip(bpy.types.Operator):
    bl_idname = "carnivores.validate_rig_round_trip"
    bl_label = "Validate Rig Round Trip"
    bl_description = "Validate the analyzed proposal, generated armature structure, and owner reconciliation"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        return bool(obj and obj.type == 'MESH')

    def execute(self, context):
        obj = context.active_object
        try:
            result = anim_utils.validate_stored_topology_proposal(obj)
        except Exception as exc:
            self.report({'ERROR'}, f"Proposal validation failed: {exc}")
            return {'CANCELLED'}
        status = "VALID" if result['valid'] else "FAILED"
        if result['valid'] and not result.get('applied'):
            status = "VALID PROPOSAL / NOT APPLIED"
        lines = [
            f"RIG PROPOSAL VALIDATION: {obj.name}",
            "=" * 40,
            f"Status: {status}",
            f"Applied: {'YES' if result.get('applied') else 'NO'}",
            f"Checksum: {result['checksum']}",
        ]
        if result["errors"]:
            lines.append("Errors:")
            lines.extend(f"- {message}" for message in result["errors"])
        if result["warnings"]:
            lines.append("Warnings:")
            lines.extend(f"- {message}" for message in result["warnings"])
        reconciliation = result.get("reconciliation", {})
        if reconciliation:
            lines.append("Reconciliation:")
            for key, value in reconciliation.items():
                lines.append(f"- {key}: {value}")
        text_name = "Carnivores_Rig_Proposal_Report"
        text = bpy.data.texts.get(text_name) or bpy.data.texts.new(text_name)
        text.clear()
        text.write("\n".join(lines) + "\n")
        self.report(
            {'INFO' if result['valid'] else 'WARNING'},
            f"Rig proposal validation {'passed' if result['valid'] else 'found issues'}; report written to {text_name}.",
        )
        return {'FINISHED'}


class CARNIVORES_OT_reconstruct_armature(bpy.types.Operator):
    """Reconstruct a skeletal rig from vertex groups (bone owners). Useful for .car models."""
    bl_idname = "carnivores.reconstruct_armature"
    bl_label = "Reconstruct Rig from Owners"
    bl_description = "Build and assign a Blender armature from imported CAR owner groups using the selected reconstruction algorithm."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return _poll_message(cls, "Select a mesh with imported owner data or vertex groups.")
        if bool(obj.vertex_groups) or obj.data.attributes.get('carnivores_owner_index'):
            return True
        return _poll_message(cls, "The active mesh has no vertex groups or imported owner cache.")

    def execute(self, context):
        obj = context.active_object
        override_idx = getattr(obj, "carnivores_reconstruct_root_override", -1)
        try:
            armature = anim_utils.reconstruct_armature(obj, root_override_idx=override_idx)
            if armature is None:
                self.report({'ERROR'}, "Reconstruction produced no armature. See the system console for details.")
                return {'CANCELLED'}
            self.report({'INFO'}, "Armature reconstructed and assigned.")
            skipped = armature.get("carnivores_reconstruct_skipped", "")
            if skipped:
                details = []
                try:
                    raw_details = json.loads(
                        armature.get("carnivores_reconstruct_skipped_details", "[]")
                    )
                    details = [
                        f"{entry.get('reason', 'unknown')}:{int(entry.get('vertex_count', 0))} verts"
                        for entry in raw_details
                        if isinstance(entry, dict)
                    ]
                except (TypeError, ValueError, AttributeError):
                    pass
                suffix = f" ({', '.join(details)})" if details else ""
                self.report(
                    {'WARNING'},
                    f"Skipped {armature.get('carnivores_reconstruct_skipped_count', 0)} groups: {skipped}{suffix}",
                )
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, f"Reconstruction failed: {exc}")
            error(f"Rig reconstruction failed for '{obj.name}': {exc}")
            return {'CANCELLED'}

def _find_mesh_armature(obj):
    if obj.parent and obj.parent.type == 'ARMATURE':
        return obj.parent
    for modifier in obj.modifiers:
        if (
            modifier.type == 'ARMATURE'
            and modifier.object
            and modifier.object.type == 'ARMATURE'
        ):
            return modifier.object
    return None


class CARNIVORES_OT_debug_rig_info(bpy.types.Operator):
    """Write detailed skeletal diagnostics to a persistent text report."""
    bl_idname = "carnivores.debug_rig_info"
    bl_label = "Generate Rig Report"
    bl_description = "Write owner-cache, generated-bone, hierarchy, topology, and vertex-group diagnostics to Carnivores_Rig_Debug; use Open Report to inspect it."
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        if obj and obj.type == 'MESH':
            return True
        return _poll_message(cls, "Select a mesh to generate rig debug information.")

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object.")
            return {'CANCELLED'}

        lines = []
        lines.append(f"DEBUG REPORT: {obj.name}")
        lines.append("="*40)
        
        # Vertex Group Info
        lines.append("\nVERTEX GROUPS:")
        v_counts = {vg.index: 0 for vg in obj.vertex_groups}
        for v in obj.data.vertices:
            for g in v.groups:
                if g.group in v_counts:
                    v_counts[g.group] += 1
        
        for vg in obj.vertex_groups:
            lines.append(f"ID {vg.index:02d}: {vg.name:<20} | Verts: {v_counts[vg.index]}")

        lines.append("\nRECONSTRUCTION CACHE:")
        for attr_name in ("carnivores_owner_index", "carnivores_owner_source"):
            attr = obj.data.attributes.get(attr_name)
            if attr:
                lines.append(f"{attr_name}: present | Domain: {attr.domain} | Type: {attr.data_type} | Count: {len(attr.data)}")
            else:
                lines.append(f"{attr_name}: missing")
        lines.append(
            f"Algorithm: {getattr(obj, 'carnivores_reconstruct_algorithm', 'LEGACY')} | "
            f"Components: {getattr(obj, 'carnivores_reconstruct_component_policy', 'MULTI_ROOT')} | "
            f"Legacy Cluster Filter: {getattr(obj, 'carnivores_reconstruct_legacy_filter_clusters', False)}"
        )
        lines.append(
            f"Weight Smoothing: enabled={getattr(obj, 'carnivores_reconstruct_smooth_weights', False)} "
            f"iters={getattr(obj, 'carnivores_reconstruct_smooth_iterations', 3)} "
            f"factor={getattr(obj, 'carnivores_reconstruct_smooth_factor', 0.5):.3f} "
            f"joints_only={getattr(obj, 'carnivores_reconstruct_smooth_joints_only', True)}"
        )
        source_id = obj.get(anim_utils.RECONSTRUCTION_SOURCE_ID_PROPERTY, "")
        lines.append(f"Reconstruction Source ID: {source_id or 'none'}")
        owner_attr = obj.data.attributes.get("carnivores_owner_index")
        if owner_attr and owner_attr.domain == 'POINT' and len(owner_attr.data) == len(obj.data.vertices):
            owner_values = np.empty(len(obj.data.vertices), dtype=np.int32)
            owner_attr.data.foreach_get("value", owner_values)
            lines.append(
                f"Owner schema: compact POINT/INT | unowned vertices: {int(np.count_nonzero(owner_values < 0))}"
            )
        mapping = obj.data.get("carnivores_owner_mapping", "")
        if mapping:
            try:
                mapping_payload = json.loads(mapping)
                raw_by_compact = mapping_payload.get("raw_by_compact", [])
                lines.append(
                    f"Raw-to-compact mapping ({len(raw_by_compact)}): "
                    + ", ".join(f"{raw}->{compact}" for compact, raw in enumerate(raw_by_compact))
                )
            except (TypeError, ValueError, AttributeError):
                lines.append("Raw-to-compact mapping: (could not decode)")

        stored_proposal = None
        if obj.data.get("carnivores_rig_proposal"):
            try:
                stored_proposal, proposal_checksum = anim_utils.load_topology_proposal(obj)
                lines.append(
                    f"Proposal: stored | checksum={proposal_checksum} | "
                    f"payload_hash={obj.data.get(anim_utils.TOPOLOGY_PROPOSAL_PAYLOAD_HASH_PROPERTY, 'none')} | "
                    f"algorithm_version={stored_proposal.algorithm_version}"
                )
                lines.append(f"Proposal settings: {json.dumps(stored_proposal.settings, sort_keys=True)}")
                for warning in stored_proposal.warnings:
                    lines.append(f"Proposal warning: {warning}")
                for component in stored_proposal.settings.get("component_sizes", []):
                    lines.append(
                        f"  Component groups={component.get('group_count', 0)} "
                        f"vertices={component.get('vertex_count', 0)} "
                        f"raw={component.get('raw_owner_ids', [])}"
                    )
                for root_component in stored_proposal.settings.get("root_candidates", []):
                    lines.append(
                        f"Root candidates {root_component.get('compact_ids', [])}: "
                        + json.dumps(root_component.get('candidates', []), sort_keys=True)
                    )
                lines.append("Proposal edge decisions:")
                accepted_pairs = {
                    tuple(sorted(item)) for item in stored_proposal.accepted_edges
                }
                forced_pairs = {
                    tuple(item) for item in stored_proposal.settings.get("forced_edges", [])
                }
                rejected_pairs = {
                    tuple(item) for item in stored_proposal.settings.get("rejected_edges", [])
                }
                for edge in stored_proposal.edge_candidates:
                    pair = tuple(sorted((edge.group_a, edge.group_b)))
                    decision = "ACCEPTED" if pair in accepted_pairs else "REJECTED"
                    if pair in forced_pairs:
                        decision = "FORCED" if pair in accepted_pairs else "FORCED_IGNORED"
                    elif pair in rejected_pairs:
                        decision = "EXPLICIT_REJECT"
                    elif edge.confidence >= 0.35 and pair not in accepted_pairs:
                        decision = "HIGH_VALUE_REJECTED"
                    elif edge.confidence < 0.35 and pair not in accepted_pairs:
                        decision = "LOW_CONFIDENCE_REJECTED"
                    lines.append(
                        f"  compact={pair} | raw=({stored_proposal.groups[pair[0]].raw_owner_id},"
                        f" {stored_proposal.groups[pair[1]].raw_owner_id}) | "
                        f"decision={decision} | source={'+'.join(edge.reason_codes)} | "
                        f"joint=({', '.join(f'{float(value):.4f}' for value in edge.boundary_joint)}) | "
                        f"confidence={edge.confidence:.3f} | terms={edge.cost_terms}"
                    )
            except Exception as exc:
                lines.append(f"Proposal: invalid or stale ({exc})")
        else:
            lines.append("Proposal: none")

        # Armature Info
        arm = None
        if obj.parent and obj.parent.type == 'ARMATURE':
            arm = obj.parent
        else:
            # Check modifier
            for mod in obj.modifiers:
                if mod.type == 'ARMATURE' and mod.object:
                    arm = mod.object
                    break
        
        if arm:
            lines.append(f"\nARMATURE: {arm.name}")
            lines.append("-" * 20)
            lines.append(
                "World transform: " + "; ".join(
                    "(" + ", ".join(f"{float(value):.5f}" for value in row) + ")"
                    for row in arm.matrix_world
                )
            )
            lines.append(
                f"Generated source ID: {arm.get(anim_utils.RECONSTRUCTION_SOURCE_ID_PROPERTY, 'none')}"
            )
            for bone in arm.data.bones:
                p_name = bone.parent.name if bone.parent else "NONE"
                h = bone.head_local
                t = bone.tail_local
                lines.append(f"Bone: {bone.name:<20} | Parent: {p_name:<20}")
                lines.append(f"      Head: ({h.x:7.3f}, {h.y:7.3f}, {h.z:7.3f})")
                lines.append(f"      Tail: ({t.x:7.3f}, {t.y:7.3f}, {t.z:7.3f})")
                roll = io_utils.get_bone_roll(bone)
                lines.append(f"      Length: {(t-h).length:7.3f} | Roll: {roll:7.3f}")
        else:
            lines.append("\nNO ARMATURE FOUND.")

        # Reconstruction Metadata
        lines.append("\nRECONSTRUCTION METADATA:")
        if arm:
            root_name = arm.get("carnivores_reconstruct_root", "N/A")
            root_idx = arm.get("carnivores_reconstruct_root_idx", "N/A")
            skipped_str = arm.get("carnivores_reconstruct_skipped", "")
            skipped_details = arm.get("carnivores_reconstruct_skipped_details", "")
            cluster_count = arm.get("carnivores_reconstruct_cluster_count", 1)
            parent_map = arm.get("carnivores_reconstruct_parent_map", "")
            name_map = arm.get("carnivores_reconstruct_bone_name_map", "")
            algorithm = arm.get("carnivores_rig_algorithm", "LEGACY")
            confidence = arm.get("carnivores_reconstruct_confidence", None)
            edge_details = arm.get("carnivores_reconstruct_edge_details", "")
            component_sizes = arm.get("carnivores_reconstruct_component_sizes", "")
            proposal_settings = arm.get("carnivores_reconstruct_proposal_settings", "")
            mirror_pair_details = arm.get("carnivores_reconstruct_mirror_pair_details", "[]")
            algorithm_version = arm.get("carnivores_rig_algorithm_version", None)
            lines.append(
                f"Algorithm: {algorithm}"
                + (f" v{int(algorithm_version)}" if algorithm_version is not None else "")
            )
            if algorithm == "TOPOLOGY":
                lines.append(
                    f"Anatomy Classification: "
                    f"{int(arm.get('carnivores_reconstruct_central_group_count', 0))} central groups, "
                    f"{int(arm.get('carnivores_reconstruct_mirror_pair_count', 0))} mirror pairs"
                )
                lines.append(
                    f"Mirror Pairs: {arm.get('carnivores_reconstruct_mirror_pairs', '[]')}"
                )
                if mirror_pair_details:
                    lines.append(f"Mirror Pair Scores: {mirror_pair_details}")
            if confidence is not None:
                lines.append(f"Mean Edge Confidence: {float(confidence):.3f}")
            lines.append(f"Selected Root: {root_name} (orig idx: {root_idx})")
            lines.append(f"Clusters Detected: {cluster_count}")
            lines.append(
                f"Side Axis: {arm.get('carnivores_reconstruct_side_axis', 'X')} "
                f"(inverted={bool(arm.get('carnivores_reconstruct_side_inverted', False))})"
            )
            if arm.get("carnivores_reconstruct_proposal_checksum"):
                lines.append(
                    f"Proposal Checksum: {arm.get('carnivores_reconstruct_proposal_checksum')}"
                )
            if proposal_settings:
                lines.append(f"Applied Proposal Settings: {proposal_settings}")
            if component_sizes:
                try:
                    decoded_components = json.loads(component_sizes)
                    lines.append(f"Components ({len(decoded_components)}):")
                    for component in decoded_components:
                        lines.append(
                            f"  groups={component.get('group_count', 0)} | "
                            f"vertices={component.get('vertex_count', 0)} | "
                            f"raw={component.get('raw_owner_ids', [])}"
                        )
                except (TypeError, ValueError, AttributeError):
                    lines.append("Components: (could not decode)")
            if skipped_str:
                lines.append(f"Skipped Groups (raw IDs): {skipped_str}")
                if arm.get("carnivores_reconstruct_skipped_cleanup"):
                    lines.append(
                        f"Skipped-group cleanup: {arm.get('carnivores_reconstruct_skipped_cleanup')}"
                    )
                try:
                    decoded_skipped = json.loads(skipped_details) if skipped_details else []
                    for entry in decoded_skipped:
                        lines.append(
                            f"  [{entry.get('raw_owner_id', '?')}] "
                            f"compact={entry.get('compact_id', '?')} | "
                            f"{entry.get('reason', 'unknown')} | "
                            f"verts={entry.get('vertex_count', 0)} | "
                            f"group='{entry.get('blender_name', '')}'"
                        )
                except (TypeError, ValueError, AttributeError):
                    lines.append("  (could not decode skipped-group details)")
            else:
                lines.append("Skipped Groups: none")
            if name_map:
                try:
                    decoded_names = json.loads(name_map)
                    lines.append(f"Bone Name Map ({len(decoded_names)}):")
                    for entry in decoded_names:
                        lines.append(
                            f"  raw={entry.get('raw_owner_id', '?')} -> "
                            f"Blender='{entry.get('blender_name', '?')}' / "
                            f"Export='{entry.get('export_name', '?')}'"
                        )
                except (TypeError, ValueError, AttributeError):
                    lines.append("Bone Name Map: (could not decode)")
            if edge_details:
                try:
                    decoded_edges = json.loads(edge_details)
                    lines.append(f"Accepted Edges ({len(decoded_edges)}):")
                    for edge in decoded_edges:
                        owners = edge.get("owners", ["?", "?"])
                        reason = "+".join(edge.get("reason", []))
                        lines.append(
                            f"  [{owners[0]}] -- [{owners[1]}] | {reason} | "
                            f"joint={edge.get('joint_source', reason)} | "
                            f"decision={edge.get('decision', 'AUTO')} | "
                            f"boundary={edge.get('boundary_edges', 0)} | "
                            f"confidence={float(edge.get('confidence', 0.0)):.3f} | "
                            f"terms={edge.get('cost_terms', {})}"
                        )
                except (TypeError, ValueError):
                    lines.append("Accepted Edges: (could not decode)")
            # Decode and display the versioned JSON parent map.
            try:
                try:
                    pm = json.loads(parent_map) if parent_map else {}
                except (TypeError, ValueError):
                    # Read Legacy metadata written by pre-Phase-5 builds.
                    pm = ast.literal_eval(parent_map) if parent_map else {}
                if pm:
                    lines.append(f"Hierarchy ({len(pm)} nodes):")
                    for child, parent in sorted(pm.items(), key=lambda x: int(x[0])):
                        if parent == -1:
                            lines.append(f"  [{child}] ROOT")
                        else:
                            lines.append(f"  [{child}] -> parent {parent}")
                else:
                    lines.append("Hierarchy: empty")
            except Exception:
                lines.append("Hierarchy: (could not decode)")
        else:
            lines.append("No armature found for metadata.")

        # Divergence Check: Vertex Groups vs Imported Owners
        owner_attr = obj.data.attributes.get("carnivores_owner_index")
        if owner_attr and obj.vertex_groups:
            lines.append("\nDIVERGENCE CHECK:")
            n = len(obj.data.vertices)
            owner_vals = np.empty(n, dtype=np.int32)
            owner_attr.data.foreach_get("value", owner_vals)
            mismatch_count = 0
            for v_idx in range(n):
                v = obj.data.vertices[v_idx]
                if not v.groups:
                    continue
                dom_group = max(v.groups, key=lambda g: g.weight).group
                if dom_group != owner_vals[v_idx]:
                    mismatch_count += 1
            if mismatch_count > 0:
                pct = (mismatch_count / max(n, 1)) * 100.0
                if arm and bool(arm.get("carnivores_reconstruct_smoothing", False)):
                    lines.append(
                        f"INFO: Generated smoothed deform weights change the dominant group on "
                        f"{mismatch_count} vertices ({pct:.1f}%)."
                    )
                    lines.append("   Canonical imported owner attributes remain unchanged.")
                else:
                    lines.append(f"WARNING: {mismatch_count} vertices ({pct:.1f}%) diverge from imported owners!")
                    lines.append("   Tip: Use 'Reset to Imported Owners' to restore them.")
            else:
                if arm and bool(arm.get("carnivores_reconstruct_smoothing", False)):
                    lines.append("OK: Smoothed deform weights retain imported owners as dominant groups.")
                    lines.append("   Canonical imported owner attributes remain unchanged.")
                else:
                    lines.append("OK: Vertex groups match imported owner cache.")
        else:
            lines.append("\nDIVERGENCE CHECK: No owner cache or no VGs")

        if stored_proposal:
            try:
                validation = anim_utils.validate_stored_topology_proposal(obj)
                lines.append(
                    f"\nPROPOSAL RECONCILIATION: {'VALID' if validation['valid'] else 'FAILED'}"
                )
                for key, value in validation.get("reconciliation", {}).items():
                    lines.append(f"  {key}: {value}")
                for message in validation.get("errors", []):
                    lines.append(f"  ERROR: {message}")
                for message in validation.get("warnings", []):
                    lines.append(f"  WARNING: {message}")
            except Exception as exc:
                lines.append(f"\nPROPOSAL RECONCILIATION: unavailable ({exc})")

        # Write to Text Editor
        txt_name = "Carnivores_Rig_Debug"
        txt = bpy.data.texts.get(txt_name) or bpy.data.texts.new(txt_name)
        txt.clear()
        txt.write("\n".join(lines))
        
        # Switch area to Text Editor if possible, or just report
        self.report({'INFO'}, f"Debug info written to text datablock: {txt_name}")
        return {'FINISHED'}

class CARNIVORES_OT_cleanup_skipped_groups(bpy.types.Operator):
    """Explicitly remove vertex groups retained because reconstruction skipped them."""
    bl_idname = "carnivores.cleanup_skipped_groups"
    bl_label = "Remove Skipped Owner Groups"
    bl_description = "Explicitly remove vertex groups reported as skipped by the last rig reconstruction. This can discard their weights."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        armature = _find_mesh_armature(obj) if obj and obj.type == 'MESH' else None
        if not armature:
            return _poll_message(cls, "Select a reconstructed mesh with skipped owner groups.")
        raw_details = armature.get("carnivores_reconstruct_skipped_details", "")
        try:
            has_skipped = bool(json.loads(raw_details)) if raw_details else False
        except (TypeError, ValueError):
            has_skipped = False
        if not has_skipped:
            return _poll_message(cls, "The active mesh has no recorded skipped owner groups.")
        return True

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Select a mesh object.")
            return {'CANCELLED'}
        armature = _find_mesh_armature(obj)
        if not armature:
            self.report({'ERROR'}, "No armature is assigned to the active mesh.")
            return {'CANCELLED'}

        raw_details = armature.get("carnivores_reconstruct_skipped_details", "")
        try:
            details = json.loads(raw_details) if raw_details else []
        except (TypeError, ValueError):
            self.report({'ERROR'}, "Skipped-group metadata is not valid JSON.")
            return {'CANCELLED'}
        if not isinstance(details, list):
            self.report({'ERROR'}, "Skipped-group metadata has an invalid structure.")
            return {'CANCELLED'}

        bone_names = {bone.name for bone in armature.data.bones}
        removals = []
        remaining = []
        for entry in details:
            if not isinstance(entry, dict):
                remaining.append(entry)
                continue
            group_name = str(entry.get("blender_name", ""))
            group = obj.vertex_groups.get(group_name) if group_name else None
            # Never delete a group currently used by a bone. Missing/renamed
            # groups remain reported instead of being guessed by compact ID.
            if group is None or group_name in bone_names:
                remaining.append(entry)
                continue
            assignment_count = sum(
                1
                for vertex in obj.data.vertices
                if any(assignment.group == group.index for assignment in vertex.groups)
            )
            removals.append((group_name, assignment_count))

        for group_name, _assignment_count in removals:
            group = obj.vertex_groups.get(group_name)
            if group is not None:
                obj.vertex_groups.remove(group)

        remaining_raw_ids = [
            str(entry.get("raw_owner_id"))
            for entry in remaining
            if isinstance(entry, dict) and entry.get("raw_owner_id") is not None
        ]
        armature["carnivores_reconstruct_skipped"] = ",".join(remaining_raw_ids)
        armature["carnivores_reconstruct_skipped_count"] = len(remaining)
        armature["carnivores_reconstruct_skipped_details"] = json.dumps(
            remaining, separators=(",", ":"), sort_keys=True
        )
        armature["carnivores_reconstruct_skipped_cleanup"] = "EXPLICIT"

        if not removals:
            self.report({'WARNING'}, "No recorded skipped groups could be safely matched; nothing was removed.")
            return {'CANCELLED'}
        assignment_count = sum(count for _name, count in removals)
        suffix = f"; {len(remaining)} remain reported" if remaining else ""
        self.report(
            {'WARNING'} if assignment_count else {'INFO'},
            f"Removed {len(removals)} skipped owner group(s) ({assignment_count} vertex assignments){suffix}.",
        )
        return {'FINISHED'}


class CARNIVORES_OT_reset_to_imported_owners(bpy.types.Operator):
    """Recreate vertex groups from the cached carnivores_owner_index attribute."""
    bl_idname = "carnivores.reset_to_imported_owners"
    bl_label = "Reset to Imported Owners"
    bl_description = "Replace generated vertex groups with one-hot groups rebuilt from the imported CAR owner cache."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        if not obj or obj.type != 'MESH':
            return _poll_message(cls, "Select an imported mesh with an owner cache to reset its vertex groups.")
        if "carnivores_owner_index" not in (obj.data.attributes.keys() if obj.data else []):
            return _poll_message(cls, "The active mesh has no imported owner cache to restore.")
        return True

    def execute(self, context):
        import numpy as np
        from ..utils.animation import OWNER_ATTR_NAME, _build_reconstruction_bone_names, _get_reconstruction_owner_source
        from ..utils import io as io_utils

        obj = context.active_object
        mesh = obj.data

        owner_attr = mesh.attributes.get(OWNER_ATTR_NAME)
        if not owner_attr:
            self.report({'ERROR'}, "No imported owner cache found on this mesh.")
            return {'CANCELLED'}

        owner_indices = np.empty(len(mesh.vertices), dtype=np.int32)
        owner_attr.data.foreach_get("value", owner_indices)

        group_count = 0
        if owner_indices is not None:
            valid_owners = owner_indices[owner_indices >= 0]
            if valid_owners.size > 0:
                group_count = int(valid_owners.max()) + 1

        if group_count <= 0:
            self.report({'ERROR'}, "No valid owner data in cache.")
            return {'CANCELLED'}

        owner_source = _get_reconstruction_owner_source(obj)
        bone_names = _build_reconstruction_bone_names(obj, group_count, owner_source=owner_source)

        for name in list(obj.vertex_groups.keys()):
            vg = obj.vertex_groups.get(name)
            if vg:
                obj.vertex_groups.remove(vg)

        io_utils.create_vertex_groups_from_bones(obj, bone_names, owner_indices)

        self.report({'INFO'}, f"Reset {len(bone_names)} vertex groups from imported owners.")
        return {'FINISHED'}

def reconstruction_root_items(obj, context):
    """Return searchable root choices with generated names and vertex counts."""
    items = [('AUTO', "Automatic", "Choose the root from the reconstruction algorithm.", 0)]
    if not obj or obj.type != 'MESH' or not obj.data:
        return items

    from ..utils.animation import _build_reconstruction_bone_names, _get_reconstruction_group_count, _get_reconstruction_owner_source
    import numpy as np

    owner_indices = None
    owner_attr = obj.data.attributes.get('carnivores_owner_index')
    if owner_attr and owner_attr.domain == 'POINT' and owner_attr.data_type == 'INT' and len(owner_attr.data) == len(obj.data.vertices):
        owner_indices = np.empty(len(obj.data.vertices), dtype=np.int32)
        owner_attr.data.foreach_get('value', owner_indices)
    group_count = _get_reconstruction_group_count(obj, owner_indices)
    if group_count <= 0:
        return items

    owner_source = None
    try:
        owner_source = _get_reconstruction_owner_source(obj)
    except (RuntimeError, ReferenceError):
        pass
    names = _build_reconstruction_bone_names(obj, group_count, owner_source=owner_source)
    counts = [0] * group_count
    if owner_indices is not None:
        for value in owner_indices:
            if 0 <= int(value) < group_count:
                counts[int(value)] += 1
    else:
        for vertex in obj.data.vertices:
            for group in vertex.groups:
                if 0 <= group.group < group_count:
                    counts[group.group] += 1

    for index, name in enumerate(names):
        items.append((
            str(index),
            f"{index}: {name} ({counts[index]} verts)",
            f"Use owner {index}, generated name '{name}', with {counts[index]} owned vertices.",
            index + 1,
        ))
    return items


def get_reconstruction_root_choice(obj):
    """Blender EnumProperty custom getters return the numeric enum value."""
    override = int(getattr(obj, 'carnivores_reconstruct_root_override', -1))
    return 0 if override < 0 else override + 1


def set_reconstruction_root_choice(obj, value):
    try:
        numeric_value = int(value)
        obj.carnivores_reconstruct_root_override = -1 if numeric_value <= 0 else numeric_value - 1
    except (TypeError, ValueError):
        obj.carnivores_reconstruct_root_override = -1


class VIEW3D_PT_carnivores_rig(bpy.types.Panel):
    """Dedicated owner reconstruction and rig diagnostics panel."""
    bl_label = "Carnivores Rig"
    bl_idname = "VIEW3D_PT_carnivores_rig"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Carnivores'

    def draw(self, context):
        layout = self.layout
        obj = getattr(context, 'active_object', None)
        if not obj or obj.type != 'MESH':
            layout.label(text="Select a mesh with imported owner data.", icon='INFO')
            return

        cache = layout.box()
        cache.label(text="Imported Owner Data", icon='GROUP_VERTEX')
        owner_attr = obj.data.attributes.get('carnivores_owner_index')
        source_attr = obj.data.attributes.get('carnivores_owner_source')
        cache.label(text=f"Owner index cache: {'Present' if owner_attr else 'Missing'}")
        cache.label(text=f"Original owner cache: {'Present' if source_attr else 'Missing'}")
        cache.label(text=f"Vertex groups: {len(obj.vertex_groups)}")

        reconstruction = layout.box()
        reconstruction.label(text="Reconstruction", icon='ARMATURE_DATA')
        reconstruction.prop(obj, 'carnivores_reconstruct_algorithm', text='Algorithm')
        if obj.carnivores_reconstruct_algorithm == 'TOPOLOGY':
            experimental = reconstruction.box()
            experimental.label(text="Topology proposal workflow", icon='INFO')
            experimental.label(text="Analyze is non-destructive; Apply uses the stored proposal.")
            experimental.prop(obj, 'carnivores_reconstruct_component_policy', text='Components')
            experimental.prop(obj, 'carnivores_reconstruct_side_axis', text='Side Axis')
            experimental.prop(obj, 'carnivores_reconstruct_side_inverted', text='Invert Side')
            proposal_status = obj.data.get('carnivores_rig_proposal_status', 'NONE')
            checksum = obj.data.get('carnivores_rig_proposal_checksum', '')
            experimental.label(text=f"Proposal: {proposal_status}")
            if checksum:
                experimental.label(text=f"Checksum: {checksum[:12]}")
            if obj.carnivores_rig_proposal_edges:
                experimental.template_list(
                    'CARNIVORES_UL_rig_proposal_edges',
                    '',
                    obj,
                    'carnivores_rig_proposal_edges',
                    obj,
                    'carnivores_rig_proposal_edge_index',
                    rows=min(8, len(obj.carnivores_rig_proposal_edges)),
                )
            proposal_actions = experimental.row(align=True)
            proposal_actions.operator('carnivores.analyze_rig_proposal', text='Analyze', icon='VIEWZOOM')
            proposal_actions.operator('carnivores.apply_rig_proposal', text='Apply', icon='CHECKMARK')
            proposal_actions.operator('carnivores.clear_rig_preview', text='Clear', icon='X')
            proposal_actions = experimental.row(align=True)
            proposal_actions.operator('carnivores.validate_rig_round_trip', text='Validate', icon='CHECKMARK')
        else:
            reconstruction.prop(obj, 'carnivores_reconstruct_legacy_filter_clusters', text='Filter Detached Clusters')
        reconstruction.prop(obj, 'carnivores_reconstruct_rig_policy', text='Existing Rig')

        weights = reconstruction.box()
        weights.label(text="Generated Deform Weights", icon='MOD_SMOOTH')
        weights.prop(obj, 'carnivores_reconstruct_smooth_weights', text='Enable Smoothing')
        if obj.carnivores_reconstruct_smooth_weights:
            weights.prop(obj, 'carnivores_reconstruct_smooth_iterations', text='Iterations')
            weights.prop(obj, 'carnivores_reconstruct_smooth_factor', text='Factor')
            weights.prop(obj, 'carnivores_reconstruct_smooth_joints_only', text='Joints Only')
        reconstruction.prop(obj, 'carnivores_reconstruct_semantic_naming', text='Semantic L/R Names')

        root = reconstruction.box()
        root.label(text="Root Selection", icon='BONE_DATA')
        root.prop(obj, 'carnivores_reconstruct_root_choice', text='Root')
        root.label(text="Automatic is recommended; the legacy integer override remains available to scripts.", icon='INFO')

        actions = layout.column(align=True)
        actions.operator(CARNIVORES_OT_reconstruct_armature.bl_idname, text='Reconstruct Rig', icon='BONE_DATA')
        actions.operator(CARNIVORES_OT_cleanup_skipped_groups.bl_idname, text='Remove Skipped Groups', icon='X')
        actions.operator(CARNIVORES_OT_reset_to_imported_owners.bl_idname, text='Reset to Imported Owners', icon='FILE_REFRESH')
        row = actions.row(align=True)
        row.operator(CARNIVORES_OT_debug_rig_info.bl_idname, text='Generate Rig Report', icon='TEXT')
        report = row.operator('carnivores.open_report', text='Open Report', icon='FILE_FOLDER')
        report.text_name = 'Carnivores_Rig_Debug'


class VIEW3D_PT_carnivores_animation(bpy.types.Panel):
    bl_label = "Carnivores Animation"
    bl_idname = "VIEW3D_PT_carnivores_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Carnivores'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.active_object

        # --- Global Sound Settings ---
        if _preview_restore_state and _preview_restore_state.get('obj') == obj:
            preview_box = layout.box()
            preview_box.label(text=f"Previewing: {_preview_restore_state.get('action_name', 'selected track')}", icon='PLAY')
            stop = preview_box.operator("carnivores.play_track_preview", text="Stop Preview", icon='PAUSE')
            stop.action_name = _preview_restore_state.get('action_name', '')
        sound_box = layout.box()
        sound_box.label(text="Animation Audio", icon='PLAY_SOUND')
        sound_box.prop(
            scene,
            "carnivores_nla_sound_enabled",
            text="On" if scene.carnivores_nla_sound_enabled else "Off",
            toggle=True,
        )
        sound_box.prop(scene, "carnivores_nla_sound_volume", text="Volume")
        sound_box.label(text="Timeline playback uses the selected, unmuted Carnivores track.", icon='INFO')
        layout.separator()

        if not obj:
            layout.label(text="Select an object", icon='INFO')
            return

        # Source Selection
        row = layout.row()
        row.prop(obj, "carnivores_anim_source", text="Source")

        # Determine Animation Data Source
        anim_data = anim_utils.get_active_animation_data(obj)

        def draw_rigging_utilities():
            box = layout.box()
            box.label(text="Rig controls moved to the Carnivores Rig panel.", icon='ARMATURE_DATA')
            box.label(text="Animation and audio controls remain here.")

        if not anim_data:
            draw_rigging_utilities()
            layout.separator()
            
            source_mode = getattr(obj, "carnivores_anim_source", "AUTO")
            if source_mode == 'SHAPE_KEYS':
                 layout.label(text="No Shape Key Animation Data", icon='INFO')
            elif source_mode == 'OBJECT':
                 layout.label(text="No Object Animation Data", icon='INFO')
            else:
                 layout.label(text="No animation data found.", icon='INFO')
            return

        # --- NLA Track List ---
        order_box = layout.box()
        order_box.label(text="NLA Tracks (CAR Export Order)", icon='NLA')
        order_box.label(text="Top row exports first; tracks with multiple strips export each strip in order.")
        row = order_box.row()
        row.template_list(
            "CARNIVORES_UL_animation_list", "", 
            anim_data, "nla_tracks", 
            obj, "carnivores_active_nla_index", 
            rows=5
        )
        move_row = order_box.row(align=True)
        earlier = move_row.operator("carnivores.move_nla_track", text="Earlier", icon='TRIA_UP')
        earlier.direction = 'UP'
        later = move_row.operator("carnivores.move_nla_track", text="Later", icon='TRIA_DOWN')
        later.direction = 'DOWN'

        # --- Active Track Details ---
        idx = obj.carnivores_active_nla_index
        if 0 <= idx < len(anim_data.nla_tracks):
            active_track = anim_data.nla_tracks[idx]
            
            if active_track and active_track.strips:
                strip = active_track.strips[0]
                action = strip.action

                if action:
                    box = layout.box()
                    box.label(text="Selected Track Details", icon='NLA')
                    row = box.row(align=True)
                    row.prop(strip, "name", text="", icon='NLA_PUSHDOWN')
                    if len(active_track.strips) > 1:
                        box.label(text=f"{len(active_track.strips)} strips will export in this track's strip order.", icon='INFO')

                    # Sound assignment and source state.
                    row = box.row(align=True)
                    row.prop(action, "carnivores_sound_ptr", text="Sound")
                    op = row.operator("carnivores.import_sound_for_action", text="Import", icon='FILE_FOLDER')
                    op.action_name = action.name
                    clear = row.operator("carnivores.clear_action_sound", text="Clear", icon='X')
                    clear.action_name = action.name
                    sound = anim_utils.resolve_action_sound(action)
                    duration = 0.0
                    if sound:
                        if sound.packed_file:
                            source_state = "Packed"
                        elif sound.filepath:
                            source_state = "External"
                        else:
                            source_state = "Unavailable"
                        box.label(text=f"Sound source: {source_state} ({sound.name})", icon='SPEAKER')
                        # Imported CAR sounds store their known duration. Avoid
                        # factory.length here because panel redraws can trigger decoding.
                        try:
                            duration = float(sound.get("carnivores_duration_seconds", 0.0))
                        except (TypeError, ValueError):
                            duration = 0.0
                        if duration > 0.0:
                            box.label(text=f"Sound duration: {duration:.3f}s")
                    else:
                        box.label(text="No linked sound", icon='INFO')
                    box.prop(action, "carnivores_sound_volume", text="Sound Volume")

                    # KPS and timing summary.
                    kps = float(action.get("carnivores_kps", scene.render.fps) or 0.0)
                    action_start, action_end = anim_utils.get_action_frame_range(action)
                    frame_count = max(1, action_end - action_start + 1)
                    strip_span = max(0.0, float(strip.frame_end - strip.frame_start))
                    frame_step = (scene.render.fps / kps) if kps > 0 else 0.0
                    export_frame_count = max(1, int((strip_span / frame_step) + 0.5) + 1) if frame_step > 0 else 0
                    animation_duration = ((export_frame_count - 1) / kps) if kps > 0 else 0.0
                    row = box.row(align=True)
                    row.prop(action, "carnivores_kps_mode", text="KPS")
                    if "carnivores_kps" in action:
                        row.prop(action, '["carnivores_kps"]', text="Override")
                    else:
                        row.label(text=f"Scene FPS: {scene.render.fps}")
                    box.label(text=f"Action: {action.name} | Frames: {frame_count} ({action_start}–{action_end})")
                    box.label(text=f"CAR timing: {export_frame_count} samples, {animation_duration:.3f}s at {kps:g} KPS")
                    if sound and duration > 0.0:
                        difference = animation_duration - duration
                        box.label(text=f"Animation − sound: {difference:+.3f}s", icon='TIME')

                    row = box.row(align=True)
                    op = row.operator("carnivores.resync_animation", text="Re-Sync Timing", icon='FILE_REFRESH')
                    op.action_name = action.name
            elif active_track:
                layout.label(text="Empty Track (No Strips)", icon='INFO')
        
        layout.separator()
        draw_rigging_utilities()