import bpy
import numpy as np
import struct
import time
import os
import re

from .. import utils
from ..utils.logger import info, debug, warn, error

def gather_vtl_animation(obj, export_matrix, vertex_count):
    """
    Collects a single animation data by baking the object's deformation.
    Strategy:
    1. Check for an active NLA strip if NLA is enabled.
    2. Fallback to the active Action.
    """
    context = bpy.context
    scene = context.scene
    
    # --- 1. Find Animation Source ---
    anim_data = None
    target_action = None
    action_name = "Anim"
    start_frame = 0
    end_frame = 0
    kps = int(scene.render.fps)

    if obj.data.shape_keys and obj.data.shape_keys.animation_data:
        anim_data = obj.data.shape_keys.animation_data
    elif obj.parent and obj.parent.type == 'ARMATURE' and obj.parent.animation_data:
        anim_data = obj.parent.animation_data
    elif obj.animation_data:
        anim_data = obj.animation_data

    if not anim_data:
        raise ValueError("No animation data found on Object, ShapeKeys, or Parent Armature.")

    # Determine what to export
    is_nla_mode = anim_data.use_nla
    
    # Temporary solo state for NLA
    original_mute_states = {}
    if anim_data.nla_tracks:
        for track in anim_data.nla_tracks:
            original_mute_states[track] = track.mute

    target_track = None

    if is_nla_mode and anim_data.nla_tracks:
        # Try to find an active track / active strip, or just take the first unmuted or top track
        for track in anim_data.nla_tracks:
            if track.select or track.active:
                target_track = track
                break
        
        if not target_track and len(anim_data.nla_tracks) > 0:
             target_track = anim_data.nla_tracks[0]

        if target_track and len(target_track.strips) > 0:
            # Get active strip, or first strip
            strip = None
            for s in target_track.strips:
                if s.select or s.active:
                    strip = s
                    break
            if not strip:
                strip = target_track.strips[0]
            
            target_action = strip.action
            action_name = strip.name
            start_frame = int(strip.frame_start)
            end_frame = int(strip.frame_end)
            if target_action:
                kps = target_action.get("carnivores_kps", int(scene.render.fps))
    else:
        # Action mode
        if anim_data.action:
            target_action = anim_data.action
            action_name = target_action.name.replace("_Action", "")
            start_frame = int(target_action.frame_range[0])
            end_frame = int(target_action.frame_range[1])
            kps = target_action.get("carnivores_kps", int(scene.render.fps))

    if not target_action:
        raise ValueError("Could not find a target Action or NLA Strip to export.")

    debug(f"Exporting animation: '{action_name}' ({start_frame}-{end_frame}), KPS: {kps}")

    # --- STATE MANAGEMENT ---
    original_frame = scene.frame_current
    original_action = anim_data.action
    original_use_nla = anim_data.use_nla
    
    original_show_only_shape_key = False
    if obj.type == 'MESH' and obj.show_only_shape_key:
        original_show_only_shape_key = True
        obj.show_only_shape_key = False
        debug("Temporarily disabled Shape Key Pinning for bake.")

    mod_states = {}
    for mod in obj.modifiers:
        mod_states[mod.name] = mod.show_viewport
        if mod.type in {'ARMATURE', 'HOOK'}: 
            mod.show_viewport = True 
        else:
            mod.show_viewport = False 

    # Prepare matrix
    mesh_to_arm = np.eye(4)
    if obj.parent and obj.parent.type == 'ARMATURE':
         mesh_to_arm = np.array(obj.parent.matrix_world.inverted() @ obj.matrix_world)
    full_matrix = export_matrix @ mesh_to_arm

    # Determine Fast path
    can_use_fast_path = (
        obj.data.shape_keys is not None and 
        all(mod.type not in {'ARMATURE', 'HOOK', 'CLOTH', 'SOFT_BODY'} for mod in obj.modifiers if mod.show_viewport)
    )
    
    trans_basis = None
    trans_delta = None
    key_blocks_names = {}
    
    if can_use_fast_path:
        sk_data = obj.data.shape_keys
        key_blocks = sk_data.key_blocks
        num_keys = len(key_blocks)
        key_blocks_names = {kb.name: i for i, kb in enumerate(key_blocks)}
        
        co_blocks = np.empty((num_keys, vertex_count, 3), dtype=np.float32)
        for i, kb in enumerate(key_blocks):
            kb.data.foreach_get("co", co_blocks[i].ravel())
            
        basis = co_blocks[0]
        key_delta = co_blocks[1:] - basis
        
        trans_basis = utils.apply_import_matrix(basis, full_matrix)
        flat_delta = key_delta.reshape(-1, 3)
        linear_matrix = full_matrix[:3, :3]
        trans_delta = (flat_delta @ linear_matrix.T).reshape(num_keys - 1, vertex_count, 3)

    def bake_range(start, end, kps):
        frames_data = []
        scene_fps = scene.render.fps
        if kps <= 0: kps = 1
        frame_step = scene_fps / kps
        num_samples = int(((end - start) / frame_step) + 0.5) + 1
        
        full_matrix_cache = None

        for i in range(num_samples):
            current_frame = start + (i * frame_step)
            scene.frame_set(int(current_frame), subframe=(current_frame % 1.0))
            
            depsgraph = context.evaluated_depsgraph_get()
            eval_obj = obj.evaluated_get(depsgraph)
            mesh = eval_obj.to_mesh()
            
            try:
                count = len(mesh.vertices)
                if count != vertex_count:
                    raise ValueError(f"Frame {current_frame:.2f} has {count} vertices, expected {vertex_count}.")
                
                verts_co_flat = np.empty(count * 3, dtype=np.float32)
                mesh.vertices.foreach_get("co", verts_co_flat)
                verts_co = verts_co_flat.reshape(count, 3)
                
                if i == 0:
                    full_matrix_cache = export_matrix
                    if obj.parent and obj.parent.type == 'ARMATURE':
                        mesh_to_arm = eval_obj.parent.matrix_world.inverted() @ eval_obj.matrix_world
                        full_matrix_cache = export_matrix @ np.array(mesh_to_arm)

                transformed_co = utils.apply_import_matrix(verts_co, full_matrix_cache)
                quantized = np.clip(np.round(transformed_co * 16.0), -32768, 32767).astype(np.int16)
                frames_data.append(quantized)
            finally:
                eval_obj.to_mesh_clear()
        
        return frames_data

    def bake_range_fast(start, end, kps, anim_source_data):
        fcurve_map = {}
        eval_time_fc = None
        num_keys_total = len(trans_delta) + 1
        
        sk_data = obj.data.shape_keys
        use_relative = sk_data.use_relative

        def get_fcurves(act):
            if not act: return []
            if hasattr(act, "slots") and bpy.app.version >= (5, 0, 0):
                import bpy_extras.anim_utils
                fcs = []
                for slot in act.slots:
                    bag = bpy_extras.anim_utils.action_get_channelbag_for_slot(act, slot)
                    if bag: fcs.extend(bag.fcurves)
                return fcs
            return getattr(act, "fcurves", [])

        for fc in get_fcurves(anim_source_data):
            if use_relative:
                match = re.match(r'key_blocks\["(.+)"\]\.value', fc.data_path)
                if match:
                    kb_name = match.group(1)
                    if kb_name in key_blocks_names:
                        idx = key_blocks_names.get(kb_name) 
                        if idx > 0: 
                            fcurve_map[idx - 1] = fc
            else:
                if fc.data_path == 'eval_time':
                    eval_time_fc = fc
                    break

        scene_fps = scene.render.fps
        if kps <= 0: kps = 1
        frame_step = scene_fps / kps
        num_samples = int(((end - start) / frame_step) + 0.5) + 1
        
        frames_data = []

        abs_frame_values = None
        if not use_relative:
            abs_frame_values = np.array([kb.frame for kb in sk_data.key_blocks], dtype=np.float32)

        for i in range(num_samples):
            t = start + (i * frame_step)
            
            if use_relative:
                current_weights = np.zeros(num_keys_total - 1, dtype=np.float32)
                for idx, fc in fcurve_map.items():
                    current_weights[idx] = fc.evaluate(t)
                
                interp = trans_basis + np.tensordot(current_weights, trans_delta, axes=([0], [0]))
            else:
                val = eval_time_fc.evaluate(t) if eval_time_fc else 0.0
                
                if val <= abs_frame_values[0]:
                    interp = trans_basis
                elif val >= abs_frame_values[-1]:
                    interp = trans_basis + trans_delta[-1]
                else:
                    idx_right = np.searchsorted(abs_frame_values, val)
                    idx_left = idx_right - 1
                    f_left = abs_frame_values[idx_left]
                    f_right = abs_frame_values[idx_right]
                    factor = (val - f_left) / (f_right - f_left)
                    co_left = trans_basis if idx_left == 0 else trans_basis + trans_delta[idx_left - 1]
                    co_right = trans_basis + trans_delta[idx_right - 1]
                    interp = co_left + (co_right - co_left) * factor
            
            quantized = np.clip(np.round(interp * 16.0), -32768, 32767).astype(np.int16)
            frames_data.append(quantized)

        return frames_data

    # Perform actual baking
    frames = []
    try:
        if is_nla_mode and target_track:
            anim_data.use_nla = True
            for track in anim_data.nla_tracks:
                track.mute = True
            target_track.mute = False
            
            if can_use_fast_path:
                frames = bake_range_fast(start_frame, end_frame, kps, target_action)
            else:
                frames = bake_range(start_frame, end_frame, kps)
                
        else:
            anim_data.use_nla = False
            if can_use_fast_path:
                frames = bake_range_fast(start_frame, end_frame, kps, target_action)
            else:
                frames = bake_range(start_frame, end_frame, kps)
                
    except Exception as e:
        error(f"Critical Error during VTL animation bake: {e}")
        raise e

    finally:
        scene.frame_set(original_frame)
        if anim_data: 
            try:
                anim_data.action = original_action
            except AttributeError:
                pass
            try:
                anim_data.use_nla = original_use_nla
            except AttributeError:
                pass
            if original_mute_states:
                for track, state in original_mute_states.items():
                    track.mute = state
        
        for mod_name, state in mod_states.items():
            if mod_name in obj.modifiers:
                obj.modifiers[mod_name].show_viewport = state
        
        if original_show_only_shape_key:
             obj.show_only_shape_key = True

    return {
        'name': action_name,
        'kps': kps,
        'frames': frames
    }


def export_vtl(filepath, obj, export_matrix):
    print(f"--- Starting .vtl export to: {filepath} ---")
    debug(f"--- Starting .vtl export to: {filepath} ---")
    
    start_time = time.perf_counter()
    
    # Needs to be a mesh
    if obj.type != 'MESH':
        raise ValueError("Active object is not a MESH.")
    
    # Update depsgraph to get accurate count
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = obj.evaluated_get(depsgraph)
    mesh = eval_obj.to_mesh()
    vertex_count = len(mesh.vertices)
    eval_obj.to_mesh_clear()

    # Gather the active animation
    anim = gather_vtl_animation(obj, export_matrix, vertex_count)
    frames = anim['frames']
    kps = anim['kps']
    frames_count = len(frames)

    if frames_count == 0:
        raise ValueError("Animation resulted in 0 frames. Nothing to export.")

    # 4. Write File
    with open(filepath, 'wb') as f:
        # Header (12 bytes)
        # 0x00 uint32 VCount
        f.write(struct.pack('<I', vertex_count))
        # 0x04 uint32 aniKPS
        f.write(struct.pack('<I', kps))
        # 0x08 uint32 FramesCount
        f.write(struct.pack('<I', frames_count))
        
        # Frames Data
        for frame_data in frames:
            # frame_data is int16 array (V, 3)
            frame_data.tofile(f)
            
    info(f"Finished .vtl export: {filepath} (Vertices: {vertex_count}, Frames: {frames_count}) in {time.perf_counter() - start_time:.4f}s")
