import bpy
import numpy as np
import aud
import os
import struct

from ..core.core import CAR_HEADER_DTYPE, VERTEX_DTYPE, FACE_DTYPE
from ..core.constants import TEXTURE_WIDTH
from .. import utils
from .export_3df import gather_mesh_data

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
                print(f"Warning: Could not load factory for sound {sound_datablock.name}")
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
        print(f"Error converting sound {sound_datablock.name}: {e}")
        return None, 0

def gather_car_animations(obj, export_matrix, vertex_count):
    """
    Collects animation data by iterating over NLA strips or the active action.
    Returns: list of dicts {'name': str, 'kps': int, 'frames': list_of_numpy_arrays, 'sound_ptr': Sound}
    """
    animations = []
    context = bpy.context
    scene = context.scene
    
    # --- 1. Find Animation Source ---
    anim_data = None
    source_label = ""
    
    # Check Shape Keys (Priority for .car workflow)
    if obj.data.shape_keys and obj.data.shape_keys.animation_data:
        anim_data = obj.data.shape_keys.animation_data
        source_label = "Shape Keys"
    # Check Parent Armature (Rigged workflow)
    elif obj.parent and obj.parent.type == 'ARMATURE' and obj.parent.animation_data:
        anim_data = obj.parent.animation_data
        source_label = f"Parent Armature ({obj.parent.name})"
    # Check Object (Legacy/Simple workflow)
    elif obj.animation_data:
        anim_data = obj.animation_data
        source_label = f"Object ({obj.name})"
        
    if not anim_data:
        print("[Export] No animation data found on Object, ShapeKeys, or Parent Armature.")
        return []
        
    print(f"[Export] Found animation source: {source_label}")

    # --- 2. Identify Actions to Process ---
    actions_to_process = [] # (Action, Name, FrameStart, FrameEnd)
    
    if anim_data.nla_tracks:
        # Iterate tracks in reverse (Top-most track first) to match visual order and file order preference
        for track in reversed(list(anim_data.nla_tracks)):
            if track.mute: continue
            for strip in track.strips:
                # Store relevant data before we mute tracks
                actions_to_process.append({
                    'action': strip.action,
                    'name': strip.action.name if strip.action else strip.name, # Use action name preferably
                    'start': int(strip.action.frame_range[0]) if strip.action else int(strip.frame_start),
                    'end': int(strip.action.frame_range[1]) if strip.action else int(strip.frame_end)
                })

    # Fallback: Active Action (if no NLA strips found or processed)
    if not actions_to_process and anim_data.action:
        actions_to_process.append({
            'action': anim_data.action,
            'name': anim_data.action.name,
            'start': int(anim_data.action.frame_range[0]),
            'end': int(anim_data.action.frame_range[1])
        })

    if not actions_to_process:
        print("[Export] No actions/strips found to export.")
        return []

    # --- STATE MANAGEMENT ---
    original_frame = scene.frame_current
    original_action = anim_data.action
    original_use_nla = anim_data.use_nla
    
    # Shape Key Pinning Handling
    original_show_only_shape_key = False
    if obj.type == 'MESH' and obj.show_only_shape_key:
        original_show_only_shape_key = True
        obj.show_only_shape_key = False # Disable pinning to allow animation
        print("[Export] Temporarily disabled Shape Key Pinning for bake.")

    # Disable NLA to force Action playback
    anim_data.use_nla = False

    # Store Modifier States & Force Enable Deformers
    mod_states = {}
    for mod in obj.modifiers:
        mod_states[mod.name] = mod.show_viewport
        if mod.type in {'ARMATURE', 'HOOK'}: 
            mod.show_viewport = True # Force enable deformers
        else:
            mod.show_viewport = False # Disable others

    print(f"[Export] Processing {len(actions_to_process)} animations...")

    try:
        for entry in actions_to_process:
            action = entry['action']
            if not action: 
                print(f"[Export] Skipping entry {entry['name']} (No Action)")
                continue
            
            # Activate Action
            anim_data.action = action
            
            # Check for Sound
            snd_ptr = getattr(action, 'carnivores_sound_ptr', None)
            if snd_ptr:
                print(f"[Export] Animation '{action.name}' has linked sound: {snd_ptr.name}")
            else:
                print(f"[Export] Animation '{action.name}' has NO linked sound.")
            
            # Determine KPS (Frames per second)
            # Priority: 1. Custom Property (imported) 2. Scene FPS (created in Blender)
            kps = action.get("carnivores_kps", int(scene.render.fps))

            start = entry['start']
            end = entry['end']
            frames_data = []
            
            print(f"[Export] Baking Action: {action.name} ({start}-{end})")

            for frame in range(start, end + 1):
                scene.frame_set(frame)
                context.view_layer.update() # Ensure depsgraph is fully updated
                
                # Evaluate mesh (Deformed by Armature/Action)
                depsgraph = context.evaluated_depsgraph_get()
                eval_obj = obj.evaluated_get(depsgraph)
                
                # Use to_mesh() to get the deformed geometry with modifiers applied
                mesh = eval_obj.to_mesh()
                
                try:
                    count = len(mesh.vertices)
                    if count != vertex_count:
                        print(f"[Export] Error: Frame {frame} of '{action.name}' has {count} vertices, expected {vertex_count} (Base). Skipping animation.")
                        eval_obj.to_mesh_clear()
                        break
                    
                    # Bulk get coords
                    verts_co_flat = np.empty(count * 3, dtype=np.float32)
                    mesh.vertices.foreach_get("co", verts_co_flat)
                    verts_co = verts_co_flat.reshape(count, 3)
                    
                    # Debug: Print first vertex position to verify animation
                    if frame == start:
                        print(f"         Frame {frame} v[0]: {verts_co[0]}")
                    elif frame == start + 1:
                        print(f"         Frame {frame} v[0]: {verts_co[0]}")

                    # Transform
                    transformed_co = utils.apply_import_matrix(verts_co, export_matrix)
                    
                    # Quantize to fixed point 16.0
                    quantized = np.clip(transformed_co * 16.0, -32768, 32767).astype(np.int16)
                    frames_data.append(quantized)
                
                finally:
                    eval_obj.to_mesh_clear()

            if len(frames_data) == (end - start + 1):
                # Static Check
                is_static = True
                if len(frames_data) > 1:
                    first_frame = frames_data[0]
                    for f_data in frames_data[1:]:
                        if not np.array_equal(f_data, first_frame):
                            is_static = False
                            break
                if is_static and len(frames_data) > 1:
                    print(f"[Export] Warning: Animation '{action.name}' appears to be static (all frames identical).")

                # Strip Blender's "_Action" suffix for cleaner names in the .car file
                clean_name = action.name.replace("_Action", "")

                animations.append({
                    'name': clean_name,
                    'kps': kps,
                    'frames': frames_data,
                    'sound_ptr': snd_ptr
                })
            # else: loop broke due to error

    except Exception as e:
        print(f"[Export] Critical Error during animation bake: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # --- RESTORE STATE ---
        scene.frame_set(original_frame)
        if anim_data: # Check if we found one
            anim_data.action = original_action
            anim_data.use_nla = original_use_nla
        
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
            print("Warning: More than 64 animations, truncation will occur in cross-ref.")
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
        
    print(f"[Export] Finished .car export: {filepath}")
