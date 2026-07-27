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

# Audio playback state
_playing_sounds = {}           # deprecated: replaced by _active_sources below
_aud_device = None
_is_real_playback = False
_preview_restore_state = None
_failed_sound_blocklist = {}   # {sound_name: expiry_timestamp}
_audio_reset_cooldown = 0.0    # monotonic timestamp for device-reset suppression

# Source-identity playback tracking (replaces _playing_sounds)
# key: (obj, action_name, strip_name, cycle)
# value: {'sound': Sound, 'handle': Handle, 'factory': Factory, 'state': 'playing'|'completed'}
_active_sources = {}

# Cycle tracking for NLA strip repeat detection
# key: (obj, strip_name)  ->  last seen cycle number
_strip_cycles = {}


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------

def _compute_strip_cycle(obj, strip, current_frame):
    """
    Return the current repeat cycle of an NLA strip, or 0 on first playthrough.
    Detect a new cycle when the current frame moves backward relative to the
    previous observation.
    """
    key = (obj, strip.name)
    prev_cycle = _strip_cycles.get(key, 0)
    last_frame = None

    # Unpack prev value if it is a (cycle, frame) tuple
    if isinstance(_strip_cycles.get(key), tuple):
        prev_cycle, last_frame = _strip_cycles[key]

    if last_frame is not None and current_frame < last_frame:
        prev_cycle += 1

    _strip_cycles[key] = (prev_cycle, current_frame)
    return prev_cycle


def _compute_audio_offset(strip, scene):
    """
    Calculate audio playback offset in seconds for the current scene frame
    relative to the start of the given NLA strip.

    Accounts for strip frame_start, action_frame_start, scale, and reverse.
    Returns 0.0 if the offset cannot be computed.
    """
    fps = scene.render.fps
    if fps <= 0:
        return 0.0

    current = scene.frame_current
    strip_start = strip.frame_start

    # Action-relative offset
    action_offset_frames = current - strip_start
    if strip.use_reverse:
        action_length = strip.action_frame_end - strip.action_frame_start
        action_offset_frames = action_length - action_offset_frames

    # Apply scale
    action_offset_frames /= strip.scale if strip.scale != 0 else 1.0

    return max(0.0, action_offset_frames / fps)


def _resolve_active_source(obj, scene):
    """
    Resolve the active audio source for an object in priority order:

      1. Extension track preview
      2. Blender NLA tweak mode
      3. Normal NLA playback

    Returns a source-identity tuple (action, snd, strip, cycle, offset) or
    (None, None, None, 0, 0.0) when nothing is active.
    """
    # --- 1. Preview mode ---
    if _preview_restore_state and _preview_restore_state.get('obj') == obj:
        action_name = _preview_restore_state.get('action_name')
        if action_name:
            action = bpy.data.actions.get(action_name)
            snd = anim_utils.resolve_action_sound(action) if action else None
            if snd:
                return (action, snd, None, 0, 0.0)

    anim_data = anim_utils.get_active_animation_data(obj)
    if not anim_data or not anim_data.nla_tracks:
        return (None, None, None, 0, 0.0)

    # --- 2. Tweak mode ---
    if scene.is_nla_tweakmode:
        active_action = anim_data.action
        if active_action:
            for track in anim_data.nla_tracks:
                if track.mute:
                    continue
                for strip in track.strips:
                    if strip.action != active_action:
                        continue
                    if strip.frame_start <= scene.frame_current < strip.frame_end:
                        snd = anim_utils.resolve_action_sound(active_action)
                        if snd:
                            cycle = _compute_strip_cycle(obj, strip, scene.frame_current)
                            offset = _compute_audio_offset(strip, scene)
                            return (active_action, snd, strip, cycle, offset)

    # --- 3. Normal NLA playback ---
    for track in anim_data.nla_tracks:
        if track.mute:
            continue
        for strip in track.strips:
            if not strip.action:
                continue
            if strip.frame_start <= scene.frame_current < strip.frame_end:
                snd = anim_utils.resolve_action_sound(strip.action)
                if snd:
                    cycle = _compute_strip_cycle(obj, strip, scene.frame_current)
                    offset = _compute_audio_offset(strip, scene)
                    return (strip.action, snd, strip, cycle, offset)

    return (None, None, None, 0, 0.0)


def _make_source_key(obj, action, strip, cycle):
    """Construct a stable tuple key for source-identity tracking."""
    action_name = action.name if action else "preview"
    strip_name = strip.name if strip else "preview"
    return (obj, action_name, strip_name, cycle)


def _clear_all_playback_state():
    """Stop all handles and reset source/cycle tracking."""
    global _active_sources, _strip_cycles
    for snd_info in _active_sources.values():
        try:
            snd_info['handle'].stop()
        except Exception:
            pass
    _active_sources.clear()
    _strip_cycles.clear()


def _load_sound_factory(sound_datablock):
    """Return an aud.Sound factory for a Blender Sound datablock, or None."""
    factory = sound_datablock.factory
    if factory:
        return factory

    abs_path = bpy.path.abspath(sound_datablock.filepath)
    if os.path.exists(abs_path):
        try:
            factory = aud.Sound.file(abs_path)
            debug(f"Loaded sound factory from file fallback: {abs_path}")
            return factory
        except Exception as e:
            warn(f"NLA Sound Warning: Fallback load failed for '{sound_datablock.name}': {e}")
    return None


def _remove_legacy_sounds():
    """Stop and remove any entries remaining in the old _playing_sounds dict."""
    global _playing_sounds
    for obj_key, (handle, _, _) in list(_playing_sounds.items()):
        try:
            handle.stop()
        except Exception:
            pass
    _playing_sounds.clear()


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
    global _aud_device, _is_real_playback, _failed_sound_blocklist, _audio_reset_cooldown

    h = bpy.app.handlers
    if carnivores_nla_sound_handler in h.frame_change_post:
        h.frame_change_post.remove(carnivores_nla_sound_handler)
    if playback_started_handler in h.animation_playback_pre:
        h.animation_playback_pre.remove(playback_started_handler)
    if playback_stopped_handler in h.animation_playback_post:
        h.animation_playback_post.remove(playback_stopped_handler)
    if clear_aud_device_on_new_file in h.load_post:
        h.load_post.remove(clear_aud_device_on_new_file)

    _remove_legacy_sounds()
    _clear_all_playback_state()

    if _aud_device is not None:
        try:
            _aud_device.stopAll()
        except Exception:
            pass
        _aud_device = None

    _is_real_playback = False
    _failed_sound_blocklist.clear()
    _audio_reset_cooldown = 0.0

def get_aud_device():
    global _aud_device
    if _aud_device is None:
        debug("AUDIO: Creating new aud.Device()")
        try:
            _aud_device = aud.Device()
        except Exception as e:
            error(f"AUDIO: Failed to create aud.Device(): {e}")
    return _aud_device

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
    """This handler is called by Blender right before animation playback starts."""
    global _is_real_playback
    _is_real_playback = True
    debug("Playback STARTED. _is_real_playback = True")

@bpy.app.handlers.persistent
def playback_stopped_handler(scene):
    """This handler is called by Blender right after animation playback stops."""
    global _is_real_playback
    _is_real_playback = False
    debug("Playback STOPPED. _is_real_playback = False")
    
    _remove_legacy_sounds()
    _clear_all_playback_state()

@bpy.app.handlers.persistent
def carnivores_nla_sound_handler(scene):
    global _active_sources, _is_real_playback, _aud_device, _failed_sound_blocklist, _audio_reset_cooldown

    if not _is_real_playback:
        return
    if not scene.carnivores_nla_sound_enabled:
        return

    device = get_aud_device()
    if not device:
        return

    # Migrate any legacy _playing_sounds entries
    _remove_legacy_sounds()

    # Mark completed handles (stop status or position >= length)
    for src_key, snd_info in list(_active_sources.items()):
        if snd_info['state'] == 'playing':
            try:
                status = snd_info['handle'].status
                if status in (aud.STATUS_STOPPED, aud.STATUS_INVALID):
                    snd_info['state'] = 'completed'
                elif status == aud.STATUS_PLAYING:
                    # Check if position advanced past the sound length
                    if hasattr(snd_info['handle'], 'position') and hasattr(snd_info['handle'], 'length'):
                        if snd_info['handle'].length > 0 and snd_info['handle'].position >= snd_info['handle'].length:
                            snd_info['state'] = 'completed'
            except Exception:
                snd_info['state'] = 'completed'

    # --- Resolve desired sources ---
    desired_sources = {}  # source_key -> (action, snd, strip, cycle, offset)
    for obj in scene.objects:
        action, snd, strip, cycle, offset = _resolve_active_source(obj, scene)
        if snd:
            key = _make_source_key(obj, action, strip, cycle)
            desired_sources[key] = (obj, action, snd, strip, cycle, offset)

    # --- Stop stale sources ---
    for src_key in list(_active_sources.keys()):
        if src_key not in desired_sources:
            snd_info = _active_sources.pop(src_key)
            try:
                snd_info['handle'].stop()
            except Exception as e:
                warn(f"AUDIO: Error stopping stale sound: {e}")

    # --- Start or restart needed sources ---
    for src_key, (obj, action, snd, strip, cycle, offset) in desired_sources.items():
        if src_key in _active_sources:
            snd_info = _active_sources[src_key]
            if snd_info['state'] == 'completed':
                prev_sound = snd_info.get('sound')
                if prev_sound != snd:
                    try:
                        snd_info['handle'].stop()
                    except Exception:
                        pass
                    del _active_sources[src_key]
                else:
                    continue  # completed and still current — don't restart
            else:
                # Already playing for this source — leave it alone
                continue

        # Skip blocklisted sounds
        if snd.name in _failed_sound_blocklist:
            expiry = _failed_sound_blocklist[snd.name]
            if time.time() < expiry:
                continue
            else:
                del _failed_sound_blocklist[snd.name]

        factory = _load_sound_factory(snd)
        if not factory:
            warn(f"NLA Sound Warning: Could not load audio factory for '{snd.name}'")
            _failed_sound_blocklist[snd.name] = time.time() + 5.0
            continue

        try:
            debug(f"AUDIO: Triggering '{snd.name}' for {obj.name} (cycle {cycle}, offset {offset:.3f}s)")
            handle = device.play(factory)
            if offset > 0.0:
                handle.position = offset
            _active_sources[src_key] = {
                'sound': snd,
                'handle': handle,
                'factory': factory,
                'state': 'playing',
            }
        except Exception as e:
            error(f"NLA Sound Error: Could not play '{snd.name}': {e}")
            _failed_sound_blocklist[snd.name] = time.time() + 5.0

            err_str = str(e)
            if "Buffer" in err_str or "OpenAL" in err_str:
                now = time.monotonic()
                if now > _audio_reset_cooldown + 5.0:
                    error("AUDIO: Critical OpenAL Error detected. Resetting audio device to recover...")
                    try:
                        if _aud_device is not None:
                            _aud_device.stopAll()
                        _aud_device = None
                        _active_sources.clear()
                        _strip_cycles.clear()
                        _audio_reset_cooldown = now
                    except:
                        pass
                else:
                    warn("AUDIO: Skipping device reset (cooldown active).")

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
            _remove_legacy_sounds()
            _clear_all_playback_state()
            debug("All playing sounds stopped and cleared.")
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
    global _preview_restore_state, _active_sources
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
            # Find the preview source key and mark it for retrigger
            preview_key = (obj, action_name, "preview", 0)
            if preview_key in _active_sources:
                snd_info = _active_sources[preview_key]
                try:
                    if snd_info['handle'].status == aud.STATUS_PLAYING:
                        snd_info['handle'].position = 0.0
                    else:
                        snd_info['handle'].position = 0.0
                        snd_info['handle'].resume()
                except Exception:
                    # Handle is dead — remove so handler recreates it
                    try:
                        snd_info['handle'].stop()
                    except Exception:
                        pass
                    del _active_sources[preview_key]

@bpy.app.handlers.persistent
def clear_aud_device_on_new_file(scene):
    global _aud_device, _is_real_playback, _failed_sound_blocklist, _audio_reset_cooldown

    debug("AUDIO: New file loaded — resetting audio system")

    _remove_legacy_sounds()
    _clear_all_playback_state()

    _is_real_playback = False
    _failed_sound_blocklist.clear()
    _audio_reset_cooldown = 0.0

    if _aud_device is not None:
        try:
            debug("AUDIO: Stopping aud device...")
            _aud_device.stopAll()
        except Exception as e:
            warn(f"Error stopping aud device on new file load: {e}")
        _aud_device = None

    # Clean up temp sound files from the previous session
    anim_utils.cleanup_temp_sound_files()

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
            anim_utils.reconstruct_armature(obj, root_override_idx=override_idx)
            self.report({'INFO'}, "Armature reconstructed and assigned.")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Reconstruction failed: {e}")
            import traceback
            traceback.print_exc()
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