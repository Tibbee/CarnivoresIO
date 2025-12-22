import bpy
import re
import wave
import tempfile
import os
import aud
import numpy as np
from .common import timed
from .io import apply_import_matrix
from . import io as io_utils
from .logger import info, debug, warn, error

# Global state for sound files
_temp_sound_files = set()

@timed('create_shape_keys_from_car_animations')
def create_shape_keys_from_car_animations(obj, animations, import_matrix_np):
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
    for anim in animations:
        anim_name = anim['name']
        frames_count = anim['frames_count']
        positions = anim['positions']  # (frames_count, vcount, 3) float32
        if positions.shape != (frames_count, vcount, 3):
            warn(f"Skipping {anim_name} (invalid positions shape {positions.shape})")
            continue
        for frame_i in range(frames_count):  # All frames as keys (Basis is static verts)
            key_name = f"{anim_name}.Frame_{frame_i+1:03d}"
            # Transform this frame's positions
            frame_pos = apply_import_matrix(positions[frame_i], import_matrix_np)  # Note: Use direct call (utils. not needed internally)
            # Add new key (from_mix=False to base on Basis)
            key = obj.shape_key_add(name=key_name, from_mix=False)
            flat_pos = frame_pos.ravel()
            key.data.foreach_set('co', flat_pos)
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
def keyframe_shape_key_animation_as_action(obj, anim_name, frame_start=1, kps=None, scene_fps=None):
    if not obj or obj.type != 'MESH':
        error('Selected object is not a mesh')
        return
    mesh = obj.data
    if not mesh.shape_keys:
        error('Object has no shape keys')
        return
    sk_data = mesh.shape_keys
    sk_data.animation_data_create()
    action_name = f"{anim_name}_Action"
    
    # Reuse existing action if possible to avoid duplicates (Action.001, etc.)
    action = bpy.data.actions.get(action_name)
    if action:
        # Clear existing data to re-bake
        action.fcurves.clear()
    else:
        action = bpy.data.actions.new(name=action_name)
    
    # Determine KPS and Step
    # Default KPS to 15 if not provided (common Carnivores value) or derive?
    # Actually, usually caller provides it. If None, maybe default to scene FPS (1:1 mapping)
    target_kps = int(kps) if kps is not None else 15 
    
    if scene_fps is None:
        scene_fps = bpy.context.scene.render.fps
        
    # Calculate frame_step: How many Blender frames represent 1 Game Frame
    # e.g. 30 FPS / 10 KPS = 3.0 Blender frames per Game Frame.
    frame_step = scene_fps / target_kps
    
    # Store KPS in action for reference
    action["carnivores_kps"] = target_kps
        
    try:
        sk_data.animation_data.action = action
    except AttributeError:
        warn(f"Could not set active action '{action.name}' (likely driven by NLA). Continuing update...")
        
    key_blocks = [kb for kb in sk_data.key_blocks if re.match(f"^{re.escape(anim_name)}\.Frame_\\d+", kb.name)]
    key_blocks.sort(key=lambda kb: kb.name)
    if not key_blocks:
        warn(f"No shape keys found for animation '{anim_name}'")
        return
    
    num_frames = len(key_blocks)
    # Calculate total duration in Blender frames
    duration_frames = (num_frames * frame_step) 
    
    debug(f"Creating Action '{action_name}' for '{anim_name}'")
    debug(f"           KPS: {target_kps}, Scene FPS: {scene_fps} -> Step: {frame_step:.2f} frames")
    debug(f"           Total Duration: {duration_frames:.1f} frames ({duration_frames/scene_fps:.2f}s)")
    
    # Identify all shape keys, excluding Basis
    reference_key = sk_data.reference_key
    all_keys = [kb for kb in sk_data.key_blocks if kb != reference_key]
    group_keys = key_blocks
    other_keys = [kb for kb in all_keys if kb not in group_keys]
    
    # Create F-Curves upfront for all relevant keys (one per shape key)
    fcurves = {}
    for kb in other_keys + group_keys:
        data_path = f'key_blocks["{kb.name}"].value'
        fc = action.fcurves.new(data_path=data_path, index=-1)
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
    strip = track.strips.new(strip_name, frame_start, action)
    strip.frame_end = frame_end
    anim_data.action = None  # Unlink active action (push down)

    debug(f"Action '{action.name}' pushed to NLA strip '{strip.name}' ({frame_start}-{frame_end})")
    return strip

@timed('auto_create_shape_key_actions_from_car')
def auto_create_shape_key_actions_from_car(obj, frame_step=1, parsed_animations=None):
    if not obj or obj.type != 'MESH':
        error('Selected object is not a mesh')
        return
    mesh = obj.data
    if not mesh.shape_keys:
        info('No shape keys on object; skipping animation setup.')
        return
    sk_data = mesh.shape_keys
    names = [kb.name for kb in sk_data.key_blocks if '.' in kb.name]
    
    # Create KPS lookup map if animations provided
    kps_map = {}
    if parsed_animations:
        for anim in parsed_animations:
            kps_map[anim['name']] = anim['kps']
    
    # Preserve order: iterate names, extract base, add to list if not seen
    base_names = []
    seen = set()
    for n in names:
        if '.Frame_' in n:
            base = n.split('.Frame_')[0]
            if base not in seen:
                seen.add(base)
                base_names.append(base)
                
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
        action = keyframe_shape_key_animation_as_action(obj, anim_name, frame_start=1, kps=anim_kps)
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
                strip = track.strips.new(strip_name, start_frame, action)
                strip.use_sync_length = True  # Tighten eval for discrete steps
            
            anim_data.action = None  # Clear active action
            debug(f"Pushed {len(actions)} actions to NLA batch (using {num_tracks_used} tracks).")
    except Exception as e:
        warn(f"NLA batch push failed (non-fatal): {e}")
        import traceback
        traceback.print_exc()  # Log full stack for debug (remove if noisy) 
    
    # Single update at end (key for perf)
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
    info('Completed all animations.')
    return actions

def get_action_frame_range(action):
    """Return the min/max frame numbers for keyframes in this Action."""
    if not action or not action.fcurves:
        return (1, 1)
    frames = [kp.co[0] for fc in action.fcurves for kp in fc.keyframe_points]
    return (int(min(frames)), int(max(frames)))

def import_car_sounds(self, sounds, model_name, context):
    imported_sounds = []
    for idx, s in enumerate(sounds):
        sound_name = s['name']
        data = s['data']
        if data.size == 0:
            warn(f"Skipping empty sound '{sound_name}' (0 samples).")
            continue
        # Create temp WAV
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_path = temp_file.name
        try:
            with wave.open(temp_path, 'wb') as wf:
                wf.setnchannels(1)  # Mono
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(22050)
                wf.setnframes(data.size)
                wf.writeframes(data.tobytes())
            # Load into Blender
            sound_block = bpy.data.sounds.load(temp_path)
            # Set the name before doing anything else
            sound_block.name = sound_name
            # Pack to embed
            sound_block.pack()
            # Unpack and repack to force Blender to update
            if sound_block.packed_file:
                sound_block.unpack(method='USE_LOCAL')
                # Add the path of the created file to our set for later cleanup
                unpacked_filepath = bpy.path.abspath(sound_block.filepath)
                _temp_sound_files.add(unpacked_filepath)
                sound_block.pack()

            imported_sounds.append(sound_block)
            info(f"Imported and packed sound '{sound_block.name}' ({data.size} samples).")
        except Exception as e:
            error(f"Failed to import sound '{sound_name}': {str(e)}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)  # Cleanup (safe now that it's packed)
    return imported_sounds

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
    if not action or not action.fcurves:
        return

    # Calculate target step
    if kps <= 0: kps = 1
    frame_step = scene_fps / kps
    
    debug(f"Rescaling '{action.name}' to {kps} KPS (Step: {frame_step:.2f})")

    # 1. Collect all unique frame times
    unique_frames = set()
    for fc in action.fcurves:
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
    for fc in action.fcurves:
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

@timed('calculate_vertex_group_centroids')
def calculate_vertex_group_centroids(obj):
    """
    Calculates the weighted centroid for each vertex group.
    Returns: list of (x, y, z) positions in group order.
    """
    mesh = obj.data
    num_groups = len(obj.vertex_groups)
    if num_groups == 0:
        return []

    # Initialize accumulators
    centroids = np.zeros((num_groups, 3), dtype=np.float64)
    weights_sum = np.zeros(num_groups, dtype=np.float64)

    # Get vertex positions
    v_count = len(mesh.vertices)
    v_pos = np.empty(v_count * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', v_pos) # FIXED: Use foreach_get, not foreach_set
    v_pos = v_pos.reshape((v_count, 3))

    # Process weights
    for v_idx, v in enumerate(mesh.vertices):
        for g in v.groups:
            g_idx = g.group
            w = g.weight
            if g_idx < num_groups:
                centroids[g_idx] += v_pos[v_idx] * w
                weights_sum[g_idx] += w

    # Avoid division by zero (for groups with no assigned vertices)
    for i in range(num_groups):
        if weights_sum[i] > 0:
            centroids[i] /= weights_sum[i]
        else:
            # Fallback to mesh center if group is empty
            centroids[i] = np.mean(v_pos, axis=0) if v_count > 0 else (0, 0, 0)

    return centroids.tolist()

@timed('infer_hierarchy_mst')
def infer_hierarchy_mst(centroids, bone_names=None):
    """
    Infers a parent-child hierarchy from centroids using Prim's MST algorithm.
    Symmetry-Aware: Heavily penalizes crossing the X=0 center plane.
    Root selection: Priority given to 'floor' bone or ID 0.
    """
    num_bones = len(centroids)
    if num_bones <= 1:
        return [-1] * num_bones

    parents = [-1] * num_bones
    connected = [False] * num_bones
    all_pos = np.array(centroids)
    
    # 1. Identify Root
    # Preference: Bone named 'floor' (case insensitive) or index 0.
    root_idx = 0
    if bone_names:
        for i, name in enumerate(bone_names):
            if "floor" in name.lower():
                root_idx = i
                break
    
    connected[root_idx] = True
    
    # 2. Symmetry-Aware MST
    for _ in range(num_bones - 1):
        min_dist = float('inf')
        best_pair = (-1, -1) # (parent, child) 
        
        for i in range(num_bones):
            if not connected[i]: continue
            
            p_pos = all_pos[i]
            for j in range(num_bones):
                if connected[j]: continue
                
                c_pos = all_pos[j]
                
                # Base distance
                dist = np.linalg.norm(p_pos - c_pos)
                
                # Symmetry Penalty: Prevent cross-leg connections
                # Penalize if child and parent are on opposite sides of X center plane
                # (Using 0.05 margin to allow spine bones to connect even if slightly off-center)
                if (p_pos[0] > 0.05 and c_pos[0] < -0.05) or (p_pos[0] < -0.05 and c_pos[0] > 0.05):
                    dist *= 50.0 # High penalty
                
                if dist < min_dist:
                    min_dist = dist
                    best_pair = (i, j)
        
        if best_pair[1] != -1:
            parents[best_pair[1]] = best_pair[0]
            connected[best_pair[1]] = True
            
    return parents

@timed('reconstruct_armature')
def reconstruct_armature(obj):
    """
    Full workflow to reconstruct an armature from vertex groups.
    """
    if not obj or obj.type != 'MESH':
        error("Active object must be a mesh")
        return None

    if not obj.vertex_groups:
        error("Object has no vertex groups to reconstruct from")
        return None

    info(f"Reconstructing rig for '{obj.name}'...")

    # 1. Calculate Centroids
    centroids = calculate_vertex_group_centroids(obj)
    bone_names = [vg.name for vg in obj.vertex_groups]
    
    # 2. Infer Hierarchy
    parents = infer_hierarchy_mst(centroids, bone_names=bone_names)
    
    # 3. Create Armature
    mesh = obj.data
    v_count = len(mesh.vertices)
    v_pos = np.empty(v_count * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', v_pos) # FIXED: Use foreach_get
    v_pos = v_pos.reshape((v_count, 3))
    
    v_owners = np.zeros(v_count, dtype=np.int32)
    for v_idx, v in enumerate(mesh.vertices):
        if v.groups:
            v_owners[v_idx] = max(v.groups, key=lambda g: g.weight).group
        else:
            v_owners[v_idx] = -1

    arm_obj = io_utils.create_armature(
        bone_names,
        centroids,
        parents,
        obj.name,
        obj.users_collection[0] if obj.users_collection else None,
        verticesTransformedPos=v_pos,
        vertex_owners=v_owners
    )
    
    # 4. Link Mesh to Armature
    io_utils.assign_armature_modifier(obj, arm_obj)
    
    info("Rig reconstruction complete.")
    return arm_obj
