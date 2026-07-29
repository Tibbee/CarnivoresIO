import bpy
import bpy_extras.io_utils
import aud
import math
import os
import time
from ..utils import animation as anim_utils
from ..utils import io as io_utils
from ..utils import common
from ..utils.logger import info, debug, warn, error

# NLA preview UI restoration state (kept outside the audio manager per design)
_preview_restore_state = None


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

        device = self.get_device()
        if not device:
            return

        self._migrate_legacy()
        self._mark_completed_handles()
        self._prune_deleted_objects(scene)

        # Resolve desired sources
        desired = {}  # source_key -> (obj, action, snd, strip, cycle, offset)
        for obj in scene.objects:
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
        """Priority: preview > tweak. Only focused (tweak mode) strips produce audio."""
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
    bl_options = {'REGISTER', 'UNDO'}

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

@bpy.app.handlers.persistent
def carnivores_nla_sound_handler(scene):
    _audio_manager.start_or_sync(scene)

class CARNIVORES_OT_import_sound_for_action(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Import a sound file and link it to the specified Action"""
    bl_idname = "carnivores.import_sound_for_action"
    bl_label = "Import Sound"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: bpy.props.StringProperty(
        default="*.wav;*.mp3;*.ogg;*.flac",
        options={'HIDDEN'},
    )

    action_name: bpy.props.StringProperty(name="Action Name")

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

class CARNIVORES_OT_toggle_nla_sound_playback(bpy.types.Operator):
    bl_idname = "carnivores.toggle_nla_sound_playback"
    bl_label = "Toggle NLA Sound Playback"
    bl_description = "Toggles automatic sound playback based on active NLA strips"
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        scene.carnivores_nla_sound_enabled = not scene.carnivores_nla_sound_enabled

        if scene.carnivores_nla_sound_enabled:
            self.report({'INFO'}, "NLA Sound Playback Enabled.")
        else:
            _audio_manager.on_stop_all()
            self.report({'INFO'}, "NLA Sound Playback Disabled.")

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

class CARNIVORES_UL_animation_list(bpy.types.UIList):
    """UIList for displaying NLA tracks in the Carnivores Animation Panel"""
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        track = item
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            # Mute Toggle (Eye Icon logic reversed: Mute=True -> Eye Closed)
            icon = 'HIDE_OFF' if not track.mute else 'HIDE_ON'
            row.prop(track, "mute", text="", icon=icon, emboss=False)
            row.prop(track, "name", text="", emboss=False)
            
            # Play Preview Button
            if track.strips:
                strip = track.strips[0]
                if strip.action:
                    # Check if this action is currently being previewed
                    is_previewing = False
                    global _preview_restore_state
                    if _preview_restore_state and _preview_restore_state.get('action_name') == strip.action.name:
                        is_previewing = True
                    
                    icon = 'PAUSE' if is_previewing else 'PLAY'
                    op = row.operator("carnivores.play_track_preview", text="", icon=icon)
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
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty()
    default_value: bpy.props.IntProperty(default=30)

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
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty()

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

class CARNIVORES_OT_play_track_preview(bpy.types.Operator):
    """Solo this track and play it in a loop with sound. Stops when you pause playback."""
    bl_idname = "carnivores.play_track_preview"
    bl_label = "Play Preview"
    bl_options = {'REGISTER'}

    action_name: bpy.props.StringProperty()

    def stop_preview(self, context):
        global _preview_restore_state
        if not _preview_restore_state:
            return

        # Restore State
        obj = _preview_restore_state.get('obj')
        
        # Check if obj is still valid (Blender objects can be invalid if deleted)
        is_obj_valid = False
        try:
            if obj and obj.name: # Accessing name is a safe way to check struct validity
                is_obj_valid = True
        except ReferenceError:
            pass

        if is_obj_valid and obj.animation_data:
             for track_name, mute_state in _preview_restore_state['track_mutes'].items():
                 track = obj.animation_data.nla_tracks.get(track_name)
                 if track:
                     track.mute = mute_state
        
        context.scene.frame_start = _preview_restore_state['original_start']
        context.scene.frame_end = _preview_restore_state['original_end']
        context.scene.frame_current = _preview_restore_state['original_frame']
        
        context.scene.carnivores_nla_sound_enabled = _preview_restore_state['original_sound_enabled']
        
        # Remove Loop Handler
        if preview_loop_handler in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.remove(preview_loop_handler)
            
        # Stop Playback
        if context.screen.is_animation_playing:
            bpy.ops.screen.animation_cancel(restore_frame=False)
            
        _preview_restore_state = None
        self.report({'INFO'}, "Preview stopped.")

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
            'action_name': self.action_name,
            'original_frame': context.scene.frame_current,
            'original_start': context.scene.frame_start,
            'original_end': context.scene.frame_end,
            'track_mutes': {t.name: t.mute for t in anim_data.nla_tracks},
            'preview_start': start_frame,
            'preview_end': int(math.ceil(end_frame)),
            'last_frame': int(start_frame) # Initialize last_frame for the handler
        }
        
        # Apply Mutes (Solo)
        for track in anim_data.nla_tracks:
            track.mute = (track != target_track)
            
        # Set Range & Frame
        context.scene.frame_start = int(start_frame)
        context.scene.frame_end = int(math.ceil(end_frame))
        
        context.scene.frame_current = int(start_frame)
        
        # Add Loop Handler (insert at 0 to ensure it runs first)
        if preview_loop_handler not in bpy.app.handlers.frame_change_post:
            bpy.app.handlers.frame_change_post.insert(0, preview_loop_handler)
            
        # Ensure Audio is ON
        _preview_restore_state['original_sound_enabled'] = context.scene.carnivores_nla_sound_enabled
        context.scene.carnivores_nla_sound_enabled = True
        
        # Start Playback
        if not context.screen.is_animation_playing:
            bpy.ops.screen.animation_play()
            
        self.report({'INFO'}, f"Previewing '{action.name}' ({kps} KPS)")
        return {'FINISHED'}

class CARNIVORES_OT_resync_animation(bpy.types.Operator):
    """Re-calculate keyframes for this animation based on current KPS and Scene FPS"""
    bl_idname = "carnivores.resync_animation"
    bl_label = "Re-Sync Timing"
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty()

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

class CARNIVORES_OT_reconstruct_armature(bpy.types.Operator):
    """Reconstruct a skeletal rig from vertex groups (bone owners). Useful for .car models."""
    bl_idname = "carnivores.reconstruct_armature"
    bl_label = "Reconstruct Rig from Owners"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj and obj.type == 'MESH' and (
            obj.vertex_groups or obj.data.attributes.get('carnivores_owner_index')
        )

    def execute(self, context):
        obj = context.active_object
        override_idx = getattr(obj, "carnivores_reconstruct_root_override", -1)
        try:
            armature = anim_utils.reconstruct_armature(obj, root_override_idx=override_idx)
            if armature is None:
                self.report({'ERROR'}, "Reconstruction produced no armature. See the system console for details.")
                return {'CANCELLED'}
            self.report({'INFO'}, "Armature reconstructed and assigned.")
            return {'FINISHED'}
        except Exception as exc:
            self.report({'ERROR'}, f"Reconstruction failed: {exc}")
            error(f"Rig reconstruction failed for '{obj.name}': {exc}")
            return {'CANCELLED'}

class CARNIVORES_OT_debug_rig_info(bpy.types.Operator):
    """Log detailed skeletal information to a text datablock for debugging."""
    bl_idname = "carnivores.debug_rig_info"
    bl_label = "Log Rig Debug Info"
    bl_options = {'REGISTER'}

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
            f"Pre-Reconstruct Smoothing: enabled={getattr(obj, 'carnivores_reconstruct_smooth_weights', False)} "
            f"iters={getattr(obj, 'carnivores_reconstruct_smooth_iterations', 3)} "
            f"factor={getattr(obj, 'carnivores_reconstruct_smooth_factor', 0.5):.3f} "
            f"joints_only={getattr(obj, 'carnivores_reconstruct_smooth_joints_only', True)}"
        )

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
            for bone in arm.data.bones:
                p_name = bone.parent.name if bone.parent else "NONE"
                h = bone.head_local
                t = bone.tail_local
                lines.append(f"Bone: {bone.name:<20} | Parent: {p_name:<20}")
                lines.append(f"      Head: ({h.x:7.3f}, {h.y:7.3f}, {h.z:7.3f})")
                lines.append(f"      Tail: ({t.x:7.3f}, {t.y:7.3f}, {t.z:7.3f})")
                lines.append(f"      Length: {(t-h).length:7.3f}")
        else:
            lines.append("\nNO ARMATURE FOUND.")

        # Reconstruction Metadata
        lines.append("\nRECONSTRUCTION METADATA:")
        if arm:
            root_name = arm.get("carnivores_reconstruct_root", "N/A")
            root_idx = arm.get("carnivores_reconstruct_root_idx", "N/A")
            skipped_str = arm.get("carnivores_reconstruct_skipped", "")
            cluster_count = arm.get("carnivores_reconstruct_cluster_count", 1)
            parent_map = arm.get("carnivores_reconstruct_parent_map", "")
            lines.append(f"Selected Root: {root_name} (orig idx: {root_idx})")
            lines.append(f"Clusters Detected: {cluster_count}")
            if skipped_str:
                lines.append(f"Skipped Groups: {skipped_str}")
            else:
                lines.append("Skipped Groups: none")
            # Decode and display parent map
            try:
                import ast
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
            import numpy as np
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
                lines.append(f"⚠️  {mismatch_count} vertices ({pct:.1f}%) diverge from imported owners!")
                lines.append("   Tip: Use 'Reset to Imported Owners' to restore them.")
            else:
                lines.append("✅ Vertex groups match imported owner cache.")
        else:
            lines.append("\nDIVERGENCE CHECK: No owner cache or no VGs")

        # Write to Text Editor
        txt_name = "Carnivores_Rig_Debug"
        txt = bpy.data.texts.get(txt_name) or bpy.data.texts.new(txt_name)
        txt.clear()
        txt.write("\n".join(lines))
        
        # Switch area to Text Editor if possible, or just report
        self.report({'INFO'}, f"Debug info written to text datablock: {txt_name}")
        return {'FINISHED'}

class CARNIVORES_OT_reset_to_imported_owners(bpy.types.Operator):
    """Recreate vertex groups from the cached carnivores_owner_index attribute."""
    bl_idname = "carnivores.reset_to_imported_owners"
    bl_label = "Reset to Imported Owners"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            return False
        return "carnivores_owner_index" in (obj.data.attributes.keys() if obj.data else [])

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
        row = layout.row()
        row.prop(scene, "carnivores_nla_sound_enabled", text="Enable NLA Sound", toggle=True)
        row.operator(CARNIVORES_OT_toggle_nla_sound_playback.bl_idname, text="", icon='PLAY_SOUND' if not scene.carnivores_nla_sound_enabled else 'PAUSE')
        layout.prop(scene, "carnivores_nla_sound_volume", text="Preview Volume")
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
            box.label(text="Rigging Utilities:", icon='ARMATURE_DATA')

            if obj and obj.type == 'MESH':
                box.label(text="Pre-Reconstruct Smoothing:", icon='MOD_SMOOTH')
                box.prop(obj, "carnivores_reconstruct_smooth_weights")
                if obj.carnivores_reconstruct_smooth_weights:
                    sub = box.column(align=True)
                    sub.prop(obj, "carnivores_reconstruct_smooth_iterations")
                    sub.prop(obj, "carnivores_reconstruct_smooth_factor")
                    sub.prop(obj, "carnivores_reconstruct_smooth_joints_only")
                
                # Expose manual root override index
                box.separator()
                box.prop(obj, "carnivores_reconstruct_root_override")
                box.prop(obj, "carnivores_reconstruct_semantic_naming")
                box.separator()

            col = box.column(align=True)
            col.operator(CARNIVORES_OT_reconstruct_armature.bl_idname, icon='BONE_DATA')
            col.operator(CARNIVORES_OT_reset_to_imported_owners.bl_idname, icon='FILE_REFRESH')
            col.operator(CARNIVORES_OT_debug_rig_info.bl_idname, icon='TEXT')

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
        layout.label(text="NLA Tracks (Export Order):")
        row = layout.row()
        row.template_list(
            "CARNIVORES_UL_animation_list", "", 
            anim_data, "nla_tracks", 
            obj, "carnivores_active_nla_index", 
            rows=5
        )

        # --- Active Track Details ---
        idx = obj.carnivores_active_nla_index
        if 0 <= idx < len(anim_data.nla_tracks):
            active_track = anim_data.nla_tracks[idx]
            
            if active_track and active_track.strips:
                # For simplicity, assume 1 strip per track for .car workflow, or take the first one
                strip = active_track.strips[0] 
                action = strip.action
                
                if action:
                    box = layout.box()
                    
                    # Header / Strip Name
                    row = box.row(align=True)
                    row.prop(strip, "name", text="", icon='NLA_PUSHDOWN')
                    
                    # Sound
                    row = box.row(align=True)
                    row.prop(action, "carnivores_sound_ptr", text="Sound")
                    op = row.operator("carnivores.import_sound_for_action", text="", icon='FILE_FOLDER')
                    op.action_name = action.name
                    box.prop(action, "carnivores_sound_volume", text="Sound Volume")
                    
                    # KPS
                    row = box.row(align=True)
                    row.prop(action, "carnivores_kps_mode", text="") # Use the new EnumProperty

                    # Check existence directly for UI state
                    if "carnivores_kps" in action:
                        row.prop(action, '["carnivores_kps"]', text="KPS")
                    else: # AUTO mode
                        row.label(text=f"KPS: {scene.render.fps} (Scene FPS)")
                    
                    # Resync Button
                    row = box.row()
                    op = row.operator("carnivores.resync_animation", text="Re-Sync Timing", icon='FILE_REFRESH')
                    op.action_name = action.name
            elif active_track:
                layout.label(text="Empty Track (No Strips)", icon='INFO')
        
        layout.separator()
        draw_rigging_utilities()