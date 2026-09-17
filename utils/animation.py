import bpy
import hashlib
import json
from dataclasses import replace
from mathutils import Vector
import re
import wave
import io
import os
import struct
import tempfile
import uuid
import aud
import numpy as np
from .common import timed
from .io import apply_import_matrix
from . import io as io_utils
from .logger import info, debug, warn, error

# Modifier types that never change evaluated vertex positions; the shape-key
# fast path may safely ignore them. Every other visible modifier (Mirror,
# Subdivision Surface, Decimate, Array, Cast, Wave, Lattice, ...) deforms or
# re-topsologizes the mesh and invalidates direct shape-key sampling.
FAST_PATH_SAFE_MODIFIER_TYPES = frozenset({
    'UV_PROJECT',      # modifies UV coordinates only
    'UV_WARP',         # modifies UV coordinates only
    'WEIGHTED_NORMAL', # adjusts custom normals only
    'NORMAL_EDIT',     # adjusts custom normals only
})

from .rig_reconstruction import (
    OWNER_MAPPING_PROPERTY,
    analyze_rig_geometry,
    build_mesh_analysis_input,
    build_topology_rig_proposal,
    mesh_analysis_checksum,
    reconcile_rig_export,
    rig_proposal_from_metadata,
    rig_proposal_to_metadata,
    proposal_edge_key,
    raw_ids_from_metadata,
    _deterministic_roll_reference,
)


def sound_datablock_to_factory(sound_datablock):
    """Return an aud.Sound factory from a Blender Sound datablock.

    Tries: Blender's built-in factory, external file fallback,
    then packed data unpacked to a managed temp file.
    Returns None if all sources fail.
    """
    # 1. Blender's built-in factory
    factory = sound_datablock.factory
    if factory:
        return factory

    # 2. External file fallback
    abs_path = bpy.path.abspath(sound_datablock.filepath)
    if os.path.exists(abs_path):
        try:
            return aud.Sound.file(abs_path)
        except Exception as e:
            warn(f"Could not load sound factory from file '{abs_path}': {e}")

    # 3. Packed data — unpack to temp file for aud.Sound.file() playback
    pf = sound_datablock.packed_file
    if pf:
        try:
            raw = pf.data
            with wave.open(io.BytesIO(raw), 'rb') as wf:
                nchannels = wf.getnchannels()
                sampwidth = wf.getsampwidth()
                framerate = wf.getframerate()
                nframes = wf.getnframes()
                if nframes == 0:
                    return None
                frames = wf.readframes(nframes)

            if sampwidth == 1:
                dtype = np.uint8
            elif sampwidth == 2:
                dtype = np.int16
            elif sampwidth == 4:
                dtype = np.int32
            else:
                warn(f"Unsupported sample width {sampwidth} for packed sound '{sound_datablock.name}'")
                return None

            data = np.frombuffer(frames, dtype=dtype).astype(np.float32)
            if sampwidth == 1:
                # WAV 8-bit PCM is unsigned: 128 is silence.
                data = (data - 128.0) / 128.0
            else:
                max_val = float(2 ** (sampwidth * 8 - 1))
                data /= max_val
            if nchannels > 1:
                # Downmix to mono by averaging channels
                data = data.reshape(-1, nchannels).mean(axis=1)
            data = data.reshape(1, -1)
            data = np.ascontiguousarray(data, dtype=np.float32)

            # Write to managed temp file for aud.Sound.file() — buffer() crashes in Blender 5.2
            temp_dir = _get_sound_temp_dir()
            import uuid
            temp_path = os.path.join(temp_dir, f"carnivores_packed_{sound_datablock.name}_{uuid.uuid4().hex[:8]}.wav")
            with wave.open(temp_path, 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(framerate)
                # Convert float32[-1,1] back to int16
                i16 = np.clip(np.round(data.ravel() * 32767), -32768, 32767).astype(np.int16)
                wf.writeframes(i16.tobytes())
            register_temp_sound_file(temp_path)
            debug(f"Unpacked to temp file for playback: {temp_path}")
            return aud.Sound.file(temp_path)
        except Exception as e:
            warn(f"Failed to create factory from packed sound '{sound_datablock.name}': {e}")

    return None

# Global state for sound import temp directory and on-demand packed-sound files
_temp_sound_files = set()
_temp_sound_dir = None


def _get_sound_temp_dir():
    """Return (and lazily create) a temp directory for holding WAV files during import."""
    global _temp_sound_dir
    if _temp_sound_dir is None:
        import uuid
        _temp_sound_dir = os.path.join(tempfile.gettempdir(), f"carnivores_io_sounds_{uuid.uuid4().hex[:8]}")
        os.makedirs(_temp_sound_dir, exist_ok=True)
        debug(f"Created temp sound directory: {_temp_sound_dir}")
    return _temp_sound_dir


def register_temp_sound_file(filepath):
    """Track a temp WAV created by the extension for later cleanup."""
    _temp_sound_files.add(filepath)


def cleanup_temp_sound_files():
    """Remove all tracked temporary sound files. Idempotent."""
    global _temp_sound_files, _temp_sound_dir

    for path in list(_temp_sound_files):
        try:
            if os.path.exists(path):
                os.remove(path)
                debug(f"Removed temp sound: {path}")
        except Exception as e:
            warn(f"Failed to remove temp sound {path}: {e}")
        finally:
            _temp_sound_files.discard(path)

    if _temp_sound_dir and os.path.isdir(_temp_sound_dir):
        try:
            remaining = os.listdir(_temp_sound_dir)
            if not remaining:
                os.rmdir(_temp_sound_dir)
                debug(f"Removed empty temp sound directory: {_temp_sound_dir}")
            else:
                warn(f"Temp sound directory not empty ({len(remaining)} files remain): {_temp_sound_dir}")
        except Exception as e:
            warn(f"Failed to remove temp sound directory {_temp_sound_dir}: {e}")
    _temp_sound_dir = None

# --- Blender 5.0+ Compatibility Helpers ---

def get_action_fcurves_storage(action, slot_type='KEY', slot_name="ShapeKeys"):
    """
    Returns the object that holds .fcurves (either action or channelbag)
    and ensures the slot exists for Blender 5.0+.
    """
    if bpy.app.version >= (5, 0, 0):
        import bpy_extras.anim_utils
        # Find or create slot
        target_slot = None
        for slot in action.slots:
            # In 5.0+, ActionSlot uses target_id_type and name_display
            if slot.target_id_type == slot_type and slot.name_display == slot_name:
                target_slot = slot
                break
        
        if not target_slot:
            target_slot = action.slots.new(id_type=slot_type, name=slot_name)
            
        return bpy_extras.anim_utils.action_ensure_channelbag_for_slot(action, target_slot)
    else:
        return action

def iter_action_fcurves(action):
    """
    Yields all fcurves in an action, handling both legacy and 5.0+ structures.
    """
    if not action:
        return

    # Check for slots (Blender 5.0+)
    if bpy.app.version >= (5, 0, 0) and hasattr(action, "slots"):
        import bpy_extras.anim_utils
        for slot in action.slots:
            # We use get_channelbag_for_slot which might return None if no bag exists
            bag = bpy_extras.anim_utils.action_get_channelbag_for_slot(action, slot)
            if bag:
                for fc in bag.fcurves:
                    yield fc
    # Fallback for legacy
    elif hasattr(action, "fcurves"):
        for fc in action.fcurves:
            yield fc

@timed('create_shape_keys_from_car_animations')
def create_shape_keys_from_car_animations(obj, animations, import_matrix_np, use_absolute=False):
    if not animations:
        debug("No animations to import")
        return
    mesh = obj.data
    vcount = len(mesh.vertices)
    total_keys = 0
    # Initialize shape keys if needed (first add creates Basis from current verts)
    if mesh.shape_keys is None:
        debug("Initializing shape keys (creating Basis)")
        obj.shape_key_add(name="Basis")  # Auto-creates obj.data.shape_keys

    sk_data = mesh.shape_keys
    sk_data.use_relative = not use_absolute

    for anim in animations:
        anim_name = anim['name']
        frames_count = anim['frames_count']
        positions = anim['positions']  # (frames_count, vcount, 3) float32
        if positions.shape != (frames_count, vcount, 3):
            warn(f"Skipping {anim_name} (invalid positions shape {positions.shape})")
            continue
        # Transform every frame in one NumPy operation, avoiding one small
        # matrix multiplication and allocation per shape key.
        transformed_positions = apply_import_matrix(
            positions.reshape(-1, 3), import_matrix_np
        ).reshape(positions.shape)
        for frame_i in range(frames_count):  # All frames as keys (Basis is static verts)
            key_name = f"{anim_name}.Frame_{frame_i+1:03d}"
            # Add new key (from_mix=False to base on Basis)
            key = obj.shape_key_add(name=key_name, from_mix=False)
            key.data.foreach_set('co', transformed_positions[frame_i].ravel())
            total_keys += 1
        debug(f"Added {frames_count} keys for '{anim_name}'")
    mesh.update()
    info(f"Total keys added: {total_keys} across {len(animations)} animations")

@timed('create_shape_key_action')
def create_shape_key_action(obj, action_name="CarAnimation"):
    """Ensure the object has a shape key action assigned."""
    if not obj or obj.type != 'MESH':
        error('Selected object is not a mesh')
        return None

    mesh = obj.data
    if not mesh.shape_keys:
        error('Object has no shape keys')
        return None

    sk_data = mesh.shape_keys
    sk_data.animation_data_create()

    # Create or reuse action
    action = bpy.data.actions.get(action_name)
    if action is None:
        action = bpy.data.actions.new(name=action_name)
        debug(f"Created new action: {action.name}")
    else:
        debug(f"Reusing existing action: {action.name}")

    sk_data.animation_data.action = action
    debug(f"Assigned to shape keys of '{obj.name}'")
    return action

@timed('keyframe_shape_key_animation_as_action')
def keyframe_shape_key_animation_as_action(obj, anim_name, frame_start=1, kps=None, scene_fps=None, use_absolute=False, use_kps_timing=True, key_blocks=None):
    if not obj or obj.type != 'MESH':
        error('Selected object is not a mesh')
        return
    mesh = obj.data
    if not mesh.shape_keys:
        error('Object has no shape keys')
        return
    sk_data = mesh.shape_keys
    sk_data.animation_data_create()

    suffix = "_Action"
    if anim_name.endswith(suffix):
        action_name = anim_name
    else:
        action_name = f"{anim_name}{suffix}"

    # Reuse existing action if possible to avoid duplicates (Action.001, etc.)
    action = bpy.data.actions.get(action_name)

    # Get storage for fcurves (Action or Channelbag)
    fc_storage = get_action_fcurves_storage(action) if action else None

    if action:
        # Clear existing data to re-bake
        if fc_storage and hasattr(fc_storage, "fcurves"):
             fc_storage.fcurves.clear()
    else:
        action = bpy.data.actions.new(name=action_name)
        # Re-fetch storage for new action
        fc_storage = get_action_fcurves_storage(action)

    # Determine KPS and Step
    target_kps = int(kps) if kps is not None else 15 

    if scene_fps is None:
        scene_fps = bpy.context.scene.render.fps

    # Calculate frame_step: How many Blender frames represent 1 Game Frame
    if use_kps_timing:
        frame_step = scene_fps / target_kps
    else:
        frame_step = 1.0

    # Store KPS in action for reference
    action["carnivores_kps"] = target_kps

    try:
        sk_data.animation_data.action = action
    except AttributeError:
        warn(f"Could not set active action '{action.name}' (likely driven by NLA). Continuing update...")

    if key_blocks is None:
        key_blocks = [kb for kb in sk_data.key_blocks if re.match(fr"^{re.escape(anim_name)}\.Frame_\d+", kb.name)]
    else:
        key_blocks = list(key_blocks)
    key_blocks.sort(key=lambda kb: kb.name)
    if not key_blocks:
        warn(f"No shape keys found for animation '{anim_name}'")
        return

    num_frames = len(key_blocks)
    
    debug(f"Creating Action '{action_name}' for '{anim_name}' (Absolute: {use_absolute}, KPS Timing: {use_kps_timing})")

    if use_absolute:
        # Absolute Path: Single F-Curve for 'eval_time'
        fc = fc_storage.fcurves.new(data_path='eval_time', index=-1)
        current_frame = float(frame_start)

        # Bulk allocation avoids repeated collection growth for long actions.
        if num_frames > 0:
            fc.keyframe_points.add(count=num_frames)
            for i in range(num_frames):
                kb = key_blocks[i]
                kp = fc.keyframe_points[i]
                # In absolute mode, ShapeKey.frame is its "address" on the timeline
                kp.co = (current_frame, kb.frame)
                kp.interpolation = 'LINEAR'
                kp.handle_left_type = 'VECTOR'
                kp.handle_right_type = 'VECTOR'
                current_frame += frame_step

        fc.update()
    else:
        # Relative Path: F-Curve for every shape key
        # Identify all shape keys, excluding Basis
        reference_key = sk_data.reference_key
        all_keys = [kb for kb in sk_data.key_blocks if kb != reference_key]
        group_keys = key_blocks
        other_keys = [kb for kb in all_keys if kb not in group_keys]

        # Create F-Curves upfront for all relevant keys (one per shape key)
        fcurves = {}
        for kb in other_keys + group_keys:
            data_path = f'key_blocks["{kb.name}"].value'
            fc = fc_storage.fcurves.new(data_path=data_path, index=-1)
            fcurves[kb.name] = fc

        current_frame = float(frame_start)

        # Initial State (Frame 0 of animation):
        # 1. Force all "Other" keys to 0 (CONSTANT) so they don't interfere
        # 2. Set First Frame of animation to 1.0
        # 3. Set Remaining Frames of animation to 0.0

        for kb in other_keys:
            kp = fcurves[kb.name].keyframe_points.insert(current_frame, 0.0)
            kp.interpolation = 'CONSTANT'

        # Set first key to 1.0
        kp = fcurves[group_keys[0].name].keyframe_points.insert(current_frame, 1.0)
        kp.interpolation = 'LINEAR'
        kp.handle_left_type = 'VECTOR'
        kp.handle_right_type = 'VECTOR'

        # Set all other group keys to 0.0 at start to ensure they start from nothing
        for kb in group_keys[1:]:
            kp = fcurves[kb.name].keyframe_points.insert(current_frame, 0.0)
            kp.interpolation = 'LINEAR'
            kp.handle_left_type = 'VECTOR'
            kp.handle_right_type = 'VECTOR'

        # Animate the sequence
        # Logic: Cross-fade. At each step, the previous key goes to 0, current goes to 1.

        prev_key = group_keys[0] 

        for i in range(1, num_frames):
            prev_frame_time = current_frame
            current_frame += frame_step
            curr_key = group_keys[i]

            # Previous key fades out to 0
            kp_prev = fcurves[prev_key.name].keyframe_points.insert(current_frame, 0.0)
            kp_prev.interpolation = 'LINEAR'
            kp_prev.handle_left_type = 'VECTOR'
            kp_prev.handle_right_type = 'VECTOR'

            # Current key ANCHOR: Force it to be 0 at the previous frame
            # This prevents it from ramping up all the way from the start of the animation
            kp_curr_anchor = fcurves[curr_key.name].keyframe_points.insert(prev_frame_time, 0.0)
            kp_curr_anchor.interpolation = 'LINEAR'
            kp_curr_anchor.handle_left_type = 'VECTOR'
            kp_curr_anchor.handle_right_type = 'VECTOR'

            # Current key fades in to 1
            kp_curr = fcurves[curr_key.name].keyframe_points.insert(current_frame, 1.0)
            kp_curr.interpolation = 'LINEAR'
            kp_curr.handle_left_type = 'VECTOR'
            kp_curr.handle_right_type = 'VECTOR'

            prev_key = curr_key

        # Update all curves
        for fc in fcurves.values():
            fc.update()

    return action

@timed('push_shape_key_action_to_nla')
def push_shape_key_action_to_nla(obj, strip_name=None, frame_start=1, frame_end=None):
    """
    Pushes the current shape key Action of the object into the NLA as a new strip.
    """
    if not obj or obj.type != 'MESH':
        error('Selected object is not a mesh')
        return None

    sk_data = obj.data.shape_keys
    if not sk_data or not sk_data.animation_data or not sk_data.animation_data.action:
        error('No active Action on shape keys. Create and keyframe first.')
        return None

    anim_data = sk_data.animation_data
    action = anim_data.action
    nla_tracks = anim_data.nla_tracks

    if strip_name is None:
        strip_name = action.name

    # Create or reuse a track
    if not nla_tracks:
        track = nla_tracks.new()
        track.name = f"{strip_name}_Track"
    else:
        # Reuse last or create new if overlapping
        track = nla_tracks[-1]
        if track.strips and track.strips[-1].frame_end > frame_start:
            track = nla_tracks.new()
            track.name = f"{strip_name}_Track"

    # Determine frame range dynamically
    if frame_end is None:
        start, end = get_action_frame_range(action)
        frame_start, frame_end = start, end

    # Add the strip to the NLA
    strip = track.strips.new(strip_name, int(frame_start), action)
    strip.frame_end = float(frame_end)
    anim_data.action = None  # Unlink active action (push down)

    debug(f"Action '{action.name}' pushed to NLA strip '{strip.name}' ({frame_start}-{frame_end})")
    return strip

@timed('auto_create_shape_key_actions_from_car')
def auto_create_shape_key_actions_from_car(obj, frame_step=1, parsed_animations=None, use_absolute=False, use_kps_timing=True):
    if not obj or obj.type != 'MESH':
        error('Selected object is not a mesh')
        return
    mesh = obj.data
    if not mesh.shape_keys:
        info('No shape keys on object; skipping animation setup.')
        return
    sk_data = mesh.shape_keys
    grouped_key_blocks = {}
    for key_block in sk_data.key_blocks:
        if '.Frame_' not in key_block.name:
            continue
        base_name = key_block.name.split('.Frame_', 1)[0]
        grouped_key_blocks.setdefault(base_name, []).append(key_block)

    # Create KPS lookup map if animations provided
    kps_map = {}
    if parsed_animations:
        for anim in parsed_animations:
            kps_map[anim['name']] = anim['kps']

    # Dict insertion order preserves the source shape-key order.
    base_names = list(grouped_key_blocks)

    if not base_names:
        info('No animation-style shape keys found (no .Frame_### pattern).')
        return
    debug(f"Found {len(base_names)} animation groups: {base_names}")

    actions = []
    for anim_name in base_names:
        debug(f"Processing animation '{anim_name}'...")
        # Retrieve KPS from map or default to None
        anim_kps = kps_map.get(anim_name)

        # Note: frame_step is now calculated internally based on KPS
        action = keyframe_shape_key_animation_as_action(
            obj, 
            anim_name, 
            frame_start=1, 
            kps=anim_kps, 
            use_absolute=use_absolute,
            use_kps_timing=use_kps_timing,
            key_blocks=grouped_key_blocks[anim_name],
        )
        if action:
            actions.append(action)
    # Batch NLA push: Inline overlap checks per action (no manual indexing)
    try:
        anim_data = sk_data.animation_data
        if actions:
            nla_tracks = anim_data.nla_tracks
            track = None
            num_tracks_used = 0
            # Reverse order so first animation in list becomes the top-most NLA track
            # (Tracks are added bottom-to-top, 0..N)
            for action in reversed(actions):
                strip_name = action.name.replace('_Action', '')
                start_frame, _ = get_action_frame_range(action)
                
                # Get last track or create first
                if track is None:
                    if nla_tracks:
                        track = nla_tracks[-1]
                    else:
                        track = nla_tracks.new()
                        track.name = strip_name
                        num_tracks_used += 1
                
                # Overlap check: If last strip ends after start_frame, new track
                if track.strips and track.strips[-1].frame_end > start_frame:
                    track = nla_tracks.new()
                    track.name = f'{strip_name}.{num_tracks_used + 1:03d}'
                    num_tracks_used += 1
                
                # Add strip
                strip = track.strips.new(strip_name, int(start_frame), action)
                strip.use_sync_length = True  # Tighten eval for discrete steps
            
            anim_data.action = None  # Clear active action
            debug(f"Pushed {len(actions)} actions to NLA batch (using {num_tracks_used} tracks).")
    except Exception as e:
        warn(f"NLA batch push failed (non-fatal): {e}")
    
    # Single update at end (key for perf)
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
    info('Completed all animations.')
    return actions

def get_action_frame_range(action):
    """Return the min/max frame numbers for keyframes in this Action."""
    if not action:
        return (1, 1)
        
    all_fcurves = list(iter_action_fcurves(action))
    if not all_fcurves:
        return (1, 1)

    minimum = None
    maximum = None
    for fcurve in all_fcurves:
        for keyframe in fcurve.keyframe_points:
            frame = keyframe.co[0]
            minimum = frame if minimum is None else min(minimum, frame)
            maximum = frame if maximum is None else max(maximum, frame)
    if minimum is None:
        return (1, 1)

    return (float(minimum), float(maximum))

@timed('import_car_sounds')
def import_car_sounds(self, sounds, model_name, context, referenced_indices=None):
    referenced = None if referenced_indices is None else {int(index) for index in referenced_indices}
    imported_sounds = [None] * len(sounds)
    for idx, s in enumerate(sounds):
        if referenced is not None and idx not in referenced:
            continue
        sound_name = s['name']
        data = s['data']
        if data.size == 0:
            warn(f"Skipping empty sound '{sound_name}' (0 samples).")
            continue
        # Create temp WAV in dedicated temp directory
        temp_dir = _get_sound_temp_dir()
        temp_path = os.path.join(temp_dir, f"{sound_name}_{idx}.wav")
        try:
            # Write a minimal PCM16 mono 22050 Hz WAV in a single pass.
            # Data from the CAR file is already int16 mono 22050, so no
            # conversion is needed — only the 44-byte RIFF header.
            data_bytes = data.tobytes()
            sample_count = data.size
            data_size = len(data_bytes)
            header = struct.pack(
                '<4sI4s4sIHHIIHH4sI',
                b'RIFF', 36 + data_size, b'WAVE',
                b'fmt ', 16, 1, 1, 22050, 22050 * 2, 2, 16,
                b'data', data_size,
            )
            with open(temp_path, 'wb') as f:
                f.write(header)
                f.write(data_bytes)
            sound_block = bpy.data.sounds.load(temp_path)
            sound_block.name = sound_name
            sound_block.pack()
            sound_block["carnivores_duration_seconds"] = sample_count / 22050.0
            imported_sounds[idx] = sound_block
            info(f"Imported and packed sound '{sound_block.name}' ({sample_count} samples).")
        except Exception as e:
            error(f"Failed to import sound '{sound_name}': {str(e)}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)
    return imported_sounds

@timed('associate_sounds_with_animations')
def associate_sounds_with_animations(self, obj, animations, cross_ref, imported_sounds, actions=None):
    if not animations or cross_ref is None or not imported_sounds:
        return

    # If actions are not passed directly, fall back to the old lookup method
    action_list = actions or bpy.data.actions

    for anim_idx, anim in enumerate(animations):
        if anim_idx >= len(cross_ref):
            break  # Table size limit
        
        sound_idx = cross_ref[anim_idx]
        if sound_idx == -1 or sound_idx >= len(imported_sounds):
            continue

        linked_sound = imported_sounds[sound_idx]
        if linked_sound is None:
            continue
        action_name = f"{anim['name']}_Action"
        
        # Find the action in the provided list or the fallback list
        action = next((act for act in action_list if act.name == action_name), None)

        if action:
            # Use the PointerProperty registered on bpy.types.Action
            action.carnivores_sound_ptr = linked_sound
            info(f"Associated sound '{linked_sound.name}' with animation '{anim['name']}'.")
        else:
            warn(f"Action '{action_name}' not found for sound association.")

@timed('rescale_standard_action')
def rescale_standard_action(action, kps, scene_fps):
    """
    Rescales a standard Object/Armature action so that its keyframes 
    align with the timing dictated by KPS and Scene FPS.
    
    Assumption: The existing keyframes represent sequential 'Game Frames'.
    We map the i-th unique keyframe time to: StartFrame + (i * FrameStep).
    """
    if not action:
        return

    all_fcurves = list(iter_action_fcurves(action))
    if not all_fcurves:
        return

    # Calculate target step
    if kps <= 0: kps = 1
    frame_step = scene_fps / kps
    
    debug(f"Rescaling '{action.name}' to {kps} KPS (Step: {frame_step:.2f})")

    # 1. Collect all unique frame times
    unique_frames = set()
    for fc in all_fcurves:
        for kp in fc.keyframe_points:
            unique_frames.add(kp.co[0])
    
    sorted_frames = sorted(list(unique_frames))
    if not sorted_frames:
        return

    start_frame = sorted_frames[0]
    
    # 2. Build Mapping: Old Time -> New Time
    # We treat the existing sorted frames as indices 0, 1, 2...
    frame_map = {}
    for i, old_frame in enumerate(sorted_frames):
        new_frame = start_frame + (i * frame_step)
        frame_map[old_frame] = new_frame

    # 3. Apply Mapping
    for fc in all_fcurves:
        for kp in fc.keyframe_points:
            old_time = kp.co[0]
            if old_time in frame_map:
                new_time = frame_map[old_time]
                kp.co[0] = new_time
                
                # Shift handles to preserve relative offset
                # (Simple shift; does not scale handle influence, effectively making curves 'sharper' if slowing down)
                kp.handle_left[0] = new_time + (kp.handle_left[0] - old_time)
                kp.handle_right[0] = new_time + (kp.handle_right[0] - old_time)

        fc.update()
        
    action["carnivores_kps"] = kps

def resolve_action_sound(action):
    """
    Resolve the Sound datablock linked to an Action.

    Returns the bpy.types.Sound pointer from action.carnivores_sound_ptr
    when set, otherwise resolves the legacy action["carnivores_sound"] name
    through bpy.data.sounds. Returns None if neither resolves.
    """
    if not action:
        return None
    ptr = getattr(action, 'carnivores_sound_ptr', None)
    if ptr:
        return ptr
    legacy_name = action.get('carnivores_sound')
    if legacy_name:
        return bpy.data.sounds.get(legacy_name)
    return None


def can_use_shape_key_fast_path(obj):
    """True when direct shape-key sampling matches dependency-graph evaluation.

    Direct sampling reads base-mesh coordinates and shape-key deltas without
    evaluating the dependency graph. Any visible modifier that can move or
    duplicate vertices would make that data diverge from what the user sees,
    so only known position-preserving modifiers keep the fast path enabled.
    Everything else forces the (slower) evaluated-mesh bake.
    """
    sk_data = obj.data.shape_keys if obj.type == 'MESH' and obj.data else None
    if sk_data is None:
        return False
    return all(
        modifier.type in FAST_PATH_SAFE_MODIFIER_TYPES
        for modifier in obj.modifiers
        if modifier.show_viewport
    )


def get_active_animation_data(obj):
    """
    Returns the animation data container (obj.animation_data or obj.data.shape_keys.animation_data)
    based on the object's 'carnivores_anim_source' setting.
    """
    if not obj:
        return None
        
    source = getattr(obj, "carnivores_anim_source", "AUTO")
    
    sk_anim = None
    if obj.type == 'MESH' and obj.data and obj.data.shape_keys:
        sk_anim = obj.data.shape_keys.animation_data
        
    obj_anim = obj.animation_data
    
    if source == 'SHAPE_KEYS':
        return sk_anim
    elif source == 'OBJECT':
        return obj_anim
    else: # AUTO
        if sk_anim:
            return sk_anim
        return obj_anim

OWNER_ATTR_NAME = "carnivores_owner_index"
OWNER_SOURCE_ATTR_NAME = "carnivores_owner_source"
RECONSTRUCTION_SOURCE_ID_PROPERTY = "carnivores_reconstruct_source_id"
TOPOLOGY_PROPOSAL_PAYLOAD_HASH_PROPERTY = "carnivores_rig_proposal_payload_hash"
RECONSTRUCTION_POLICIES = {
    "CREATE_NEW",
    "REPLACE_GENERATED",
    "UPDATE_GENERATED",
    "CANCEL_IF_RIGGED",
}
GENERATED_RIG_ALGORITHMS = {"LEGACY", "TOPOLOGY", "MOTION"}
GENERATED_WEIGHT_CHECKSUM_PROPERTY = "carnivores_reconstruct_generated_weight_checksum"
GENERATED_WEIGHT_CHECKSUM_VERSION_PROPERTY = "carnivores_reconstruct_generated_weight_checksum_version"
GENERATED_WEIGHT_CHECKSUM_VERSION = 2


def generated_weight_checksum(obj):
    """Return a stable checksum for the current deform vertex-group weights."""
    import hashlib

    digest = hashlib.sha256()
    digest.update(str(len(obj.data.vertices)).encode("ascii"))
    digest.update(b"\0")
    groups = sorted(
        ((int(group.index), str(group.name)) for group in obj.vertex_groups),
        key=lambda item: item[0],
    )
    for index, name in groups:
        encoded = name.encode("utf-8")
        digest.update(index.to_bytes(4, "little", signed=True))
        digest.update(len(encoded).to_bytes(4, "little"))
        digest.update(encoded)
    for vertex in obj.data.vertices:
        assignments = sorted(
            (
                int(assignment.group),
                np.float32(assignment.weight).tobytes(),
            )
            for assignment in vertex.groups
        )
        digest.update(int(vertex.index).to_bytes(4, "little", signed=False))
        digest.update(len(assignments).to_bytes(4, "little"))
        for group_index, weight_bytes in assignments:
            digest.update(group_index.to_bytes(4, "little", signed=True))
            digest.update(weight_bytes)
    return digest.hexdigest()


def store_generated_weight_checksum(armature, obj):
    checksum = generated_weight_checksum(obj)
    armature[GENERATED_WEIGHT_CHECKSUM_PROPERTY] = checksum
    armature[GENERATED_WEIGHT_CHECKSUM_VERSION_PROPERTY] = GENERATED_WEIGHT_CHECKSUM_VERSION
    return checksum


def _dominant_deform_owners(obj, name_entries=None, strict_metadata=False):
    """Return compact generated-owner IDs and vertices with no deform groups."""
    vertex_count = len(obj.data.vertices)
    group_index_to_compact = {}
    if name_entries:
        for entry in name_entries:
            if not isinstance(entry, dict):
                continue
            blender_name = str(entry.get("blender_name", ""))
            group = obj.vertex_groups.get(blender_name) if blender_name else None
            if group is not None and entry.get("compact_id") is not None:
                try:
                    group_index_to_compact[group.index] = int(entry["compact_id"])
                except (TypeError, ValueError):
                    continue
    if not group_index_to_compact and not strict_metadata:
        group_index_to_compact = {
            int(group.index): int(group.index) for group in obj.vertex_groups
        }
    dominant = np.full(vertex_count, -1, dtype=np.int32)
    no_deform = []
    for vertex in obj.data.vertices:
        winning = max(vertex.groups, key=lambda assignment: assignment.weight, default=None)
        if winning is None or winning.group not in group_index_to_compact:
            no_deform.append(vertex.index)
            continue
        dominant[vertex.index] = group_index_to_compact[winning.group]
    return dominant, tuple(no_deform)


def _load_armature_name_entries(armature):
    raw = armature.get("carnivores_reconstruct_bone_name_map", "[]")
    try:
        value = json.loads(raw) if raw else []
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return value if isinstance(value, list) else []


def _source_group_names(obj, group_count):
    """Return the complete source-name list when the mesh mapping provides it."""
    raw = obj.data.get(OWNER_MAPPING_PROPERTY, "")
    try:
        payload = json.loads(raw) if raw else {}
        names = payload.get("bone_names", []) if isinstance(payload, dict) else []
        names = [str(name) for name in names]
        if len(names) == int(group_count):
            return names
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        pass
    return None


def _reconciliation_export_bones(mapping):
    return tuple(mapping.bones) if mapping is not None else ()


def _build_rig_export_reconciliation(obj, armature, canonical_compact, canonical_raw_by_compact, skipped_groups=(), proposal=None):
    """Build Phase 7 reconciliation from the actual exporter mapping."""
    export_mapping = io_utils.collect_export_mapping(obj, np.identity(4, dtype=np.float64))
    name_entries = _load_armature_name_entries(armature) if armature else []
    strict_name_metadata = bool(
        armature
        and armature.get("carnivores_reconstruct_bone_name_map", "")
    )
    dominant, no_deform = _dominant_deform_owners(
        obj,
        name_entries,
        strict_metadata=strict_name_metadata and not name_entries,
    )

    additional_warnings = []
    additional_errors = []
    proposal_settings = proposal.settings if proposal is not None else {}
    if proposal is None and armature:
        raw_settings = armature.get("carnivores_reconstruct_proposal_settings", "")
        if raw_settings:
            try:
                stored_settings = json.loads(raw_settings)
                if not isinstance(stored_settings, dict):
                    raise ValueError("stored proposal settings must be an object")
                proposal_settings = stored_settings
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                additional_errors.append(
                    f"Stored reconstruction proposal settings are invalid: {exc}"
                )

    forced_diagnostics = []
    if proposal_settings:
        forced_diagnostics.extend(
            {"type": "FORCED_EDGE", "edge": list(edge)}
            for edge in proposal_settings.get("forced_edges", [])
        )
        forced_diagnostics.extend(
            {"type": "REJECTED_EDGE", "edge": list(edge)}
            for edge in proposal_settings.get("rejected_edges", [])
        )
    smoothing_enabled = bool(
        proposal_settings.get(
            "smooth_weights",
            armature.get("carnivores_reconstruct_smoothing", False) if armature else False,
        )
    )
    expected_checksum = (
        armature.get(GENERATED_WEIGHT_CHECKSUM_PROPERTY) if armature else None
    )
    if armature and expected_checksum is None:
        additional_warnings.append(
            "Stored generated-weight checksum is unavailable; current weights cannot be verified against the generated snapshot."
        )
    elif armature:
        checksum_version = armature.get(GENERATED_WEIGHT_CHECKSUM_VERSION_PROPERTY)
        try:
            unsupported_version = (
                checksum_version is not None
                and int(checksum_version) != GENERATED_WEIGHT_CHECKSUM_VERSION
            )
        except (TypeError, ValueError):
            unsupported_version = True
        if unsupported_version:
            additional_warnings.append(
                "Stored generated-weight checksum uses an unsupported version; current weights cannot be compared reliably."
            )
            expected_checksum = None
    actual_checksum = generated_weight_checksum(obj)
    source_names = _source_group_names(obj, len(canonical_raw_by_compact))
    if source_names is None:
        source_names = [
            str(entry.get("source_name", ""))
            for entry in name_entries
            if isinstance(entry, dict)
        ]
        if len(source_names) != len(canonical_raw_by_compact):
            source_names = None
    return reconcile_rig_export(
        canonical_compact_owners=canonical_compact,
        canonical_raw_by_compact=canonical_raw_by_compact,
        generated_dominant_owners=dominant,
        export_owner_by_vertex=export_mapping.vertex_owners,
        export_bones=_reconciliation_export_bones(export_mapping),
        skipped_groups=skipped_groups,
        source_group_names=source_names,
        no_deform_vertices=no_deform,
        export_mapping=export_mapping,
        smoothing_enabled=smoothing_enabled,
        generated_weight_checksum=actual_checksum,
        expected_weight_checksum=expected_checksum,
        forced_edge_diagnostics=forced_diagnostics,
        additional_warnings=additional_warnings,
        additional_errors=additional_errors,
    )


def _get_reconstruction_setting(obj, name, default):
    """Read explicit ID-property overrides before registered RNA defaults."""
    if obj is not None and name in obj:
        return obj.get(name)
    return getattr(obj, name, default) if obj is not None else default


def _ensure_reconstruction_source_id(obj, create=True):
    source_id = obj.get(RECONSTRUCTION_SOURCE_ID_PROPERTY)
    if source_id:
        return str(source_id)
    source_id = uuid.uuid4().hex
    if create:
        obj[RECONSTRUCTION_SOURCE_ID_PROPERTY] = source_id
    return source_id


def _find_rigged_armatures(obj):
    rigs = []
    for modifier in obj.modifiers:
        if modifier.type == 'ARMATURE' and modifier.object and modifier.object.type == 'ARMATURE':
            if modifier.object not in rigs:
                rigs.append(modifier.object)
    if obj.parent and obj.parent.type == 'ARMATURE' and obj.parent not in rigs:
        rigs.append(obj.parent)
    return rigs


def _find_generated_armature(obj, source_id):
    candidates = []
    for modifier in obj.modifiers:
        if modifier.type == 'ARMATURE' and modifier.object and modifier.object.type == 'ARMATURE':
            candidates.append(modifier.object)
    if obj.parent and obj.parent.type == 'ARMATURE':
        candidates.append(obj.parent)

    for armature in candidates:
        if armature.get("carnivores_rig_algorithm") not in GENERATED_RIG_ALGORITHMS:
            continue
        stored_source_id = armature.get(RECONSTRUCTION_SOURCE_ID_PROPERTY)
        stored_source_mesh = armature.get("carnivores_reconstruct_source_mesh")
        if (
            (stored_source_id and source_id and stored_source_id == source_id)
            or (stored_source_mesh == obj.name)
        ):
            return armature
    return None


def _find_other_armature_users(source_obj, armature):
    users = []
    for candidate in bpy.data.objects:
        if candidate in {source_obj, armature}:
            continue
        if candidate.parent == armature or any(
            modifier.type == 'ARMATURE' and modifier.object == armature
            for modifier in candidate.modifiers
        ):
            users.append(candidate)
            continue
        for constraint in candidate.constraints:
            if getattr(constraint, "target", None) == armature:
                users.append(candidate)
                break
            targets = getattr(constraint, "targets", None)
            if targets and any(getattr(target, "target", None) == armature for target in targets):
                users.append(candidate)
                break
        if candidate in users:
            continue
        animation_data = getattr(candidate, "animation_data", None)
        if animation_data and any(
            getattr(target, "id", None) == armature
            for fcurve in animation_data.drivers
            for variable in fcurve.driver.variables
            for target in variable.targets
        ):
            users.append(candidate)
    return users


def _resolve_reconstruction_policy(obj):
    policy = str(_get_reconstruction_setting(
        obj, "carnivores_reconstruct_rig_policy", "CREATE_NEW"
    )).upper()
    return policy if policy in RECONSTRUCTION_POLICIES else "CREATE_NEW"


def _validate_reconstruction_lifecycle(obj, policy, source_id):
    """Return the generated target and refuse unsafe user-rig operations."""
    rigs = _find_rigged_armatures(obj)
    generated = _find_generated_armature(obj, source_id)
    unrelated = [rig for rig in rigs if rig != generated]
    if unrelated:
        error(
            f"Mesh '{obj.name}' is already bound to a non-generated armature "
            f"('{unrelated[0].name}'); refusing automatic rig replacement."
        )
        return None, False
    if policy == "CANCEL_IF_RIGGED" and rigs:
        error("Mesh is already rigged; refusing reconstruction (CANCEL_IF_RIGGED).")
        return None, False
    if policy == "UPDATE_GENERATED" and generated is not None:
        other_users = _find_other_armature_users(obj, generated)
        if other_users:
            error(
                f"Generated armature '{generated.name}' is shared by "
                f"{len(other_users)} other object(s); refusing in-place UPDATE_GENERATED. "
                "Use CREATE_NEW or REPLACE_GENERATED."
            )
            return None, False
    return generated, True


def _get_reconstruction_owner_source(obj):
    mesh = obj.data
    attr = mesh.attributes.get(OWNER_SOURCE_ATTR_NAME)
    if not attr:
        return None
    if len(attr.data) != len(mesh.vertices):
        warn(
            f"Owner source attribute '{OWNER_SOURCE_ATTR_NAME}' on '{obj.name}' has an unexpected length; ignoring source cache."
        )
        return None

    owner_indices = np.empty(len(mesh.vertices), dtype=np.int32)
    attr.data.foreach_get("value", owner_indices)

    # Meshes imported before signed owner support cached file-level -1 as the
    # unsigned short value 65535. Correct it in memory without rewriting the
    # legacy source attribute merely by opening the blend file.
    if raw_ids_from_metadata(mesh.get(OWNER_MAPPING_PROPERTY)) is None and np.any(owner_indices == 65535):
        owner_indices[owner_indices == 65535] = -1
        warn(f"Interpreting legacy unsigned owner 65535 as -1 on '{obj.name}'.")
    return owner_indices


def _resolve_generated_bone_names(bone_names, existing=None):
    """Return Blender-safe, unique bone names.

    Applies ASCII cleaning, 32-byte truncation (Carnivores export limit), and
    duplicate resolution via deterministic numeric suffixes. ``existing`` is a
    set of names already reserved in the target armature.
    """
    existing = set(existing or ())
    resolved = []
    for name in bone_names:
        cleaned = "".join(
            char for char in str(name)
            if 32 <= ord(char) < 127
        ).strip()
        if not cleaned:
            cleaned = "Bone"
        if len(cleaned.encode("utf-8", "ignore")) > 31:
            cleaned = cleaned.encode("utf-8", "ignore")[:31].decode("utf-8", "ignore")
        candidate = cleaned
        suffix = 1
        while candidate in existing:
            stem = cleaned[:31 - len(str(suffix)) - 1]
            candidate = f"{stem}.{suffix}"
            suffix += 1
        existing.add(candidate)
        resolved.append(candidate)
    return resolved


def _truncate_utf8_name(name, max_bytes):
    encoded = str(name).encode("utf-8", "ignore")[:max_bytes]
    return encoded.decode("utf-8", "ignore")


def _resolve_blender_bone_names(bone_names, existing=None):
    """Preserve source names while making Blender-side names deterministic."""
    existing = set(existing or ())
    resolved = []
    for name in bone_names:
        cleaned = "".join(char for char in str(name) if ord(char) >= 32)
        if not cleaned.strip():
            cleaned = "Bone"
        cleaned = _truncate_utf8_name(cleaned, 63)
        candidate = cleaned
        suffix = 1
        while candidate in existing:
            suffix_text = f".{suffix}"
            suffix_budget = max(1, 63 - len(suffix_text.encode("utf-8")))
            stem = _truncate_utf8_name(cleaned, suffix_budget)
            candidate = f"{stem}{suffix_text}"
            suffix += 1
        existing.add(candidate)
        resolved.append(candidate)
    return resolved


def _resolve_reconstruction_name_pair(bone_names, reserved=None):
    blender_names = _resolve_blender_bone_names(bone_names, existing=reserved)
    export_names = _resolve_generated_bone_names(blender_names)
    return blender_names, export_names


def _build_reconstruction_bone_names(obj, group_count, owner_source=None):
    """Resolve source names while retaining a synthetic fallback for owner data."""
    generated_names = [f"Bone_{i}" for i in range(group_count)]
    raw_ids = None

    if obj and obj.data:
        raw_ids = raw_ids_from_metadata(obj.data.get(OWNER_MAPPING_PROPERTY))
        if raw_ids is not None and raw_ids.size != group_count:
            raw_ids = None

    if raw_ids is None and owner_source is not None:
        owner_source = np.asarray(owner_source, dtype=np.int32).reshape(-1)
        source_ids = np.unique(owner_source[owner_source >= 0])
        if source_ids.size == group_count:
            raw_ids = source_ids
        elif source_ids.size > 0:
            # Compatibility for meshes imported with the old min-offset schema.
            raw_ids = np.arange(group_count, dtype=np.int32) + int(source_ids[0])

    if raw_ids is not None:
        generated_names = [f"CarBone_{int(raw_id)}" for raw_id in raw_ids]

    # Vertex-group names are the editable/source names. Prefer them whenever a
    # group exists; generated CarBone names are only a fallback for owner-only
    # meshes with no editable groups yet.
    if obj and obj.vertex_groups:
        for vg in obj.vertex_groups:
            if 0 <= vg.index < group_count and vg.name:
                generated_names[vg.index] = vg.name
    return generated_names


def _ensure_reconstruction_vertex_groups(obj, group_count, owner_indices, owner_source=None):
    """Ensure owner compact IDs have stable vertex-group indices."""
    if len(obj.vertex_groups) >= group_count:
        return
    names = _build_reconstruction_bone_names(obj, group_count, owner_source=owner_source)
    for vertex_group in list(obj.vertex_groups):
        obj.vertex_groups.remove(vertex_group)
    io_utils.create_vertex_groups_from_bones(obj, names, owner_indices)


def _rename_vertex_groups_by_compact_id(obj, compact_ids, final_names):
    """Rename active deform groups by owner identity, never by string lookup."""
    groups = []
    for compact_id in compact_ids:
        group = obj.vertex_groups[compact_id] if 0 <= compact_id < len(obj.vertex_groups) else None
        groups.append(group)

    temporary_names = set(group.name for group in obj.vertex_groups)
    for index, group in enumerate(groups):
        if group is None or group.name == final_names[index]:
            continue
        temporary = f"__CIO_TMP_{index}"
        suffix = 1
        while temporary in temporary_names:
            temporary = f"__CIO_TMP_{index}_{suffix}"
            suffix += 1
        group.name = temporary
        temporary_names.add(temporary)

    for group, final_name in zip(groups, final_names):
        if group is not None:
            group.name = final_name


def _get_vertex_group_assignment_counts(obj):
    counts = {}
    for vertex in obj.data.vertices:
        for assignment in vertex.groups:
            counts[assignment.group] = counts.get(assignment.group, 0) + 1
    return counts


def _clear_reconstruction_metadata(arm_obj):
    for key in list(arm_obj.keys()):
        if key.startswith("carnivores_reconstruct_") or key.startswith("carnivores_rig_"):
            del arm_obj[key]


def _snapshot_reconstruction_state(obj):
    assignments = []
    for vertex in obj.data.vertices:
        assignments.append([(group.group, group.weight) for group in vertex.groups])
    referenced_armatures = {
        modifier.object for modifier in obj.modifiers
        if modifier.type == 'ARMATURE' and modifier.object
    }
    if obj.parent and obj.parent.type == 'ARMATURE':
        referenced_armatures.add(obj.parent)
    metadata = {
        armature: {key: armature[key] for key in armature.keys()}
        for armature in referenced_armatures
    }
    armature_states = {
        armature: {
            "data": armature.data,
            "matrix_world": armature.matrix_world.copy(),
        }
        for armature in referenced_armatures
    }
    return {
        "vertex_groups": [group.name for group in obj.vertex_groups],
        "assignments": assignments,
        "armature_objects": tuple(
            modifier.object for modifier in obj.modifiers
            if modifier.type == 'ARMATURE' and modifier.object
        ),
        "parent": obj.parent,
        "parent_inverse": obj.matrix_parent_inverse.copy(),
        "world_matrix": obj.matrix_world.copy(),
        "source_id": obj.get(RECONSTRUCTION_SOURCE_ID_PROPERTY),
        "metadata": metadata,
        "armature_states": armature_states,
    }


def _restore_reconstruction_state(obj, snapshot):
    active = bpy.context.view_layer.objects.active
    if active and active.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for group in list(obj.vertex_groups):
        obj.vertex_groups.remove(group)
    groups = [obj.vertex_groups.new(name=name) for name in snapshot["vertex_groups"]]
    for vertex_index, assignments in enumerate(snapshot["assignments"]):
        for group_index, weight in assignments:
            if 0 <= group_index < len(groups):
                groups[group_index].add([vertex_index], weight, 'REPLACE')

    original_armatures = set(snapshot["armature_objects"])
    for modifier in list(obj.modifiers):
        if modifier.type == 'ARMATURE' and modifier.object not in original_armatures:
            obj.modifiers.remove(modifier)

    for armature, state in snapshot.get("armature_states", {}).items():
        if armature and armature.name in bpy.data.objects:
            io_utils.rollback_pending_armature_update(
                armature,
                original_data=state.get("data"),
                original_matrix=state.get("matrix_world"),
            )

    obj.parent = snapshot["parent"]
    obj.matrix_parent_inverse = snapshot["parent_inverse"]
    obj.matrix_world = snapshot["world_matrix"]
    source_id = snapshot["source_id"]
    if source_id is None:
        if RECONSTRUCTION_SOURCE_ID_PROPERTY in obj:
            del obj[RECONSTRUCTION_SOURCE_ID_PROPERTY]
    else:
        obj[RECONSTRUCTION_SOURCE_ID_PROPERTY] = source_id

    for armature, properties in snapshot["metadata"].items():
        if not armature or armature.name not in bpy.data.objects:
            continue
        for key in list(armature.keys()):
            if key.startswith("carnivores_reconstruct_") or key.startswith("carnivores_rig_"):
                del armature[key]
        for key, value in properties.items():
            armature[key] = value


def _discard_new_reconstruction_objects(obj, existing_object_names):
    for armature in list(bpy.data.objects):
        if (
            armature.name not in existing_object_names
            and armature.type == 'ARMATURE'
        ):
            io_utils._remove_armature_object(armature)


def _store_reconstruction_owner_mapping(arm_obj, raw_by_compact):
    raw_by_compact = np.asarray(raw_by_compact, dtype=np.int32).reshape(-1)
    arm_obj["carnivores_reconstruct_owner_mapping"] = json.dumps(
        {
            "raw_by_compact": [int(raw_id) for raw_id in raw_by_compact],
            "compact_by_raw": {
                str(int(raw_id)): int(compact_id)
                for compact_id, raw_id in enumerate(raw_by_compact)
            },
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _store_reconstruction_name_map(
    arm_obj, raw_by_compact, compact_ids, source_names, blender_names, export_names
):
    arm_obj["carnivores_reconstruct_bone_name_map"] = json.dumps(
        [
            {
                "compact_id": int(compact_id),
                "raw_owner_id": int(raw_by_compact[compact_id]),
                "source_name": str(source_name),
                "blender_name": str(blender_name),
                "export_name": str(export_name),
            }
            for compact_id, source_name, blender_name, export_name in zip(
                compact_ids, source_names, blender_names, export_names
            )
        ],
        separators=(",", ":"),
        sort_keys=True,
    )


def _compute_reconstruction_body_axis(centroids):
    positions = np.asarray(centroids, dtype=np.float64)
    if positions.shape[0] < 2:
        return np.array((0.0, 1.0, 0.0), dtype=np.float64)

    centered = positions - np.mean(positions, axis=0)
    if not np.any(centered):
        return np.array((0.0, 1.0, 0.0), dtype=np.float64)

    try:
        cov = np.cov(centered, rowvar=False)
        eigvals, eigvecs = np.linalg.eigh(cov)
        axis = eigvecs[:, int(np.argmax(eigvals))]
        norm = np.linalg.norm(axis)
        if norm > 1e-8:
            return axis / norm
    except Exception:
        pass

    return np.array((0.0, 1.0, 0.0), dtype=np.float64)


def _score_reconstruction_edge(parent_idx, child_idx, positions, center_x, x_margin, center_distances, body_axis, group_weights=None):
    p_pos = positions[parent_idx]
    c_pos = positions[child_idx]
    edge = c_pos - p_pos
    dist = float(np.linalg.norm(edge))
    if dist <= 1e-8:
        return float('inf')

    score = dist

    # Avoid cross-body links unless they are clearly the best option.
    if ((p_pos[0] > center_x + x_margin and c_pos[0] < center_x - x_margin) or
            (p_pos[0] < center_x - x_margin and c_pos[0] > center_x + x_margin)):
        score *= 50.0

    # Prefer parents that are more central than their children.
    parent_radius = float(center_distances[parent_idx])
    child_radius = float(center_distances[child_idx])
    if parent_radius > child_radius + 1e-6:
        score += (parent_radius - child_radius) * 0.75
    else:
        score -= min(child_radius - parent_radius, dist) * 0.10

    # Slightly prefer links that follow the main body axis.
    axis_len = float(np.linalg.norm(body_axis))
    if axis_len > 1e-8:
        alignment = abs(float(np.dot(edge / dist, body_axis / axis_len)))
        score *= (1.0 - (alignment * 0.10))

    # Small deterministic bias for denser groups as parents.
    if group_weights is not None:
        weights = np.asarray(group_weights, dtype=np.float64)
        if weights.size > max(parent_idx, child_idx):
            max_weight = float(np.max(weights)) if np.any(weights > 0) else 0.0
            if max_weight > 0.0:
                score *= (1.0 - (weights[parent_idx] / max_weight) * 0.05)
                score *= (1.0 + (weights[child_idx] / max_weight) * 0.02)

    return score


def _get_reconstruction_group_count(obj, owner_indices=None):
    group_count = max((vg.index for vg in obj.vertex_groups), default=-1) + 1 if obj and obj.vertex_groups else 0
    if owner_indices is not None:
        valid_owners = owner_indices[owner_indices >= 0]
        if valid_owners.size > 0:
            group_count = max(group_count, int(valid_owners.max()) + 1)
    return group_count


def _get_vertex_group_names_by_index(obj, group_count):
    bone_names = [f"Bone_{i}" for i in range(group_count)]
    if obj and obj.vertex_groups:
        for vg in obj.vertex_groups:
            if 0 <= vg.index < group_count:
                bone_names[vg.index] = vg.name
    return bone_names


def _get_reconstruction_owner_indices(obj):
    mesh = obj.data
    attr = mesh.attributes.get(OWNER_ATTR_NAME)
    if not attr:
        return None
    if len(attr.data) != len(mesh.vertices):
        warn(
            f"Owner attribute '{OWNER_ATTR_NAME}' on '{obj.name}' has an unexpected length; ignoring cached owners."
        )
        return None

    owner_indices = np.empty(len(mesh.vertices), dtype=np.int32)
    attr.data.foreach_get("value", owner_indices)
    return owner_indices


def extract_rig_mesh_input(obj, owner_indices=None, owner_source=None):
    """Extract mesh-local reconstruction arrays for the pure analysis core."""
    if not obj or obj.type != 'MESH':
        raise ValueError("Rig analysis requires a mesh object.")

    mesh = obj.data
    vertex_count = len(mesh.vertices)
    vertices = np.empty(vertex_count * 3, dtype=np.float64)
    mesh.vertices.foreach_get('co', vertices)
    vertices = vertices.reshape((-1, 3))

    if owner_indices is None:
        owner_indices = np.full(vertex_count, -1, dtype=np.int32)
        for vertex in mesh.vertices:
            if vertex.groups:
                owner_indices[vertex.index] = max(
                    vertex.groups, key=lambda assignment: assignment.weight
                ).group
    else:
        owner_indices = np.asarray(owner_indices, dtype=np.int32).reshape(-1)

    group_count = _get_reconstruction_group_count(obj, owner_indices)
    raw_ids = raw_ids_from_metadata(mesh.get(OWNER_MAPPING_PROPERTY))
    if raw_ids is None or raw_ids.size != group_count:
        if owner_source is not None:
            source_ids = np.unique(np.asarray(owner_source, dtype=np.int32))
            source_ids = source_ids[source_ids >= 0]
            raw_ids = source_ids if source_ids.size == group_count else None
        if raw_ids is None:
            raw_ids = np.arange(group_count, dtype=np.int32)

    group_names = _build_reconstruction_bone_names(
        obj, group_count, owner_source=owner_source
    )

    mesh.calc_loop_triangles()
    triangles = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)
    if triangles.size:
        mesh.loop_triangles.foreach_get('vertices', triangles)
    triangles = triangles.reshape((-1, 3))

    edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    if edges.size:
        mesh.edges.foreach_get('vertices', edges)
    edges = edges.reshape((-1, 2))

    return build_mesh_analysis_input(
        vertices,
        owner_indices,
        raw_ids,
        triangles=triangles,
        edges=edges if edges.size else None,
        group_names=group_names,
    )


def analyze_reconstruction_geometry(obj):
    """Run Phase 2 geometry analysis without changing the Blender scene."""
    owners = _get_reconstruction_owner_indices(obj)
    source = _get_reconstruction_owner_source(obj)
    mesh_input = extract_rig_mesh_input(obj, owners, source)
    return analyze_rig_geometry(mesh_input)


def _topology_proposal_settings(obj, root_override_idx=-1):
    """Capture every object setting that changes a topology application."""
    return {
        "disconnected_policy": str(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_component_policy", "MULTI_ROOT"
        )).upper(),
        "root_override": int(root_override_idx),
        "side_axis": str(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_side_axis", "X"
        )).upper(),
        "side_inverted": bool(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_side_inverted", False
        )),
        "semantic_naming": bool(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_semantic_naming", True
        )),
        "smooth_weights": bool(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_smooth_weights", False
        )),
        "smooth_iterations": int(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_smooth_iterations", 3
        )),
        "smooth_factor": float(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_smooth_factor", 0.5
        )),
        "smooth_joints_only": bool(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_smooth_joints_only", True
        )),
    }


def topology_proposal_settings_match(obj, proposal):
    """Return whether current apply-affecting settings match a proposal."""
    if proposal is None:
        return False
    stored = proposal.settings
    current = _topology_proposal_settings(
        obj,
        root_override_idx=int(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_root_override", -1
        )),
    )
    keys = (
        "disconnected_policy",
        "root_override",
        "side_axis",
        "side_inverted",
        "semantic_naming",
        "smooth_weights",
        "smooth_iterations",
        "smooth_factor",
        "smooth_joints_only",
    )
    for key in keys:
        if key not in stored:
            return False
        if key == "smooth_factor":
            if not np.isclose(float(stored[key]), float(current[key])):
                return False
        elif stored[key] != current[key]:
            return False
    return True


def _remove_stored_proposal_text(mesh):
    """Remove an owned proposal text datablock referenced by a mesh."""
    text_name = mesh.get("carnivores_rig_proposal_text", "")
    text = bpy.data.texts.get(str(text_name)) if text_name else None
    if text and bool(text.get("carnivores_rig_proposal_text", False)):
        bpy.data.texts.remove(text)


def _proposal_metadata_from_mesh(mesh):
    """Read proposal JSON from the preferred Text datablock or old ID property."""
    text_name = mesh.get("carnivores_rig_proposal_text", "")
    if text_name:
        text = bpy.data.texts.get(str(text_name))
        if text is None:
            raise ValueError("Stored rig proposal text datablock is missing.")
        return text.as_string()

    metadata = mesh.get("carnivores_rig_proposal", "")
    if not metadata:
        return ""
    try:
        envelope = json.loads(metadata) if isinstance(metadata, str) else metadata
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid stored rig proposal metadata: {exc}") from exc
    if isinstance(envelope, dict) and envelope.get("storage") == "TEXT":
        text_name = envelope.get("text_name", "")
        text = bpy.data.texts.get(str(text_name)) if text_name else None
        if text is None:
            raise ValueError("Stored rig proposal text datablock is missing.")
        return text.as_string()
    # Phase 6 initially stored the complete JSON directly on the mesh. Keep
    # reading that representation for existing .blend files.
    return metadata


def analyze_topology_proposal(obj, root_override_idx=-1, forced_edges=(), rejected_edges=()):
    """Analyze topology reconstruction without changing Blender scene state."""
    if not obj or obj.type != 'MESH':
        raise ValueError("Topology proposal analysis requires a mesh object.")
    owner_indices = _get_reconstruction_owner_indices(obj)
    owner_source = _get_reconstruction_owner_source(obj)
    if owner_indices is None and not obj.vertex_groups:
        raise ValueError("Object has no vertex groups or owner cache to analyze.")
    mesh_input = extract_rig_mesh_input(obj, owner_indices, owner_source)
    analysis = analyze_rig_geometry(mesh_input)
    settings = _topology_proposal_settings(obj, root_override_idx)
    proposal = build_topology_rig_proposal(
        analysis,
        disconnected_policy=settings["disconnected_policy"],
        root_override=settings["root_override"],
        side_axis=settings["side_axis"],
        side_inverted=settings["side_inverted"],
        forced_edges=forced_edges,
        rejected_edges=rejected_edges,
    )
    # The pure core owns geometry settings; the adapter records the Blender
    # application settings as well so Apply cannot silently drift from Analyze.
    proposal = replace(
        proposal,
        settings={**proposal.settings, **settings},
    )
    return proposal, mesh_analysis_checksum(mesh_input)


def _topology_proposal_payload_hash(metadata):
    """Return the SHA-256 digest of the exact serialized proposal payload."""
    return hashlib.sha256(str(metadata).encode("utf-8")).hexdigest()


def store_topology_proposal(obj, proposal, checksum, source_id=None):
    """Persist the latest topology proposal in an owned Text datablock."""
    if not obj or obj.type != 'MESH':
        raise ValueError("Topology proposal requires a mesh object.")
    mesh = obj.data
    source_id = str(source_id or _ensure_reconstruction_source_id(obj, create=True))
    if obj.get(RECONSTRUCTION_SOURCE_ID_PROPERTY) != source_id:
        obj[RECONSTRUCTION_SOURCE_ID_PROPERTY] = source_id
    # Membership arrays are already represented by the current mesh and are
    # not needed to apply the stored geometry; omit them from persisted text.
    metadata = rig_proposal_to_metadata(proposal, compact=True)
    payload_hash = _topology_proposal_payload_hash(metadata)
    _remove_stored_proposal_text(mesh)
    text_name = f"Carnivores_Rig_Proposal_{source_id}"
    text = bpy.data.texts.get(text_name)
    if text is not None and not bool(text.get("carnivores_rig_proposal_text", False)):
        text = None
    if text is None:
        text = bpy.data.texts.new(text_name)
    text.clear()
    text.write(metadata)
    text["carnivores_rig_proposal_text"] = True
    text["carnivores_rig_proposal_source_id"] = source_id
    text["carnivores_rig_proposal_source_mesh"] = obj.name
    text[TOPOLOGY_PROPOSAL_PAYLOAD_HASH_PROPERTY] = payload_hash
    mesh["carnivores_rig_proposal_text"] = text.name
    # Keep a small envelope for polling and backwards-compatible discovery;
    # the potentially large proposal itself is no longer stored on the mesh.
    mesh["carnivores_rig_proposal"] = json.dumps(
        {
            "payload_hash": payload_hash,
            "storage": "TEXT",
            "text_name": text.name,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    mesh[TOPOLOGY_PROPOSAL_PAYLOAD_HASH_PROPERTY] = payload_hash
    mesh["carnivores_rig_proposal_checksum"] = str(checksum)
    mesh["carnivores_rig_proposal_source_id"] = source_id
    mesh["carnivores_rig_proposal_status"] = "ANALYZED"
    return mesh["carnivores_rig_proposal"]


def load_topology_proposal(obj):
    """Load the stored proposal and reject stale or malformed source data."""
    if not obj or obj.type != 'MESH':
        raise ValueError("Topology proposal requires a mesh object.")
    metadata = _proposal_metadata_from_mesh(obj.data)
    if not metadata:
        raise ValueError("No analyzed topology proposal is stored on this mesh.")
    stored_payload_hash = str(
        obj.data.get(TOPOLOGY_PROPOSAL_PAYLOAD_HASH_PROPERTY, "")
    )
    text_name = str(obj.data.get("carnivores_rig_proposal_text", ""))
    if not text_name:
        try:
            envelope = json.loads(obj.data.get("carnivores_rig_proposal", ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            envelope = {}
        if isinstance(envelope, dict) and envelope.get("storage") == "TEXT":
            text_name = str(envelope.get("text_name", ""))
    if text_name:
        if not stored_payload_hash:
            raise ValueError("Stored rig proposal payload hash is missing; analyze again.")
        text = bpy.data.texts.get(text_name)
        if text is None or not bool(text.get("carnivores_rig_proposal_text", False)):
            raise ValueError("Stored rig proposal Text datablock is not owned by CarnivoresIO.")
        text_payload_hash = str(text.get(TOPOLOGY_PROPOSAL_PAYLOAD_HASH_PROPERTY, ""))
        if text_payload_hash != stored_payload_hash:
            raise ValueError("Stored rig proposal Text hash metadata is inconsistent.")
        actual_payload_hash = _topology_proposal_payload_hash(metadata)
        if actual_payload_hash != stored_payload_hash:
            raise ValueError("Stored rig proposal payload was modified; analyze again.")
    elif stored_payload_hash and _topology_proposal_payload_hash(metadata) != stored_payload_hash:
        raise ValueError("Stored rig proposal payload was modified; analyze again.")
    try:
        proposal = rig_proposal_from_metadata(metadata)
    except ValueError as exc:
        raise ValueError(str(exc)) from exc
    owners = _get_reconstruction_owner_indices(obj)
    source = _get_reconstruction_owner_source(obj)
    mesh_input = extract_rig_mesh_input(obj, owners, source)
    current_checksum = mesh_analysis_checksum(mesh_input)
    stored_checksum = str(obj.data.get("carnivores_rig_proposal_checksum", ""))
    if not stored_checksum or stored_checksum != current_checksum:
        raise ValueError("The analyzed topology proposal is stale; analyze the mesh again.")
    stored_source_id = str(obj.data.get("carnivores_rig_proposal_source_id", ""))
    current_source_id = _ensure_reconstruction_source_id(obj, create=False)
    if stored_source_id and stored_source_id != current_source_id:
        raise ValueError("The analyzed topology proposal belongs to a different source mesh.")
    return proposal, current_checksum


def clear_topology_proposal(obj):
    """Remove only stored proposal metadata and its owned Text datablock."""
    if not obj or obj.type != 'MESH':
        return
    _remove_stored_proposal_text(obj.data)
    for key in (
        "carnivores_rig_proposal",
        "carnivores_rig_proposal_text",
        "carnivores_rig_proposal_checksum",
        "carnivores_rig_proposal_source_id",
        TOPOLOGY_PROPOSAL_PAYLOAD_HASH_PROPERTY,
        "carnivores_rig_proposal_status",
        "carnivores_rig_preview_collection",
    ):
        if key in obj.data:
            del obj.data[key]


def _preview_color(kind):
    return {
        "CENTER": (0.08, 0.6, 1.0, 1.0),
        "COMPONENT": (0.7, 0.7, 0.7, 1.0),
        "ROOT": (0.1, 1.0, 0.2, 1.0),
        "SKIPPED": (1.0, 0.0, 1.0, 1.0),
        "ACCEPTED": (0.2, 0.8, 0.25, 1.0),
        "LOW_CONFIDENCE": (1.0, 0.65, 0.05, 1.0),
        "FORCED": (0.0, 0.9, 1.0, 1.0),
        "REJECTED": (0.9, 0.1, 0.1, 1.0),
    }[kind]


def _preview_world_position(obj, position):
    return obj.matrix_world @ Vector(tuple(float(value) for value in position))


def _new_preview_empty(collection, name, position, kind, size=0.05):
    marker = bpy.data.objects.new(name, None)
    marker.empty_display_type = 'SPHERE'
    marker.empty_display_size = size
    marker.show_in_front = True
    marker.location = position
    marker.color = _preview_color(kind)
    marker["carnivores_rig_preview"] = True
    marker["carnivores_rig_preview_kind"] = kind
    collection.objects.link(marker)
    return marker


def _new_preview_curve(collection, name, first, second, kind):
    curve = bpy.data.curves.new(name, type='CURVE')
    curve.dimensions = '3D'
    curve.bevel_depth = 0.008
    curve.bevel_resolution = 1
    spline = curve.splines.new('POLY')
    spline.points.add(1)
    spline.points[0].co = (*first, 1.0)
    spline.points[1].co = (*second, 1.0)
    obj = bpy.data.objects.new(name, curve)
    obj.color = _preview_color(kind)
    obj["carnivores_rig_preview"] = True
    obj["carnivores_rig_preview_kind"] = kind
    collection.objects.link(obj)
    return obj


def _preview_source_matches(item, obj, source_id):
    if obj is None:
        return True
    stored_id = item.get("carnivores_rig_preview_source_id")
    if source_id and stored_id:
        return str(stored_id) == str(source_id)
    # Compatibility with previews created before stable source IDs were added.
    return item.get("carnivores_rig_preview_source") == obj.name


def clear_topology_preview(obj=None):
    """Remove only tagged topology preview collections and objects."""
    source_id = (
        _ensure_reconstruction_source_id(obj, create=False)
        if obj is not None else None
    )
    tagged_objects = [
        item for item in bpy.data.objects
        if bool(item.get("carnivores_rig_preview", False))
        and _preview_source_matches(item, obj, source_id)
    ]
    for item in tagged_objects:
        if item.name in bpy.data.objects:
            data = item.data if item.type == 'CURVE' else None
            bpy.data.objects.remove(item, do_unlink=True)
            if data is not None and data.name in bpy.data.curves and data.users == 0:
                bpy.data.curves.remove(data)

    tagged_collections = [
        collection for collection in bpy.data.collections
        if bool(collection.get("carnivores_rig_preview", False))
        and _preview_source_matches(collection, obj, source_id)
    ]
    for collection in tagged_collections:
        if collection.name in bpy.data.collections:
            # Remove children through the owned collection as a second line of
            # defense if preview creation failed before tagging an object with
            # the source ID.
            for item in list(collection.objects):
                if item.name in bpy.data.objects:
                    data = item.data if item.type == 'CURVE' else None
                    bpy.data.objects.remove(item, do_unlink=True)
                    if data is not None and data.name in bpy.data.curves and data.users == 0:
                        bpy.data.curves.remove(data)
            bpy.data.collections.remove(collection, do_unlink=True)
    if obj and obj.type == 'MESH' and "carnivores_rig_preview_collection" in obj.data:
        del obj.data["carnivores_rig_preview_collection"]


def create_topology_preview(obj, proposal):
    """Create a tagged, non-selecting scene preview for a topology proposal."""
    if not obj or obj.type != 'MESH':
        raise ValueError("Topology preview requires a mesh object.")
    source_id = _ensure_reconstruction_source_id(obj, create=True)
    clear_topology_preview(obj)
    collection = bpy.data.collections.new(f"{obj.name}_RigProposalPreview")
    collection["carnivores_rig_preview"] = True
    collection["carnivores_rig_preview_source"] = obj.name
    collection["carnivores_rig_preview_source_id"] = source_id
    bpy.context.scene.collection.children.link(collection)

    group_by_id = {group.compact_id: group for group in proposal.groups}
    component_by_group = {}
    for component_index, component in enumerate(
        proposal.settings.get("component_sizes", [])
    ):
        for compact_id in component.get("compact_ids", []):
            component_by_group[int(compact_id)] = component_index
    for group in proposal.groups:
        marker = _new_preview_empty(
            collection,
            f"CIO_PREVIEW_CENTER_{group.compact_id}",
            _preview_world_position(obj, group.median_center),
            "SKIPPED" if group.compact_id in proposal.skipped_groups else "CENTER",
        )
        marker["carnivores_rig_preview_source"] = obj.name
        marker["carnivores_rig_preview_source_id"] = source_id
        marker["carnivores_rig_preview_component"] = component_by_group.get(
            int(group.compact_id), -1
        )
    for component_index, component in enumerate(
        proposal.settings.get("component_sizes", [])
    ):
        component_ids = [
            int(compact_id) for compact_id in component.get("compact_ids", [])
            if int(compact_id) in group_by_id
        ]
        if not component_ids:
            continue
        component_center = np.mean(
            [group_by_id[compact_id].median_center for compact_id in component_ids],
            axis=0,
        )
        component_marker = _new_preview_empty(
            collection,
            f"CIO_PREVIEW_COMPONENT_{component_index}",
            _preview_world_position(obj, component_center),
            "COMPONENT",
            size=0.12,
        )
        component_marker["carnivores_rig_preview_source"] = obj.name
        component_marker["carnivores_rig_preview_source_id"] = source_id
        component_marker["carnivores_rig_preview_component"] = component_index
        component_marker["carnivores_rig_preview_group_count"] = len(component_ids)

    for root_id in proposal.root_groups:
        root = _new_preview_empty(
            collection,
            f"CIO_PREVIEW_ROOT_{root_id}",
            _preview_world_position(obj, proposal.head_by_group[root_id]),
            "ROOT",
            size=0.08,
        )
        root["carnivores_rig_preview_source"] = obj.name
        root["carnivores_rig_preview_source_id"] = source_id
        root["carnivores_rig_preview_component"] = component_by_group.get(
            int(root_id), -1
        )

    forced = {
        tuple(edge) for edge in proposal.settings.get("forced_edges", [])
    }
    rejected = {
        tuple(edge) for edge in proposal.settings.get("rejected_edges", [])
    }
    accepted = {
        tuple(sorted(edge)) for edge in proposal.accepted_edges
    }
    for edge in proposal.edge_candidates:
        pair = tuple(sorted((edge.group_a, edge.group_b)))
        kind = "FORCED" if pair in forced else (
            "REJECTED" if pair in rejected or (
                pair not in accepted and edge.confidence >= 0.35
            ) else (
                "LOW_CONFIDENCE" if edge.confidence < 0.35 else "ACCEPTED"
            )
        )
        if pair not in accepted and kind == "ACCEPTED":
            kind = "LOW_CONFIDENCE"
        first = _preview_world_position(obj, edge.boundary_joint)
        second = _preview_world_position(
            obj,
            group_by_id[edge.group_b].median_center
            if edge.group_b in group_by_id else edge.boundary_joint,
        )
        curve = _new_preview_curve(
            collection,
            f"CIO_PREVIEW_EDGE_{edge.group_a}_{edge.group_b}",
            tuple(first),
            tuple(second),
            kind,
        )
        curve["carnivores_rig_preview_source"] = obj.name
        curve["carnivores_rig_preview_source_id"] = source_id
        curve["carnivores_rig_preview_groups"] = f"{edge.group_a},{edge.group_b}"

    obj.data["carnivores_rig_preview_collection"] = collection.name
    return collection


def validate_stored_topology_proposal(obj):
    """Validate proposal freshness, armature structure, and owner reconciliation."""
    proposal, checksum = load_topology_proposal(obj)
    result = {
        "valid": True,
        "applied": False,
        "checksum": checksum,
        "errors": [],
        "warnings": list(proposal.warnings),
        "reconciliation": {},
    }
    group_count = len(proposal.groups)
    skipped = set(proposal.skipped_groups)
    active_ids = [group.compact_id for group in proposal.groups if group.compact_id not in skipped]
    active_set = set(active_ids)
    if any(
        int(parent) >= 0 and int(parent) not in active_set
        for parent in proposal.parent_by_group
    ):
        result["errors"].append("Proposal hierarchy references a skipped or missing parent group.")
    if any(root not in active_set for root in proposal.root_groups):
        result["errors"].append("Proposal roots contain a skipped or missing group.")
    if any(int(proposal.parent_by_group[group_id]) == group_id for group_id in active_ids):
        result["errors"].append("Proposal hierarchy contains a self-parenting group.")

    armature = next(
        (
            modifier.object for modifier in obj.modifiers
            if modifier.type == 'ARMATURE' and modifier.object
        ),
        obj.parent if obj.parent and obj.parent.type == 'ARMATURE' else None,
    )
    if armature is None:
        result["warnings"].append("No armature is assigned; proposal has not been applied.")
        result["valid"] = not result["errors"]
        return result

    result["applied"] = True
    if armature.get("carnivores_rig_algorithm") != "TOPOLOGY":
        result["errors"].append("Assigned armature was not generated by topology reconstruction.")
    source_id = str(obj.get(RECONSTRUCTION_SOURCE_ID_PROPERTY, ""))
    armature_source_id = str(armature.get(RECONSTRUCTION_SOURCE_ID_PROPERTY, ""))
    if source_id and armature_source_id and source_id != armature_source_id:
        result["errors"].append("Assigned armature belongs to a different reconstruction source.")
    if not np.allclose(armature.matrix_world, obj.matrix_world, atol=1e-5):
        result["errors"].append("Armature world transform differs from the analyzed mesh transform.")
    if len(armature.data.bones) != len(active_ids):
        result["errors"].append(
            f"Armature has {len(armature.data.bones)} bones; proposal expects {len(active_ids)}."
        )

    bone_by_compact = {}
    name_entries = []
    try:
        name_entries = json.loads(
            armature.get("carnivores_reconstruct_bone_name_map", "[]")
        )
        if not isinstance(name_entries, list):
            raise ValueError("bone name map is not a list")
        for entry in name_entries:
            if isinstance(entry, dict) and entry.get("compact_id") is not None:
                compact_id = int(entry["compact_id"])
                bone = armature.data.bones.get(str(entry.get("blender_name", "")))
                if bone is not None:
                    bone_by_compact[compact_id] = bone
    except (TypeError, ValueError, AttributeError, json.JSONDecodeError):
        result["errors"].append("Generated armature bone-name metadata is invalid.")

    for compact_id in active_ids:
        bone = bone_by_compact.get(compact_id)
        if bone is None:
            result["errors"].append(f"No generated bone maps to proposal group {compact_id}.")
            continue
        if not np.allclose(bone.head_local, proposal.head_by_group[compact_id], atol=1e-5):
            result["errors"].append(f"Bone '{bone.name}' head differs from proposal group {compact_id}.")
        if not np.allclose(bone.tail_local, proposal.tail_by_group[compact_id], atol=1e-5):
            result["errors"].append(f"Bone '{bone.name}' tail differs from proposal group {compact_id}.")
        expected_parent = int(proposal.parent_by_group[compact_id])
        actual_parent = None
        if bone.parent is not None:
            actual_parent = next(
                (
                    parent_compact
                    for parent_compact, parent_bone in bone_by_compact.items()
                    if parent_bone == bone.parent
                ),
                None,
            )
        if actual_parent != (expected_parent if expected_parent >= 0 else None):
            result["errors"].append(f"Bone '{bone.name}' parent differs from proposal group {compact_id}.")

    owner_indices = _get_reconstruction_owner_indices(obj)
    if owner_indices is not None:
        raw_by_compact = raw_ids_from_metadata(obj.data.get(OWNER_MAPPING_PROPERTY))
        if raw_by_compact is None:
            owner_source = _get_reconstruction_owner_source(obj)
            source_ids = np.unique(owner_source[owner_source >= 0]) if owner_source is not None else np.array([], dtype=np.int32)
            raw_by_compact = source_ids if source_ids.size == int(np.max(owner_indices, initial=-1)) + 1 else np.arange(int(np.max(owner_indices, initial=-1)) + 1, dtype=np.int32)
        reconciliation = _build_rig_export_reconciliation(
            obj,
            armature,
            owner_indices,
            raw_by_compact,
            skipped_groups=skipped,
            proposal=proposal,
        )
        result["reconciliation"] = reconciliation.to_dict(include_vertex_owners=False)
        result["reconciliation"].update({
            "owned_vertices": reconciliation.counts["owned_vertices"],
            "unowned_vertices": reconciliation.counts["unowned_vertices"],
            "skipped_owner_vertices": reconciliation.counts["skipped_owner_vertices"],
            "dominant_matches": reconciliation.counts["dominant_matches"],
            "dominant_drift": reconciliation.counts["dominant_drift"],
            "missing_deform_assignments": reconciliation.counts["missing_deform_assignments"],
        })
        result["phase7_level"] = reconciliation.level
        result["phase7_valid"] = reconciliation.valid
        result["warnings"].extend(reconciliation.warnings)
        result["errors"].extend(reconciliation.errors)
    else:
        result["phase7_level"] = "ERROR"
        result["phase7_valid"] = False
        result["warnings"].append("No canonical owner attribute is available for reconciliation.")
        result["reconciliation"] = {
            "phase7_level": "ERROR",
            "counts": {},
        }

    result["valid"] = not result["errors"]
    return result


@timed('calculate_vertex_group_centroids')
def calculate_vertex_group_centroids(obj, owner_indices=None, group_count=None, return_weights=False):
    """
    Calculates centroids for reconstruction groups.
    If owner_indices is provided, uses imported raw owner data directly.
    Returns: list of either (x, y, z) tuples or None for degenerate (empty) groups.
    """
    mesh = obj.data
    v_count = len(mesh.vertices)
    if group_count is None:
        group_count = _get_reconstruction_group_count(obj, owner_indices)

    if group_count == 0 or v_count == 0:
        empty = []
        return (empty, np.zeros(0, dtype=np.float64)) if return_weights else empty

    v_pos = np.empty(v_count * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', v_pos)
    v_pos = v_pos.reshape((v_count, 3))

    centroids = np.zeros((group_count, 3), dtype=np.float64)
    weights_sum = np.zeros(group_count, dtype=np.float64)

    if owner_indices is not None:
        owners = np.asarray(owner_indices, dtype=np.int32).reshape(-1)
        if owners.size != v_count:
            warn(
                f"Owner cache size mismatch for '{obj.name}' (expected {v_count}, got {owners.size}); falling back to vertex groups."
            )
            return calculate_vertex_group_centroids(
                obj,
                owner_indices=None,
                group_count=group_count,
                return_weights=return_weights,
            )

        valid = owners >= 0
        if np.any(valid):
            np.add.at(centroids, owners[valid], v_pos[valid])
            np.add.at(weights_sum, owners[valid], 1.0)

        # Build result: None for empty/degenerate groups
        result = []
        for i in range(group_count):
            if weights_sum[i] > 0:
                result.append(tuple(centroids[i] / weights_sum[i]))
            else:
                result.append(None)

        return (result, weights_sum) if return_weights else result

    # Vertex groups path
    for v_idx, v in enumerate(mesh.vertices):
        for g in v.groups:
            g_idx = g.group
            w = g.weight
            if g_idx < group_count:
                centroids[g_idx] += v_pos[v_idx] * w
                weights_sum[g_idx] += w

    result = []
    for i in range(group_count):
        if weights_sum[i] > 0:
            result.append(tuple(centroids[i] / weights_sum[i]))
        else:
            result.append(None)

    return (result, weights_sum) if return_weights else result


def _build_legacy_joint_geometry(vertices, source_owners, valid_indices, parents, names):
    """Build scaled joint heads/tails for the compatibility reconstruction path."""
    vertices = np.asarray(vertices, dtype=np.float64)
    source_owners = np.asarray(source_owners, dtype=np.int32).reshape(-1)
    group_heads = []
    group_points = []
    for compact_id in valid_indices:
        points = vertices[source_owners == compact_id]
        group_points.append(points)
        group_heads.append(
            np.median(points, axis=0) if len(points) else np.zeros(3, dtype=np.float64)
        )
    heads = np.asarray(group_heads, dtype=np.float64)
    if len(heads) == 0:
        return heads, heads.copy()

    body_axis = _compute_reconstruction_body_axis(heads)
    body_axis /= max(float(np.linalg.norm(body_axis)), np.finfo(np.float64).eps)
    center_x = float(np.median(heads[:, 0]))
    center_scale = max(float(np.ptp(heads[:, 0])), np.finfo(np.float64).eps)
    owned = vertices[source_owners >= 0]
    model_extent = float(np.linalg.norm(np.ptp(owned, axis=0))) if len(owned) else 0.0
    pairwise = np.linalg.norm(heads[:, None, :] - heads[None, :, :], axis=2)
    pairwise = pairwise[np.triu_indices(len(heads), k=1)]
    pairwise = pairwise[pairwise > np.finfo(np.float64).eps]
    characteristic = float(np.median(pairwise)) if pairwise.size else max(model_extent, 1e-8)
    minimum = max(characteristic * 1e-4, 1e-8)

    children = {index: [] for index in range(len(valid_indices))}
    for child, parent in enumerate(parents):
        if parent >= 0:
            children[parent].append(child)

    tails = np.zeros_like(heads)
    for index, head in enumerate(heads):
        child_indices = children[index]
        if child_indices:
            def continuation_key(child):
                offset = heads[child] - head
                length = float(np.linalg.norm(offset))
                alignment = (
                    abs(float(np.dot(offset, body_axis)) / length)
                    if length > np.finfo(np.float64).eps else 0.0
                )
                centrality = 1.0 - min(
                    abs(float(heads[child, 0] - center_x)) / center_scale,
                    1.0,
                )
                semantic = 1.0 if any(
                    token in names[child].lower()
                    for token in ("root", "pelvis", "hip", "spine", "torso", "body", "neck", "head")
                ) else 0.0
                return (
                    -(alignment * 0.45 + centrality * 0.30 + semantic * 0.10
                      + min(length / max(characteristic, minimum), 1.0) * 0.15),
                    int(child),
                )

            continuation = min(child_indices, key=continuation_key)
            tails[index] = heads[continuation]
            continue

        points = group_points[index]
        fallback = heads[index] - heads[parents[index]] if parents[index] >= 0 else body_axis
        fallback_norm = float(np.linalg.norm(fallback))
        if fallback_norm <= np.finfo(np.float64).eps:
            fallback = body_axis.copy()
            fallback_norm = 1.0
        fallback /= fallback_norm
        direction = fallback
        if len(points) >= 2:
            centered = points - np.mean(points, axis=0)
            try:
                _, _, vh = np.linalg.svd(centered, full_matrices=False)
                candidate = np.asarray(vh[0], dtype=np.float64)
                candidate /= max(float(np.linalg.norm(candidate)), np.finfo(np.float64).eps)
                if np.dot(candidate, fallback) < 0.0:
                    candidate *= -1.0
                direction = candidate
            except np.linalg.LinAlgError:
                pass
        extent = float(np.ptp(points @ direction)) if len(points) else 0.0
        length = max(extent, characteristic * 0.05, minimum)
        tails[index] = heads[index] + direction * length

    # A root with children already points at its continuation child. A lone
    # root still receives a scale-aware prop/floor tail rather than a fixed
    # world-space length.
    return heads, tails


def _find_mirror_partners(centroids, center_x):
    """
    Identifies pairs of bones that are symmetric mirror partners around center_x.
    Uses adaptive tolerances based on the model's bounding box dimensions.
    Returns: List of booleans of length N, where True indicates the bone has a mirror partner.
    """
    positions = np.asarray(centroids, dtype=np.float64)
    n = positions.shape[0]
    has_mirror = [False] * n

    if n <= 1:
        return has_mirror

    # Calculate bounding box ranges for adaptive tolerances
    ptp = np.ptp(positions, axis=0)
    width_x = max(float(ptp[0]), 0.001)
    depth_y = max(float(ptp[1]), 0.001)
    height_z = max(float(ptp[2]), 0.001)

    # Tolerances scale with model dimensions: 12% of depth/height, 8% of width
    tol_x = max(width_x * 0.08, 0.05)
    tol_yz = max(max(depth_y, height_z) * 0.12, 0.08)

    rel_x = positions[:, 0] - center_x

    for i in range(n):
        if has_mirror[i]:
            continue

        # Midline bones (very close to center_x) are not mirror partners
        if abs(rel_x[i]) < max(width_x * 0.03, 0.01):
            continue

        p_i = positions[i]
        for j in range(n):
            if i == j:
                continue

            p_j = positions[j]
            # Opposite X check: rel_x[i] and rel_x[j] have opposite signs and sum close to 0
            x_match = abs(rel_x[i] + rel_x[j]) < tol_x and (rel_x[i] * rel_x[j] < 0)

            # Y and Z close check
            yz_match = abs(p_i[1] - p_j[1]) < tol_yz and abs(p_i[2] - p_j[2]) < tol_yz

            if x_match and yz_match:
                has_mirror[i] = True
                has_mirror[j] = True
                break

    return has_mirror


@timed('select_root_bone')
def select_root_bone(centroids, bone_names=None, group_weights=None, root_override_idx=-1, mirror_partners=None):
    """
    Selects the most likely root bone using geometric centrality, symmetry-plane alignment,
    name hints, and weight distribution.
    """
    num_bones = len(centroids)
    if num_bones == 0:
        return -1
    if num_bones == 1:
        return 0

    # Manual index override
    if 0 <= root_override_idx < num_bones:
        return root_override_idx

    # 1. Check for high-priority midline keywords in bone names
    if bone_names:
        priority_keywords = ["floor", "root", "pelvis", "hips", "spine"]
        for keyword in priority_keywords:
            for i, name in enumerate(bone_names):
                if name and keyword in name.lower():
                    # Exclude mirror-paired lateral bones even if name matches
                    if mirror_partners and mirror_partners[i]:
                        continue
                    # Active exclusion of lateral limb bones via standard naming suffixes
                    name_lower = name.lower()
                    if "_l" in name_lower or "_r" in name_lower or ".l" in name_lower or ".r" in name_lower:
                        continue
                    return i

    positions = np.asarray(centroids, dtype=np.float64)
    center = np.median(positions, axis=0)
    pairwise = np.linalg.norm(positions[:, None, :] - positions[None, :, :], axis=2)
    centrality = pairwise.sum(axis=1) / max(num_bones - 1, 1)
    center_dist = np.linalg.norm(positions - center, axis=1)
    
    # Base centrality score
    scores = centrality + (center_dist * 0.5)

    # 2. Symmetry-plane (X-Offset) Penalty (CRITICAL to prevent lateral leg bones from being root)
    center_x = float(center[0])
    width_x = float(np.ptp(positions[:, 0]))
    if width_x > 0.001:
        x_offsets = np.abs(positions[:, 0] - center_x)
        # Heavily penalize off-center bones
        scores += (x_offsets / width_x) * 5.0

    # 3. Weight/Vertex-density bias (symmetry-scaled so it only rewards midline bones)
    if group_weights is not None:
        weights = np.asarray(group_weights, dtype=np.float64)
        if weights.size == num_bones and np.any(weights > 0):
            x_offsets = np.abs(positions[:, 0] - center_x)
            weight_reward = (weights / weights.max()) * 0.25
            if width_x > 0.001:
                weight_reward *= np.exp(-x_offsets / width_x)
            scores -= weight_reward

    # 4. Blacklist mirror-partner bones when at least one finite candidate remains.
    unfiltered_scores = scores.copy()
    if mirror_partners:
        for i in range(num_bones):
            if mirror_partners[i]:
                scores[i] = float('inf')
        if not np.any(np.isfinite(scores)):
            warn("Every root candidate was mirror-paired; using the best unfiltered candidate.")
            scores = unfiltered_scores

    return int(np.argmin(scores))


@timed('infer_hierarchy_mst')
def infer_hierarchy_mst(centroids, bone_names=None, group_weights=None, root_override_idx=-1, mirror_partners=None):
    """
    Infers a parent-child hierarchy from centroids using a scored MST search.
    Symmetry-aware: penalizes cross-body links around the mesh's own X center.
    Root selection: chooses the most central group, with 'floor' as an override.
    """
    num_bones = len(centroids)
    if num_bones <= 1:
        return [-1] * num_bones

    parents = [-1] * num_bones
    connected = [False] * num_bones
    all_pos = np.asarray(centroids, dtype=np.float64)

    # 1. Identify Root
    root_idx = select_root_bone(centroids, bone_names=bone_names, group_weights=group_weights, root_override_idx=root_override_idx, mirror_partners=mirror_partners)
    if root_idx < 0:
        return parents

    connected[root_idx] = True

    # 2. Scored MST
    center_point = np.mean(all_pos, axis=0)
    center_x = float(center_point[0])
    x_margin = max(float(np.ptp(all_pos[:, 0])) * 0.05, 0.001)
    center_distances = np.linalg.norm(all_pos - center_point, axis=1)
    body_axis = _compute_reconstruction_body_axis(all_pos)

    for _ in range(num_bones - 1):
        min_score = float('inf')
        best_pair = (-1, -1)  # (parent, child)

        for i in range(num_bones):
            if not connected[i]:
                continue

            for j in range(num_bones):
                if connected[j]:
                    continue

                score = _score_reconstruction_edge(
                    i,
                    j,
                    all_pos,
                    center_x,
                    x_margin,
                    center_distances,
                    body_axis,
                    group_weights=group_weights,
                )

                if score < min_score:
                    min_score = score
                    best_pair = (i, j)

        if best_pair[1] != -1:
            parents[best_pair[1]] = best_pair[0]
            connected[best_pair[1]] = True

    return parents


def _detect_disconnected_clusters(centroids, threshold_factor=2.0):
    """
    Lightweight BFS-based spatial clustering on bone centroids.
    Returns {local_idx: cluster_id} so callers can isolate detached groups.
    """
    positions = np.asarray(centroids, dtype=np.float64)
    n = positions.shape[0]
    if n < 2:
        return {0: 0}
    if n == 2:
        # With only one distance there is no statistical evidence that either
        # group is detached. Preserve the only possible skeletal connection.
        return {0: 0, 1: 0}

    # Pairwise Euclidean
    diff = positions[:, None, :] - positions[None, :, :]
    dists = np.linalg.norm(diff, axis=2)
    np.fill_diagonal(dists, np.inf)

    finite = dists[np.isfinite(dists)]
    if finite.size == 0:
        return {i: 0 for i in range(n)}

    # Pairwise standard deviation collapses to zero for regular layouts and for
    # two-group rigs. Typical nearest-neighbor spacing is a more stable local
    # scale for this legacy fallback (topology mode will replace this heuristic).
    nearest_distances = np.min(dists, axis=1)
    typical_spacing = float(np.median(nearest_distances[np.isfinite(nearest_distances)]))
    threshold = max(typical_spacing * threshold_factor, 1e-6)

    labels = -np.ones(n, dtype=np.int32)
    cluster_id = 0

    for i in range(n):
        if labels[i] != -1:
            continue
        queue = [i]
        labels[i] = cluster_id
        idx = 0
        while idx < len(queue):
            current = queue[idx]
            idx += 1
            neighbors = np.where(dists[current] <= threshold)[0]
            for nb in neighbors:
                if labels[nb] == -1:
                    labels[nb] = cluster_id
                    queue.append(nb)
        cluster_id += 1

    return {i: int(labels[i]) for i in range(n)}


def _apply_semantic_suffixes(
    obj, bone_names, centroids, center_x, side_axis=0, side_inverted=False
):
    """Append L/R suffixes using the selected bilateral axis.

    ``center_x`` is retained as the positional argument used by Legacy mode;
    for a non-X topology axis it is simply the center coordinate on that axis.
    """
    positions = np.asarray(centroids, dtype=np.float64)
    n = len(bone_names)
    if n == 0:
        return bone_names
    try:
        side_axis = int(side_axis)
    except (TypeError, ValueError):
        side_axis = {"X": 0, "Y": 1, "Z": 2}.get(str(side_axis).upper(), 0)
    if side_axis not in (0, 1, 2):
        side_axis = 0

    side_span = float(np.ptp(positions[:, side_axis])) if n > 1 else 0.0
    side_margin = max(side_span * 0.03, 0.01)
    relative = positions[:, side_axis] - float(center_x)
    if side_inverted:
        relative *= -1.0

    new_names = list(bone_names)
    renamed_any = False

    for i in range(n):
        name = bone_names[i]
        # Semantic suffixes are reserved for clearly synthetic names. User
        # and source names must remain unchanged unless explicitly renamed.
        name_lower = name.lower()
        synthetic_name = bool(re.match(r"^(?:car)?bone(?:[_ .-]?\d+)?$", name_lower))
        if not synthetic_name:
            continue
        if any(name_lower.endswith(s) for s in ["_l", "_r", ".l", ".r"]):
            continue
        if any(s in name_lower for s in [" left", " right"]):
            continue

        if relative[i] > side_margin:
            new_names[i] = f"{name}_L"
            renamed_any = True
        elif relative[i] < -side_margin:
            new_names[i] = f"{name}_R"
            renamed_any = True

    # Vertex groups are renamed later by compact owner ID, after final ASCII
    # cleaning and collision resolution. String lookup here can bind weights to
    # the wrong group when two source names clean to the same export name.
    return new_names


def _reconstruct_armature_topology_impl(obj, root_override_idx=-1, proposal=None):
    """Apply a topology proposal while preserving compact/raw owner identities."""
    source_id = _ensure_reconstruction_source_id(obj, create=False)
    creation_policy = _resolve_reconstruction_policy(obj)
    existing_rig, lifecycle_ok = _validate_reconstruction_lifecycle(
        obj, creation_policy, source_id
    )
    if not lifecycle_ok:
        return None
    obj[RECONSTRUCTION_SOURCE_ID_PROPERTY] = source_id

    owner_indices = _get_reconstruction_owner_indices(obj)
    owner_source = _get_reconstruction_owner_source(obj)
    if owner_indices is None and not obj.vertex_groups:
        error("Object has no vertex groups or owner cache to reconstruct from")
        return None

    analysis = analyze_rig_geometry(
        extract_rig_mesh_input(obj, owner_indices, owner_source)
    )
    if proposal is None:
        settings = _topology_proposal_settings(obj, root_override_idx)
        proposal = build_topology_rig_proposal(
            analysis,
            disconnected_policy=settings["disconnected_policy"],
            root_override=settings["root_override"],
            side_axis=settings["side_axis"],
            side_inverted=settings["side_inverted"],
        )
    policy = str(proposal.settings.get(
        "disconnected_policy",
        _get_reconstruction_setting(obj, "carnivores_reconstruct_component_policy", "MULTI_ROOT"),
    )).upper()
    for message in proposal.warnings:
        warn(f"Topology proposal: {message}")

    skipped = set(proposal.skipped_groups)
    active_ids = [
        group.compact_id for group in proposal.groups
        if group.compact_id not in skipped
    ]
    if not active_ids:
        error("Topology analysis produced no reconstructable owner groups")
        return None

    local_by_compact = {
        compact_id: local_id for local_id, compact_id in enumerate(active_ids)
    }
    group_by_id = {group.compact_id: group for group in proposal.groups}
    bone_names = [group_by_id[compact_id].name for compact_id in active_ids]
    source_bone_names = list(bone_names)

    # Topology always analyzes canonical imported owners, but its generated
    # deform groups may be rebuilt and smoothed non-cumulatively on request.
    proposal_settings = proposal.settings if proposal is not None else {}
    smooth_enabled = bool(proposal_settings.get(
        "smooth_weights",
        _get_reconstruction_setting(obj, "carnivores_reconstruct_smooth_weights", False),
    ))
    if smooth_enabled:
        for vertex_group in list(obj.vertex_groups):
            obj.vertex_groups.remove(vertex_group)
        canonical_names = _build_reconstruction_bone_names(
            obj, analysis.mesh.raw_by_compact.size, owner_source=owner_source
        )
        io_utils.create_vertex_groups_from_bones(obj, canonical_names, analysis.mesh.compact_owners)
    elif not obj.vertex_groups or len(obj.vertex_groups) < analysis.mesh.raw_by_compact.size:
        _ensure_reconstruction_vertex_groups(
            obj,
            analysis.mesh.raw_by_compact.size,
            analysis.mesh.compact_owners,
            owner_source=owner_source,
        )

    semantic_enabled = bool(proposal_settings.get(
        "semantic_naming",
        _get_reconstruction_setting(obj, "carnivores_reconstruct_semantic_naming", True),
    ))
    side_axis = proposal_settings.get(
        "side_axis",
        _get_reconstruction_setting(obj, "carnivores_reconstruct_side_axis", "X"),
    )
    side_axis_index = {"X": 0, "Y": 1, "Z": 2}.get(str(side_axis).upper(), 0)
    side_inverted = bool(proposal_settings.get(
        "side_inverted",
        _get_reconstruction_setting(obj, "carnivores_reconstruct_side_inverted", False),
    ))
    if semantic_enabled:
        active_centroids = [group_by_id[compact_id].centroid for compact_id in active_ids]
        center_side = float(np.median(
            analysis.mesh.vertices[analysis.mesh.compact_owners >= 0, side_axis_index]
        )) if np.any(analysis.mesh.compact_owners >= 0) else 0.0
        bone_names = _apply_semantic_suffixes(
            obj,
            bone_names,
            active_centroids,
            center_side,
            side_axis=side_axis_index,
            side_inverted=side_inverted,
        )

    if smooth_enabled:
        io_utils.smooth_vertex_weights(
            obj,
            iterations=int(proposal_settings.get(
                "smooth_iterations",
                _get_reconstruction_setting(obj, "carnivores_reconstruct_smooth_iterations", 3),
            )),
            factor=float(proposal_settings.get(
                "smooth_factor",
                _get_reconstruction_setting(obj, "carnivores_reconstruct_smooth_factor", 0.5),
            )),
            joints_only=bool(proposal_settings.get(
                "smooth_joints_only",
                _get_reconstruction_setting(obj, "carnivores_reconstruct_smooth_joints_only", True),
            )),
        )

    bone_heads = [proposal.head_by_group[compact_id] for compact_id in active_ids]
    bone_tails = [proposal.tail_by_group[compact_id] for compact_id in active_ids]
    roll_references = [proposal.roll_reference_by_group[compact_id] for compact_id in active_ids]
    parents = []
    for compact_id in active_ids:
        parent_compact = int(proposal.parent_by_group[compact_id])
        parents.append(local_by_compact.get(parent_compact, -1))

    # Name safety: reserve skipped/non-deform group names so final deform
    # groups remain unique across the entire mesh.
    active_set = set(active_ids)
    reserved_group_names = [
        vertex_group.name for vertex_group in obj.vertex_groups
        if vertex_group.index not in active_set
    ]
    blender_bone_names, export_bone_names = _resolve_reconstruction_name_pair(
        bone_names, reserved=reserved_group_names
    )

    vertex_count = len(obj.data.vertices)
    positions = np.empty(vertex_count * 3, dtype=np.float32)
    obj.data.vertices.foreach_get('co', positions)
    positions = positions.reshape((-1, 3))
    if owner_indices is None:
        source_owners = analysis.mesh.compact_owners
    else:
        source_owners = np.asarray(owner_indices, dtype=np.int32)
    local_owners = np.full(vertex_count, -1, dtype=np.int32)
    for compact_id, local_id in local_by_compact.items():
        local_owners[source_owners == compact_id] = local_id

    arm_obj = io_utils.create_armature(
        blender_bone_names,
        bone_heads,
        parents,
        obj.name,
        obj.users_collection[0] if obj.users_collection else None,
        verticesTransformedPos=positions,
        vertex_owners=local_owners,
        explicit_tail_positions=bone_tails,
        roll_reference_vectors=roll_references,
        creation_policy=creation_policy,
        existing_armature=existing_rig,
        world_matrix=obj.matrix_world,
    )
    if arm_obj is None:
        error("Topology armature construction failed")
        return None

    # Rename by compact owner identity so cleaned-name collisions cannot move
    # weights between bones.
    _rename_vertex_groups_by_compact_id(obj, active_ids, blender_bone_names)
    _clear_reconstruction_metadata(arm_obj)

    raw_by_compact = analysis.mesh.raw_by_compact
    parent_map_raw = {}
    for compact_id in active_ids:
        raw_id = int(raw_by_compact[compact_id])
        parent_compact = int(proposal.parent_by_group[compact_id])
        parent_map_raw[str(raw_id)] = (
            int(raw_by_compact[parent_compact]) if parent_compact >= 0 else -1
        )
    arm_obj["carnivores_rig_algorithm"] = "TOPOLOGY"
    arm_obj["carnivores_rig_algorithm_version"] = proposal.algorithm_version
    arm_obj[RECONSTRUCTION_SOURCE_ID_PROPERTY] = source_id
    arm_obj["carnivores_reconstruct_source_mesh"] = obj.name
    arm_obj["carnivores_rig_metadata_version"] = 1
    arm_obj["carnivores_reconstruct_parent_map"] = json.dumps(
        parent_map_raw, separators=(",", ":"), sort_keys=True
    )
    root_raw_ids = [int(raw_by_compact[root]) for root in proposal.root_groups]
    arm_obj["carnivores_reconstruct_roots"] = json.dumps(root_raw_ids)
    if proposal.root_groups:
        first_root = proposal.root_groups[0]
        root_local = local_by_compact.get(first_root)
        arm_obj["carnivores_reconstruct_root"] = (
            blender_bone_names[root_local] if root_local is not None else group_by_id[first_root].name
        )
        arm_obj["carnivores_reconstruct_root_idx"] = int(first_root)
        arm_obj["carnivores_reconstruct_root_raw_id"] = int(raw_by_compact[first_root])
    arm_obj["carnivores_reconstruct_cluster_count"] = len(proposal.root_groups)
    arm_obj["carnivores_reconstruct_skipped_count"] = len(proposal.skipped_groups)
    skipped_raw_ids = [
        int(raw_by_compact[group]) for group in proposal.skipped_groups
        if group < raw_by_compact.size
    ]
    arm_obj["carnivores_reconstruct_skipped"] = ",".join(str(raw_id) for raw_id in skipped_raw_ids)
    group_assignment_counts = _get_vertex_group_assignment_counts(obj)
    arm_obj["carnivores_reconstruct_skipped_details"] = json.dumps(
        [
            {
                "compact_id": int(group),
                "raw_owner_id": int(raw_by_compact[group]),
                "reason": "TOPOLOGY_FILTER",
                "blender_name": (
                    obj.vertex_groups[group].name
                    if 0 <= group < len(obj.vertex_groups)
                    else ""
                ),
                "vertex_count": int(group_assignment_counts.get(group, 0)),
            }
            for group in proposal.skipped_groups
            if group < raw_by_compact.size
        ],
        separators=(",", ":"),
        sort_keys=True,
    )
    arm_obj["carnivores_reconstruct_component_policy"] = policy
    arm_obj["carnivores_reconstruct_rig_policy"] = creation_policy
    arm_obj["carnivores_reconstruct_smoothing"] = smooth_enabled
    arm_obj["carnivores_reconstruct_semantic_naming"] = semantic_enabled
    arm_obj["carnivores_reconstruct_side_axis"] = str(side_axis).upper()
    arm_obj["carnivores_reconstruct_side_inverted"] = side_inverted
    arm_obj["carnivores_reconstruct_proposal_checksum"] = str(
        obj.data.get("carnivores_rig_proposal_checksum", "")
    )
    arm_obj["carnivores_reconstruct_proposal_settings"] = json.dumps(
        proposal.settings, separators=(",", ":"), sort_keys=True, default=str
    )
    arm_obj["carnivores_reconstruct_component_sizes"] = json.dumps(
        proposal.settings.get("component_sizes", []),
        separators=(",", ":"),
        sort_keys=True,
    )
    arm_obj["carnivores_reconstruct_mirror_pair_count"] = int(
        proposal.settings.get("mirror_pair_count", 0)
    )
    arm_obj["carnivores_reconstruct_mirror_pairs"] = json.dumps(
        proposal.settings.get("mirror_pairs", []), separators=(",", ":")
    )
    arm_obj["carnivores_reconstruct_mirror_pair_details"] = json.dumps(
        proposal.settings.get("mirror_pair_details", []),
        separators=(",", ":"),
        sort_keys=True,
    )
    arm_obj["carnivores_reconstruct_central_group_count"] = int(
        proposal.settings.get("central_group_count", 0)
    )
    arm_obj["carnivores_reconstruct_accepted_edges"] = json.dumps(
        [
            [int(raw_by_compact[first]), int(raw_by_compact[second])]
            for first, second in proposal.accepted_edges
        ]
    )
    accepted_pairs = {tuple(sorted(pair)) for pair in proposal.accepted_edges}
    accepted_details = []
    for first, second in sorted(accepted_pairs):
        matching = [
            edge for edge in proposal.edge_candidates
            if (edge.group_a, edge.group_b) == (first, second)
        ]
        if not matching:
            continue
        edge = min(matching, key=lambda candidate: candidate.total_cost)
        accepted_details.append(edge)
    accepted_confidence = [edge.confidence for edge in accepted_details]
    forced_pairs = {
        tuple(proposal_edge_key(*edge))
        for edge in proposal.settings.get("forced_edges", [])
    }
    arm_obj["carnivores_reconstruct_edge_details"] = json.dumps(
        [
            {
                "owners": [int(raw_by_compact[edge.group_a]), int(raw_by_compact[edge.group_b])],
                "compact_ids": [int(edge.group_a), int(edge.group_b)],
                "reason": list(edge.reason_codes),
                "joint_source": "+".join(edge.reason_codes),
                "boundary_edges": int(edge.boundary_edge_count),
                "nearest_distance": round(float(edge.nearest_distance), 6),
                "cost": round(float(edge.total_cost), 6),
                "cost_terms": {
                    str(key): round(float(value), 6)
                    for key, value in edge.cost_terms.items()
                },
                "confidence": round(float(edge.confidence), 6),
                "decision": (
                    "FORCED"
                    if tuple(sorted((edge.group_a, edge.group_b))) in forced_pairs
                    else "AUTO"
                ),
            }
            for edge in accepted_details
        ],
        separators=(",", ":"),
        sort_keys=True,
    )
    arm_obj["carnivores_reconstruct_confidence"] = (
        float(np.mean(accepted_confidence)) if accepted_confidence else 0.0
    )
    arm_obj["carnivores_owner_attribute"] = OWNER_ATTR_NAME if owner_indices is not None else ""
    arm_obj["carnivores_owner_source_attribute"] = OWNER_SOURCE_ATTR_NAME if owner_source is not None else ""
    _store_reconstruction_owner_mapping(arm_obj, raw_by_compact)
    _store_reconstruction_name_map(
        arm_obj,
        raw_by_compact,
        active_ids,
        source_bone_names,
        blender_bone_names,
        export_bone_names,
    )
    store_generated_weight_checksum(arm_obj, obj)

    try:
        io_utils.assign_armature_modifier(obj, arm_obj)
        io_utils.finalize_reconstruction_lifecycle(
            obj, existing_rig, arm_obj, creation_policy
        )
    except Exception:
        if arm_obj != existing_rig and arm_obj.name in bpy.data.objects:
            io_utils._remove_armature_object(arm_obj)
        raise
    info(
        f"Topology rig reconstruction complete: {len(active_ids)} groups, "
        f"{len(proposal.accepted_edges)} edges, {len(proposal.root_groups)} roots."
    )
    return arm_obj


def _reconstruct_armature_topology(obj, root_override_idx=-1, proposal=None):
    snapshot = _snapshot_reconstruction_state(obj)
    context_snapshot = io_utils._capture_blender_context()
    existing_object_names = {armature.name for armature in bpy.data.objects}
    try:
        return _reconstruct_armature_topology_impl(
            obj,
            root_override_idx=root_override_idx,
            proposal=proposal,
        )
    except Exception:
        _discard_new_reconstruction_objects(obj, existing_object_names)
        _restore_reconstruction_state(obj, snapshot)
        raise
    finally:
        io_utils._restore_blender_context(context_snapshot)


@timed('reconstruct_armature')
def _reconstruct_armature_impl(obj, root_override_idx=-1):
    """
    Full workflow to reconstruct an armature from preserved owner data or vertex groups.
    """
    if not obj or obj.type != 'MESH':
        error("Active object must be a mesh")
        return None

    source_id = _ensure_reconstruction_source_id(obj, create=False)
    creation_policy = _resolve_reconstruction_policy(obj)
    existing_rig, lifecycle_ok = _validate_reconstruction_lifecycle(
        obj, creation_policy, source_id
    )
    if not lifecycle_ok:
        return None
    obj[RECONSTRUCTION_SOURCE_ID_PROPERTY] = source_id

    algorithm = _get_reconstruction_setting(
        obj, "carnivores_reconstruct_algorithm", "LEGACY"
    )
    if algorithm == "TOPOLOGY":
        return _reconstruct_armature_topology(obj, root_override_idx=root_override_idx)

    mesh = obj.data
    owner_indices = _get_reconstruction_owner_indices(obj)
    owner_source = _get_reconstruction_owner_source(obj)

    if owner_indices is None and not obj.vertex_groups:
        error("Object has no vertex groups or owner cache to reconstruct from")
        return None

    source_label = "stored owner attribute" if owner_indices is not None else "vertex groups"
    info(f"Reconstructing rig for '{obj.name}' from {source_label}...")

    # Optional pre-reconstruction smoothing (matches import-time smoothing behavior)
    smooth_enabled = bool(_get_reconstruction_setting(
        obj, "carnivores_reconstruct_smooth_weights", False
    ))
    centroid_owner_indices = owner_indices
    if smooth_enabled:
        smooth_iterations = int(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_smooth_iterations", 3
        ))
        smooth_factor = float(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_smooth_factor", 0.5
        ))
        smooth_joints_only = bool(_get_reconstruction_setting(
            obj, "carnivores_reconstruct_smooth_joints_only", True
        ))

        if not obj.vertex_groups and owner_indices is not None:
            group_count_for_groups = _get_reconstruction_group_count(obj, owner_indices)
            temp_bone_names = _build_reconstruction_bone_names(obj, group_count_for_groups, owner_source=owner_source)
            io_utils.create_vertex_groups_from_bones(obj, temp_bone_names, owner_indices)

        if obj.vertex_groups:
            io_utils.smooth_vertex_weights(
                obj,
                iterations=smooth_iterations,
                factor=smooth_factor,
                joints_only=smooth_joints_only,
            )
            # Use the smoothed vertex groups as the reconstruction source.
            centroid_owner_indices = None

    # 1. Calculate Centroids
    group_count = _get_reconstruction_group_count(obj, centroid_owner_indices)
    if group_count <= 0:
        error("No reconstructable owner groups were found")
        return None

    centroids, group_weights = calculate_vertex_group_centroids(
        obj,
        owner_indices=centroid_owner_indices,
        group_count=group_count,
        return_weights=True,
    )
    bone_names = _build_reconstruction_bone_names(obj, group_count, owner_source=owner_source)

    # ---- Phase 1A: Degeneracy Pruning ----
    # Build valid entries (skip groups with no vertices, returned as None)
    valid_entries = []
    for i in range(len(centroids)):
        if centroids[i] is None:
            warn(f"Group {i} ({bone_names[i]}) is degenerate (no vertices); skipping in reconstruction.")
        else:
            valid_entries.append((i, centroids[i], group_weights[i], bone_names[i]))
    if not valid_entries:
        error("All owner groups are degenerate (no vertices); cannot reconstruct armature")
        return None

    valid_indices = [v[0] for v in valid_entries]
    valid_centroids = [v[1] for v in valid_entries]
    valid_weights = [v[2] for v in valid_entries]
    valid_names = [v[3] for v in valid_entries]

    # ---- Phase 1B: Disconnected Cluster Detection ----
    # Only keep the largest spatial cluster; warn about isolated ones.
    cluster_labels = _detect_disconnected_clusters(valid_centroids)
    unique_clusters = set(cluster_labels.values())
    filter_legacy_clusters = bool(_get_reconstruction_setting(
        obj, "carnivores_reconstruct_legacy_filter_clusters", False
    ))
    if len(unique_clusters) > 1 and filter_legacy_clusters:
        from collections import Counter
        cluster_counts = Counter(cluster_labels.values())
        main_cluster_id = max(cluster_counts, key=lambda cid: cluster_counts[cid])
        new_entries = []
        for local_i, (orig_i, c, w, n) in enumerate(zip(valid_indices, valid_centroids, valid_weights, valid_names)):
            if cluster_labels[local_i] == main_cluster_id:
                new_entries.append((orig_i, c, w, n))
            else:
                warn(f"Group {orig_i} ({bone_names[orig_i]}) belongs to a disconnected cluster; skipping in reconstruction.")
        if not new_entries:
            error("Main body cluster is empty; cannot reconstruct armature")
            return None
        valid_entries = new_entries
        valid_indices = [v[0] for v in valid_entries]
        valid_centroids = [v[1] for v in valid_entries]
        valid_weights = [v[2] for v in valid_entries]
        valid_names = [v[3] for v in valid_entries]
    elif len(unique_clusters) > 1:
        info(
            f"Legacy centroid analysis found {len(unique_clusters)} spatial clusters; "
            "preserving every nonempty group for compatibility with the original reconstruction."
        )

    source_valid_names = list(valid_names)

    # 2. Infer Hierarchy (on stable main cluster only)
    local_override_idx = -1
    if root_override_idx >= 0:
        try:
            local_override_idx = valid_indices.index(root_override_idx)
        except ValueError:
            warn(f"Root override index {root_override_idx} is not in the reconstructed main cluster. Falling back to automatic selection.")

    # Calculate symmetry center X of the stable main cluster only
    main_positions = np.asarray(valid_centroids, dtype=np.float64)
    center_x = 0.0
    if len(main_positions) > 0:
        center_x = float(np.median(main_positions[:, 0]))

    # Apply bilateral semantic naming suffixes to generic names if enabled
    semantic_enabled = bool(_get_reconstruction_setting(
        obj, "carnivores_reconstruct_semantic_naming", True
    ))
    if semantic_enabled:
        valid_names = _apply_semantic_suffixes(obj, valid_names, valid_centroids, center_x)

    # Run mirror partner detection on the stable main cluster
    mirror_partners = _find_mirror_partners(valid_centroids, center_x)

    parents = infer_hierarchy_mst(
        valid_centroids,
        bone_names=valid_names,
        group_weights=valid_weights,
        root_override_idx=local_override_idx,
        mirror_partners=mirror_partners
    )

    # 3. Create Armature
    v_count = len(mesh.vertices)
    v_pos = np.empty(v_count * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', v_pos)
    v_pos = v_pos.reshape((v_count, 3))

    if owner_indices is not None:
        source_v_owners = np.asarray(owner_indices, dtype=np.int32).copy()
    else:
        source_v_owners = np.full(v_count, -1, dtype=np.int32)
        for v_idx, v in enumerate(mesh.vertices):
            if v.groups:
                source_v_owners[v_idx] = max(v.groups, key=lambda g: g.weight).group

    _ensure_reconstruction_vertex_groups(
        obj,
        group_count,
        source_v_owners,
        owner_source=owner_source,
    )
    legacy_heads, legacy_tails = _build_legacy_joint_geometry(
        v_pos, source_v_owners, valid_indices, parents, valid_names
    )
    legacy_analysis = analyze_rig_geometry(
        extract_rig_mesh_input(obj, owner_indices, owner_source)
    )
    legacy_roll_reference = _deterministic_roll_reference(legacy_analysis)
    legacy_center_x = float(np.median(legacy_heads[:, 0])) if len(legacy_heads) else 0.0
    legacy_rolls = []
    for head in legacy_heads:
        reference = legacy_roll_reference.copy()
        if head[0] < legacy_center_x:
            reference[0] *= -1.0
        legacy_rolls.append(reference)

    # create_armature receives a pruned local bone list, so owners must use the
    # same local indices rather than the original compact group IDs.
    v_owners = np.full(v_count, -1, dtype=np.int32)
    for local_idx, original_idx in enumerate(valid_indices):
        v_owners[source_v_owners == original_idx] = local_idx

    raw_by_compact = raw_ids_from_metadata(mesh.get(OWNER_MAPPING_PROPERTY))
    if raw_by_compact is None or raw_by_compact.size != group_count:
        if owner_source is not None:
            source_ids = np.unique(np.asarray(owner_source, dtype=np.int32))
            source_ids = source_ids[source_ids >= 0]
            raw_by_compact = source_ids if source_ids.size == group_count else None
        if raw_by_compact is None:
            raw_by_compact = np.arange(group_count, dtype=np.int32)

    reserved_group_names = [
        vertex_group.name for vertex_group in obj.vertex_groups
        if vertex_group.index not in set(valid_indices)
    ]
    blender_bone_names, export_bone_names = _resolve_reconstruction_name_pair(
        valid_names, reserved=reserved_group_names
    )

    arm_obj = io_utils.create_armature(
        blender_bone_names,
        legacy_heads,
        parents,
        obj.name,
        obj.users_collection[0] if obj.users_collection else None,
        verticesTransformedPos=v_pos,
        vertex_owners=v_owners,
        explicit_tail_positions=legacy_tails,
        roll_reference_vectors=legacy_rolls,
        creation_policy=creation_policy,
        existing_armature=existing_rig,
        world_matrix=obj.matrix_world,
    )
    if arm_obj is None:
        error("Armature construction failed")
        return None

    # Rename by compact owner identity so source-name cleaning cannot detach
    # weights from their corresponding bones.
    _rename_vertex_groups_by_compact_id(obj, valid_indices, blender_bone_names)
    _clear_reconstruction_metadata(arm_obj)

    # Store reconstruction metadata for diagnostics and round-trip validation
    root_local = next((i for i, p in enumerate(parents) if p == -1), -1)
    if root_local >= 0:
        arm_obj["carnivores_reconstruct_root"] = blender_bone_names[root_local]
        arm_obj["carnivores_reconstruct_root_idx"] = valid_indices[root_local]
        arm_obj["carnivores_reconstruct_root_raw_id"] = int(raw_by_compact[valid_indices[root_local]])
    arm_obj["carnivores_rig_algorithm"] = "LEGACY"
    arm_obj["carnivores_rig_algorithm_version"] = 1
    arm_obj["carnivores_rig_metadata_version"] = 1
    arm_obj["carnivores_reconstruct_joint_policy"] = "ROBUST_MEDIAN_CONTINUATION_PCA_V1"
    arm_obj[RECONSTRUCTION_SOURCE_ID_PROPERTY] = source_id
    arm_obj["carnivores_reconstruct_source_mesh"] = obj.name
    arm_obj["carnivores_reconstruct_rig_policy"] = creation_policy
    # Track all skipped groups: degenerate (empty) and disconnected.
    all_skipped = [i for i in range(group_count) if centroids[i] is None]
    kept_orig = set(valid_indices)
    disconnected = [i for i in range(group_count) if (centroids[i] is not None) and (i not in kept_orig)]
    all_skipped.extend(disconnected)
    skipped_set = sorted(set(all_skipped))
    skipped_raw_ids = [int(raw_by_compact[index]) for index in skipped_set]
    arm_obj["carnivores_reconstruct_skipped"] = ",".join(str(index) for index in skipped_raw_ids)
    arm_obj["carnivores_reconstruct_skipped_count"] = len(skipped_set)
    group_assignment_counts = _get_vertex_group_assignment_counts(obj)
    arm_obj["carnivores_reconstruct_skipped_details"] = json.dumps(
        [
            {
                "compact_id": int(index),
                "raw_owner_id": int(raw_by_compact[index]),
                "reason": "DEGENERATE" if centroids[index] is None else "DISCONNECTED_CLUSTER",
                "blender_name": (
                    obj.vertex_groups[index].name
                    if 0 <= index < len(obj.vertex_groups)
                    else ""
                ),
                "vertex_count": int(group_assignment_counts.get(index, 0)),
            }
            for index in skipped_set
        ],
        separators=(",", ":"),
        sort_keys=True,
    )

    cluster_count_val = len(unique_clusters) if 'unique_clusters' in locals() else 1
    parent_map_raw = {
        str(int(raw_by_compact[valid_indices[i]])): int(raw_by_compact[valid_indices[p]]) if p >= 0 else -1
        for i, p in enumerate(parents)
    }
    arm_obj["carnivores_reconstruct_parent_map"] = json.dumps(
        parent_map_raw, separators=(",", ":"), sort_keys=True
    )
    arm_obj["carnivores_reconstruct_cluster_count"] = cluster_count_val

    arm_obj["carnivores_reconstruct_source"] = source_label
    arm_obj["carnivores_owner_attribute"] = OWNER_ATTR_NAME if owner_indices is not None else ""
    arm_obj["carnivores_owner_source_attribute"] = OWNER_SOURCE_ATTR_NAME if owner_source is not None else ""
    _store_reconstruction_owner_mapping(arm_obj, raw_by_compact)
    _store_reconstruction_name_map(
        arm_obj,
        raw_by_compact,
        valid_indices,
        source_valid_names,
        blender_bone_names,
        export_bone_names,
    )
    store_generated_weight_checksum(arm_obj, obj)

    try:
        io_utils.assign_armature_modifier(obj, arm_obj)
        io_utils.finalize_reconstruction_lifecycle(
            obj, existing_rig, arm_obj, creation_policy
        )
    except Exception:
        if arm_obj != existing_rig and arm_obj.name in bpy.data.objects:
            io_utils._remove_armature_object(arm_obj)
        raise

    info("Rig reconstruction complete.")
    return arm_obj


def apply_topology_proposal(obj, proposal):
    """Apply a validated topology proposal transactionally."""
    if not topology_proposal_settings_match(obj, proposal):
        raise ValueError(
            "Topology proposal settings changed; analyze the mesh again before applying."
        )
    return _reconstruct_armature_topology(
        obj,
        root_override_idx=int(proposal.settings.get("root_override", -1)),
        proposal=proposal,
    )


def apply_stored_topology_proposal(obj):
    """Apply the current stored topology proposal after freshness validation."""
    proposal, _checksum = load_topology_proposal(obj)
    if not topology_proposal_settings_match(obj, proposal):
        raise ValueError(
            "Topology proposal settings changed; analyze the mesh again before applying."
        )
    return apply_topology_proposal(obj, proposal)


def reconstruct_armature(obj, root_override_idx=-1):
    if not obj or obj.type != 'MESH':
        return _reconstruct_armature_impl(obj, root_override_idx=root_override_idx)
    snapshot = _snapshot_reconstruction_state(obj)
    context_snapshot = io_utils._capture_blender_context()
    existing_object_names = {armature.name for armature in bpy.data.objects}
    try:
        return _reconstruct_armature_impl(obj, root_override_idx=root_override_idx)
    except Exception:
        _discard_new_reconstruction_objects(obj, existing_object_names)
        _restore_reconstruction_state(obj, snapshot)
        raise
    finally:
        io_utils._restore_blender_context(context_snapshot)
