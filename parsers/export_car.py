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
        factory = factory.resample(22050, high_quality=True)
        # Mixdown: Mono
        factory = factory.mixdown(1) # 1 channel
        
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
    Returns: list of dicts {'name': str, 'kps': int, 'frames': list_of_numpy_arrays, 'sound_name': str}
    """
    animations = []
    context = bpy.context
    scene = context.scene
    
    # Determine source of animations
    # Priority: Selected NLA Strips -> All NLA Strips -> Active Action
    actions_to_process = [] # (Action, Name, FrameStart, FrameEnd)
    
    anim_data = obj.animation_data
    if anim_data and anim_data.nla_tracks:
        for track in anim_data.nla_tracks:
            if track.mute: continue
            for strip in track.strips:
                # We use the strip's action duration
                # Note: Carnivores anims are usually baked actions. 
                # We'll export the action itself, not the NLA strip placement.
                actions_to_process.append(strip)

    # Fallback to active action if no NLA
    if not actions_to_process and anim_data and anim_data.action:
        # Create a dummy strip-like object
        class DummyStrip:
            action = anim_data.action
            name = anim_data.action.name
            frame_start = anim_data.action.frame_range[0]
            frame_end = anim_data.action.frame_range[1]
        actions_to_process.append(DummyStrip())

    if not actions_to_process:
        return []

    # Store current state
    original_frame = scene.frame_current
    original_action = anim_data.action if anim_data else None
    
    # We need to mute NLA to play individual actions cleanly
    # Or just use the action directly on the object
    
    try:
        # Force Object Mode for evaluation
        # if obj.mode != 'OBJECT':
        #     bpy.ops.object.mode_set(mode='OBJECT')

        for strip in actions_to_process:
            action = strip.action
            if not action: continue
            
            # Prepare Action
            anim_data.action = action
            
            # Determine FPS / KPS
            fps = scene.render.fps
            kps = fps * 1000 if fps < 100 else fps # Some heuristics for KPS? 
            # Actually Carnivores KPS is often FrameRate. parse_car reads it as uint32. 
            # Let's use scene FPS.
            kps = int(fps) 

            start = int(action.frame_range[0])
            end = int(action.frame_range[1])
            frames_data = []
            
            print(f"[Export] Processing Action: {action.name} ({start}-{end})")

            for frame in range(start, end + 1):
                scene.frame_set(frame)
                
                # Evaluate mesh
                depsgraph = context.evaluated_depsgraph_get()
                eval_obj = obj.evaluated_get(depsgraph)
                
                # Extract vertices
                # We need them in the same order as the base mesh
                # Assuming topology doesn't change
                mesh = eval_obj.data
                
                # Apply export matrix
                count = len(mesh.vertices)
                if count != vertex_count:
                    print(f"Warning: Frame {frame} of {action.name} has {count} verts, expected {vertex_count}. Skipping frame.")
                    # Fill with zeros or previous frame to prevent crash?
                    # Better to just abort this animation
                    break
                
                # Bulk get coords
                verts_co_flat = np.empty(count * 3, dtype=np.float32)
                mesh.vertices.foreach_get("co", verts_co_flat)
                verts_co = verts_co_flat.reshape(count, 3)
                
                # Transform
                transformed_co = utils.apply_import_matrix(verts_co, export_matrix)
                
                # Quantize to fixed point 16.0
                # Carnivores: integer = float * 16.0
                quantized = np.clip(transformed_co * 16.0, -32768, 32767).astype(np.int16)
                
                frames_data.append(quantized)

            if len(frames_data) == (end - start + 1):
                animations.append({
                    'name': action.name,
                    'kps': kps,
                    'frames': frames_data,
                    'sound_ptr': getattr(action, 'carnivores_sound_ptr', None)
                })
            else:
                print(f"Skipping animation {action.name} due to vertex count mismatch.")

    finally:
        # Restore
        scene.frame_set(original_frame)
        if anim_data:
            anim_data.action = original_action

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
        # .CAR vertex format: (x,y,z, owner, hide). Same as .3DF but order might matter.
        # parse_car uses same dtype.
        verts_arr.tofile(f)
        
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
