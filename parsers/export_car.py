import bpy
import numpy as np
import aud
import os
import struct

from ..core.core import CAR_HEADER_DTYPE, VERTEX_DTYPE, FACE_DTYPE
from ..core.constants import TEXTURE_WIDTH
from .. import utils
from .export_3df import gather_mesh_data
from ..utils.logger import info, debug, warn, error

# Helper for sound conversion
def convert_sound_to_22khz_mono(sound_datablock):
    """
    Converts a Blender Sound datablock to raw 16-bit signed, 22050Hz, Mono PCM data.
    Returns (bytes_data, length_in_bytes)
    """
    if not sound_datablock:
        return None, 0
        
    # Use aud for conversion
    try:
        factory = sound_datablock.factory
        if not factory:
            # Fallback for external files not yet cached
            abs_path = bpy.path.abspath(sound_datablock.filepath)
            if os.path.exists(abs_path):
                factory = aud.Sound.file(abs_path)
            else:
                warn(f"Could not load factory for sound {sound_datablock.name}")
                return None, 0

        # Resample: 22050 Hz
        factory = factory.limit(0, 100000) # Safety limit? No, just process.
        factory = factory.resample(22050)
        # Mixdown: Mono
        factory = factory.rechannel(1) # 1 channel
        
        # Render to numpy array
        # aud.Sound.data() returns numpy array of float32 samples usually
        data = factory.data() 
        
        if data is None:
            return None, 0
            
        # Convert float32 [-1, 1] to int16 [-32768, 32767]
        # Note: aud.Sound.data() return format depends on backend but usually float32
        # Let's check dtype
        if data.dtype == np.float32:
            data = np.clip(data * 32767, -32768, 32767).astype(np.int16)
        elif data.dtype == np.int32:
             # Sometimes it might be different? Assuming float32 for now as per API
             pass
             
        return data.tobytes(), len(data) * 2 # 2 bytes per sample
        
    except Exception as e:
        error(f"Error converting sound {sound_datablock.name}: {e}")
        return None, 0

def gather_car_animations(obj, export_matrix, vertex_count):
    """
    Collects animation data by baking the object's deformation.
    Strategy:
    1. If NLA tracks exist, iterate through each strip (Reversed order):
       - Solo the track (mute others).
       - Bake the frame range.
    2. If no NLA tracks, fallback to baking the active Action.
    """
    animations = []
    context = bpy.context
    scene = context.scene
    
    # --- 1. Find Animation Source ---
    anim_data = None
    source_label = ""
    
    # Priority: Shape Keys -> Parent Armature -> Object
    if obj.data.shape_keys and obj.data.shape_keys.animation_data:
        anim_data = obj.data.shape_keys.animation_data
        source_label = "Shape Keys"
    elif obj.parent and obj.parent.type == 'ARMATURE' and obj.parent.animation_data:
        anim_data = obj.parent.animation_data
        source_label = f"Parent Armature ({obj.parent.name})"
    elif obj.animation_data:
        anim_data = obj.animation_data
        source_label = f"Object ({obj.name})"
        
    if not anim_data:
        warn("No animation data found on Object, ShapeKeys, or Parent Armature.")
        return []
        
    debug(f"Found animation source: {source_label}")

    # --- STATE MANAGEMENT ---
    original_frame = scene.frame_current
    original_action = anim_data.action
    original_use_nla = anim_data.use_nla
    
    # Store original mute states if NLA exists
    original_mute_states = {}
    if anim_data.nla_tracks:
        for track in anim_data.nla_tracks:
            original_mute_states[track] = track.mute

    # Shape Key Pinning Handling
    original_show_only_shape_key = False
    if obj.type == 'MESH' and obj.show_only_shape_key:
        original_show_only_shape_key = True
        obj.show_only_shape_key = False # Disable pinning to allow animation
        debug("Temporarily disabled Shape Key Pinning for bake.")

    # Store Modifier States & Force Enable Deformers
    mod_states = {}
    for mod in obj.modifiers:
        mod_states[mod.name] = mod.show_viewport
        if mod.type in {'ARMATURE', 'HOOK'}: 
            mod.show_viewport = True # Force enable deformers
        else:
            mod.show_viewport = False # Disable others

    # --- Define Bake Helper ---
    def bake_range(name, start, end, kps, sound_ptr):
        debug(f"Baking '{name}' ({start}-{end}) KPS:{kps}")
        frames_data = []
        
        # Calculate time step (Blender Frames per Game Frame)
        # e.g. 60 FPS / 20 KPS = 3.0 step
        scene_fps = scene.render.fps
        if kps <= 0: kps = 1 # Safety
        frame_step = scene_fps / kps
        
        # Robustly calculate number of samples
        # Use explicit +0.5 for "round half up" logic to avoid Banker's Rounding (even/odd) issues
        num_samples = int(((end - start) / frame_step) + 0.5) + 1
        
        debug(f"         Step: {frame_step:.4f}, Samples: {num_samples}")

        for i in range(num_samples):
            current_frame = start + (i * frame_step)
            
            # Subframe support: passing float to frame_set works in Blender
            scene.frame_set(int(current_frame), subframe=(current_frame % 1.0))
            context.view_layer.update() # Ensure depsgraph is fully updated
            
            # Evaluate mesh (Deformed by Armature/Action/NLA)
            depsgraph = context.evaluated_depsgraph_get()
            eval_obj = obj.evaluated_get(depsgraph)
            
            # Use to_mesh() to get the deformed geometry with modifiers applied
            mesh = eval_obj.to_mesh()
            
            try:
                count = len(mesh.vertices)
                if count != vertex_count:
                    error(f"Frame {current_frame:.2f} of '{name}' has {count} vertices, expected {vertex_count} (Base). Skipping animation.")
                    eval_obj.to_mesh_clear()
                    return None # Signal error
                
                # Bulk get coords
                verts_co_flat = np.empty(count * 3, dtype=np.float32)
                mesh.vertices.foreach_get("co", verts_co_flat)
                verts_co = verts_co_flat.reshape(count, 3)
                
                # Transform into Armature-local space if armature exists
                full_matrix = export_matrix
                if obj.parent and obj.parent.type == 'ARMATURE':
                    # Mesh to Armature transform (using evaluated matrices for animation accuracy)
                    mesh_to_arm = eval_obj.parent.matrix_world.inverted() @ eval_obj.matrix_world
                    full_matrix = export_matrix @ np.array(mesh_to_arm)

                transformed_co = utils.apply_import_matrix(verts_co, full_matrix)
                
                # Quantize to fixed point 16.0
                quantized = np.clip(transformed_co * 16.0, -32768, 32767).astype(np.int16)
                frames_data.append(quantized)
            
            finally:
                eval_obj.to_mesh_clear()
        
        # Static Check
        if len(frames_data) > 1:
            if all(np.array_equal(f, frames_data[0]) for f in frames_data[1:]):
                 warn(f"Animation '{name}' appears to be static.")

        return frames_data

    try:
        # --- PATH A: NLA TRACKS ---
        if anim_data.nla_tracks:
            debug("Mode: NLA Tracks (Soloing)")
            anim_data.use_nla = True # Ensure NLA is ON
            
            # Mute ALL tracks first
            for track in anim_data.nla_tracks:
                track.mute = True
            
            # Iterate Tracks in REVERSE (Top-most first? or whatever user requested)
            # User requested "reversed order we handle them currently"
            # Usually we want to export the list of animations.
            for track in reversed(anim_data.nla_tracks):
                
                # Solo this track
                track.mute = False
                
                for strip in track.strips:
                    action = strip.action
                    if not action: continue
                    
                    # Name
                    anim_name = strip.name
                    # Use the NLA Strip name as the exported animation name.
                    # This allows users to reuse the same Action multiple times (e.g. loops)
                    # or rename animations for export without changing the source Action.
                    clean_name = strip.name
                    
                    # Range
                    start = int(strip.frame_start)
                    end = int(strip.frame_end)
                    
                    # KPS/Sound
                    kps = action.get("carnivores_kps", int(scene.render.fps))
                    snd_ptr = getattr(action, 'carnivores_sound_ptr', None)
                    
                    # Bake
                    frames = bake_range(clean_name, start, end, kps, snd_ptr)
                    
                    if frames:
                        animations.append({
                            'name': clean_name,
                            'kps': kps,
                            'frames': frames,
                            'sound_ptr': snd_ptr
                        })
                
                # Re-mute after processing this track
                track.mute = True

        # --- PATH B: ACTIVE ACTION (Fallback) ---
        elif anim_data.action:
            debug("Mode: Active Action (No NLA)")
            anim_data.use_nla = False # Force Action
            
            action = anim_data.action
            clean_name = action.name.replace("_Action", "")
            start, end = int(action.frame_range[0]), int(action.frame_range[1])
            kps = action.get("carnivores_kps", int(scene.render.fps))
            snd_ptr = getattr(action, 'carnivores_sound_ptr', None)
            
            frames = bake_range(clean_name, start, end, kps, snd_ptr)
            if frames:
                animations.append({
                    'name': clean_name,
                    'kps': kps,
                    'frames': frames,
                    'sound_ptr': snd_ptr
                })
                
        else:
            warn("No NLA tracks and no Active Action. No animations exported.")

    except Exception as e:
        error(f"Critical Error during animation bake: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # --- RESTORE STATE ---
        scene.frame_set(original_frame)
        
        if anim_data: 
            try:
                anim_data.action = original_action
            except AttributeError:
                warn("Could not restore active action (likely NLA driven/read-only).")
            
            try:
                anim_data.use_nla = original_use_nla
            except AttributeError:
                warn("Could not restore use_nla state (likely NLA driven/read-only).")
            
            # Restore Mute States
            if original_mute_states:
                for track, state in original_mute_states.items():
                    track.mute = state
        
        # Restore Modifiers
        for mod_name, state in mod_states.items():
            if mod_name in obj.modifiers:
                obj.modifiers[mod_name].show_viewport = state
        
        # Restore Pinning
        if original_show_only_shape_key:
             obj.show_only_shape_key = True

    return animations

def export_car(filepath, obj, export_matrix, export_textures=False, 
               flip_u=False, flip_v=False, flip_handedness=True, 
               model_name_override=""):
    
    # 1. Gather Base Mesh Data
    (vertex_count, face_count, bone_count, texture_size, 
     faces_arr, verts_arr, bones_arr, texture_raw) = gather_mesh_data(
        obj, export_matrix, export_textures, flip_u, flip_v, flip_handedness
    )

    # 2. Gather Animations & Sounds
    anims = gather_car_animations(obj, export_matrix, vertex_count)
    
    sounds_map = {} # Sound DataBlock -> Index in file
    sound_list = [] # List of dicts to write
    cross_ref = np.full(64, -1, dtype=np.int32)
    
    # Process sounds
    for i, anim in enumerate(anims):
        if i >= 64: 
            warn("More than 64 animations, truncation will occur in cross-ref.")
            break
            
        snd = anim['sound_ptr']
        if snd:
            if snd.name not in sounds_map:
                # Convert and add
                data_bytes, length = convert_sound_to_22khz_mono(snd)
                if data_bytes:
                    idx = len(sound_list)
                    sounds_map[snd.name] = idx
                    sound_list.append({
                        'name': snd.name,
                        'length': length,
                        'data': data_bytes
                    })
                else:
                    sounds_map[snd.name] = -1
            
            cross_ref[i] = sounds_map[snd.name]

    # 3. Build Header
    header = np.zeros(1, dtype=CAR_HEADER_DTYPE)
    
    # Model Name
    m_name = model_name_override if model_name_override else os.path.splitext(os.path.basename(filepath))[0]
    # Ensure "msc: #" suffix if not present? 
    # Actually, the user might want to set this exactly.
    # We will truncate to 32 chars.
    header['model_name'] = m_name.encode('ascii', 'ignore')[:32].ljust(32, b'\x00')
    
    header['ani_count'] = len(anims)
    header['sfx_count'] = len(sound_list)
    header['vertex_count'] = vertex_count
    header['face_count'] = face_count
    header['texture_size'] = texture_size # Total bytes

    # 4. Write File
    with open(filepath, 'wb') as f:
        # Header
        header.tofile(f)
        
        # Faces
        faces_arr.tofile(f)
        
        # Vertices
        # The .car format expects 1-based indexing for vertex owners.
        # Our internal representation (from collect_bones_and_owners) is 0-based.
        # Create a temporary copy for modification.
        car_verts_arr = verts_arr.copy()
        # Increment all owner indices by 1.
        # A 0-based index of 0 (corresponding to the first bone) becomes 1 (the first bone's ID in .car).
        # This applies to all vertices, ensuring consistency with the .car format's 1-based bone indexing.
        car_verts_arr['owner'] += 1
        car_verts_arr.tofile(f)
        
        # Texture
        if texture_raw is not None:
            texture_raw.tofile(f)
            
        # Animations
        for anim in anims:
            # Name 32
            f.write(anim['name'].encode('ascii', 'ignore')[:32].ljust(32, b'\x00'))
            # KPS 4
            f.write(struct.pack('<I', anim['kps']))
            # Frames Count 4
            f.write(struct.pack('<I', len(anim['frames'])))
            # Frames Data
            for frame_data in anim['frames']:
                # frame_data is int16 array (N, 3)
                frame_data.tofile(f)
                
        # Sounds
        for snd in sound_list:
            # Name 32
            f.write(snd['name'].encode('ascii', 'ignore')[:32].ljust(32, b'\x00'))
            # Length 4
            f.write(struct.pack('<I', snd['length']))
            # Data
            f.write(snd['data'])
            
        # Cross Ref
        cross_ref.tofile(f)
        
    info(f"Finished .car export: {filepath}")