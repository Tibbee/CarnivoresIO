import bpy
import bpy_extras.io_utils
import aud
import math
import os
from ..utils import animation as anim_utils
from ..utils import io as io_utils
from ..utils import common
from ..utils.logger import info, debug, warn, error

# Global dictionary to track playing sounds for each object
_playing_sounds = {}
_aud_device = None # Global aud device
_is_real_playback = False # Our reliable flag for actual playback state
_preview_restore_state = None

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
        if 'carnivores_sound' not in action:
            self.report({'ERROR'}, f"Animation '{action.name}' has no linked sound (missing 'carnivores_sound' property).")
            return {'CANCELLED'}

        sound_name = action['carnivores_sound']
        linked_sound = bpy.data.sounds.get(sound_name)

        if not linked_sound:
            self.report({'ERROR'}, f"Linked sound '{sound_name}' not found in Blender data.")
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
    global _is_real_playback, _playing_sounds
    _is_real_playback = False
    debug("Playback STOPPED. _is_real_playback = False")
    
    # If there are any lingering sounds, stop them now.
    if _playing_sounds:
        debug("Playback stopped, stopping all managed sounds.")
        for handle, _, _ in _playing_sounds.values():
            handle.stop()
        _playing_sounds.clear()

@bpy.app.handlers.persistent
def carnivores_nla_sound_handler(scene):
    global _playing_sounds, _is_real_playback, _preview_restore_state, _aud_device
    
    # This handler should ONLY run when our flag indicates real playback is happening.
    if not _is_real_playback:
        # debug("AUDIO: Handler skipped (not real playback)")
        return

    if not scene.carnivores_nla_sound_enabled:
        return

    device = get_aud_device()
    if not device:
        # debug("AUDIO: No audio device available in handler")
        return

    objects_with_active_sounds = {} # {obj: linked_sound_name}

    for obj in scene.objects:
        # 1. Priority: Preview Playback (Programmatic Tweak Mode)
        if _preview_restore_state and _preview_restore_state.get('obj') == obj:
             action_name = _preview_restore_state.get('action_name')
             if action_name:
                 action = bpy.data.actions.get(action_name)
                 
                 # Check if we are within the preview range (simple loop check)
                 # The handler loop ensures we stay in range, but for sound triggering:
                 if action:
                     sound_name = None
                     if getattr(action, 'carnivores_sound_ptr', None):
                         sound_name = action.carnivores_sound_ptr.name
                     elif 'carnivores_sound' in action:
                         sound_name = action['carnivores_sound']
                     
                     if sound_name:
                         objects_with_active_sounds[obj] = sound_name
                         continue # Skip to next object

        anim_data_container = anim_utils.get_active_animation_data(obj)
        
        # 2. Standard Tweak Mode (Shift+Tab)
        if anim_data_container and anim_data_container.nla_tracks:
            current_action = None
            if scene.is_nla_tweakmode:
                active_action = anim_data_container.action if anim_data_container else None
                if active_action:
                    for track in anim_data_container.nla_tracks:
                        for strip in track.strips:
                            if strip.action == active_action:
                                if strip.frame_start <= scene.frame_current < strip.frame_end:
                                    current_action = active_action
                                break
                        if current_action:
                            break

            # Use the pointer property first, fallback to legacy string property if needed (optional)
            if current_action:
                sound_name = None
                if getattr(current_action, 'carnivores_sound_ptr', None):
                     sound_name = current_action.carnivores_sound_ptr.name
                elif 'carnivores_sound' in current_action:
                     sound_name = current_action['carnivores_sound']
                
                if sound_name:
                    objects_with_active_sounds[obj] = sound_name

    # Stop sounds that should no longer be playing
    for obj_playing in list(_playing_sounds.keys()):
        current_handle, current_sound_name, _ = _playing_sounds[obj_playing]
        if obj_playing not in objects_with_active_sounds or objects_with_active_sounds[obj_playing] != current_sound_name:
            debug(f"AUDIO: Stopping sound '{current_sound_name}' for {obj_playing.name}")
            try:
                current_handle.stop()
            except Exception as e:
                warn(f"AUDIO: Error stopping sound (cleanup): {e}")
            del _playing_sounds[obj_playing]

    # Start new sounds
    for obj_active, linked_sound_name in objects_with_active_sounds.items():
        if obj_active in _playing_sounds:
            continue

        debug(f"AUDIO: Triggering sound '{linked_sound_name}' for {obj_active.name}")
        linked_sound_data_block = bpy.data.sounds.get(linked_sound_name)
        if not linked_sound_data_block:
            debug(f"AUDIO: Sound datablock '{linked_sound_name}' not found")
            continue

        try:
            sound_factory = linked_sound_data_block.factory
            
            # Fallback: If Blender failed to create a factory (common with some external files),
            # try loading it directly via aud using the absolute path.
            if not sound_factory:
                abs_path = bpy.path.abspath(linked_sound_data_block.filepath)
                if os.path.exists(abs_path):
                    try:
                        sound_factory = aud.Sound.file(abs_path)
                        debug(f"Loaded sound factory from file fallback: {abs_path}")
                    except Exception as e:
                        warn(f"NLA Sound Warning: Fallback load failed for '{linked_sound_name}': {e}")

            if sound_factory:
                # Play the sound without looping (looping handled by re-triggering or future features)
                handle = device.play(sound_factory)
                _playing_sounds[obj_active] = (handle, linked_sound_name, sound_factory)
            else:
                # Factory creation failed (broken file or invalid path)
                warn(f"NLA Sound Warning: Could not load audio factory for '{linked_sound_name}'")

        except Exception as e:
            error(f"NLA Sound Error: Could not play sound '{linked_sound_name}' for {obj_active.name}: {e}")
            
            # Check for critical OpenAL/Device errors that require a reset
            err_str = str(e)
            if "Buffer" in err_str or "OpenAL" in err_str:
                error("AUDIO: Critical OpenAL Error detected. Resetting audio device to recover...")
                try:
                    # Invalidate global device so get_aud_device() creates a new one next frame
                    _aud_device = None 
                    # Clear handles as they belong to the dead device
                    _playing_sounds.clear() 
                except:
                    pass
            
            if obj_active in _playing_sounds:
                del _playing_sounds[obj_active]

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
            # Stop all aud handles when disabling
            global _playing_sounds
            for obj_id, (handle, sound_name, _) in list(_playing_sounds.items()): # Use list() to iterate over a copy
                handle.stop()
                del _playing_sounds[obj_id]
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
    global _preview_restore_state, _playing_sounds
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
        # Detected a loop (e.g. wrap around from end to start)
        should_restart = True
        
    _preview_restore_state['last_frame'] = current

    if should_restart:
        # Loop audio: Stop current sound so handler restarts it
        obj = _preview_restore_state.get('obj')
        if obj and obj in _playing_sounds:
            handle, _, _ = _playing_sounds[obj]
            handle.stop()
            del _playing_sounds[obj]

@bpy.app.handlers.persistent
def clear_aud_device_on_new_file(scene):
    global _aud_device, _playing_sounds, _is_real_playback

    debug(f"AUDIO: clear_aud_device_on_new_file called. Scene: {scene.name if scene else 'None'}")
    debug(f"AUDIO: Current Handlers (load_post): {len(bpy.app.handlers.load_post)}")

    debug("AUDIO: New file loaded — resetting audio system")

    # Stop all currently playing sounds
    for handle, _, _ in _playing_sounds.values():
        try:
            handle.stop()
        except Exception as e:
            warn(f"Error stopping audio handle on new file load: {e}")
    _playing_sounds.clear()

    # Hard reset playback flag
    _is_real_playback = False

    # Properly shut down aud device
    if _aud_device is not None:
        try:
            debug("AUDIO: Stopping aud device...")
            _aud_device.stopAll()
        except Exception as e:
            warn(f"Error stopping aud device on new file load: {e}")
        _aud_device = None

    # Re-enable sound playback for new scene if property exists
    if hasattr(bpy.context.scene, "carnivores_nla_sound_enabled"):
        bpy.context.scene.carnivores_nla_sound_enabled = True
        debug("AUDIO: Re-enabled carnivores_nla_sound_enabled for new scene.")
    else:
        debug("AUDIO: 'carnivores_nla_sound_enabled' property not found in new scene.")

    # Re-add handlers (if missing)
    h = bpy.app.handlers
    debug(f"AUDIO: Checking handlers... frame_change_post len: {len(h.frame_change_post)}")
    
    if carnivores_nla_sound_handler not in h.frame_change_post:
        h.frame_change_post.append(carnivores_nla_sound_handler)
        debug("AUDIO: Re-added carnivores_nla_sound_handler")
    else:
        debug("AUDIO: carnivores_nla_sound_handler already present")

    if playback_started_handler not in h.animation_playback_pre:
        h.animation_playback_pre.append(playback_started_handler)
        debug("AUDIO: Re-added playback_started_handler")
        
    if playback_stopped_handler not in h.animation_playback_post:
        h.animation_playback_post.append(playback_stopped_handler)
        debug("AUDIO: Re-added playback_stopped_handler")

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
        if action.fcurves:
            # Check first curve path
            path = action.fcurves[0].data_path
            if path.startswith("key_blocks"):
                is_shape_key = True
        
        # Fallback for empty actions: Check if matching shape keys exist
        if not action.fcurves and obj.type == 'MESH' and obj.data.shape_keys:
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
        return obj and obj.type == 'MESH' and obj.vertex_groups

    def execute(self, context):
        obj = context.active_object
        try:
            anim_utils.reconstruct_armature(obj)
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

        # Write to Text Editor
        txt_name = "Carnivores_Rig_Debug"
        txt = bpy.data.texts.get(txt_name) or bpy.data.texts.new(txt_name)
        txt.clear()
        txt.write("\n".join(lines))
        
        # Switch area to Text Editor if possible, or just report
        self.report({'INFO'}, f"Debug info written to text datablock: {txt_name}")
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
            col = box.column(align=True)
            col.operator(CARNIVORES_OT_reconstruct_armature.bl_idname, icon='BONE_DATA')
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