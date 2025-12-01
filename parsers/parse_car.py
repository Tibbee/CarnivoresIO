import numpy as np
import os
from . import validate as validator  # Reuse 3DF validations
from ..utils import timed, handle_car_owners
from ..core.core import FACE_DTYPE, VERTEX_DTYPE, CAR_HEADER_DTYPE  # Reuse dtypes
from ..core.constants import TEXTURE_WIDTH

class ParserContext:
    def __init__(self):
        self.warnings = []

def parse_car_header(file):
    header = np.fromfile(file, dtype=CAR_HEADER_DTYPE, count=1)[0]
    if CAR_HEADER_DTYPE.itemsize != 52:
        raise ValueError('Incomplete CAR header: expected 52 bytes.')
    # Sanitize string: split at first null byte to discard potential garbage
    model_name = header['model_name'].decode('ascii', errors='ignore').split('\x00')[0]
    # Loose check for expected suffix
    if not model_name.endswith('msc: #'):
        context = ParserContext()
        context.warnings.append(f"Unexpected model name format: '{model_name}' (expected suffix 'msc: #').")
    texture_height = header['texture_size'] // (TEXTURE_WIDTH * 2)
    return header, model_name, texture_height

# Reuse from parse_3df.py (identical layouts)
def parse_car_faces(file, face_count, texture_height, flip_handedness=True):
    from .parse_3df import parse_3df_faces  # Import to reuse
    return parse_3df_faces(file, face_count, texture_height, flip_handedness)

def parse_car_vertices(file, vertex_count):
    from .parse_3df import parse_3df_vertices  # Import to reuse
    return parse_3df_vertices(file, vertex_count)

# Reuse from parse_3df.py
def parse_car_texture(file, texture_size, texture_height):
    from .parse_3df import parse_3df_texture  # Import to reuse
    return parse_3df_texture(file, texture_size, texture_height)

def parse_car_animations(file, header, context):
    animations = []
    vcount = header['vertex_count']
    if header['ani_count'] > 0:
        print(f"[Debug] Starting animation parsing: {header['ani_count']} animations, {vcount} vertices")
        for anim_idx in range(header['ani_count']):
            # Read 32-byte name
            ani_name_raw = np.fromfile(file, dtype='S32', count=1)[0]
            # Sanitize string: split at first null byte
            ani_name = ani_name_raw.decode('ascii', errors='ignore').split('\x00')[0]
            if not ani_name:
                ani_name = f"Anim_{anim_idx}"
            # Read kps and frames_count
            ani_kps = np.fromfile(file, dtype='<u4', count=1)[0]
            frames_count = np.fromfile(file, dtype='<u4', count=1)[0]
            # Compute and read raw data
            data_size = frames_count * vcount * 6
            expected_count = data_size // 2  # int16 per coord
            raw_data = np.fromfile(file, dtype='<i2', count=expected_count)
            if raw_data.size != expected_count:
                context.warnings.append(f"Truncated data for {ani_name}: expected {expected_count}, got {raw_data.size}")
                print(f"[Debug] Warning: Truncated {ani_name} (skipping)")
                continue
            # Decode to absolute positions (float32)
            positions = raw_data.reshape(frames_count, vcount, 3).astype(np.float32) / 16.0
            animations.append({
                'name': ani_name,
                'kps': int(ani_kps),
                'frames_count': int(frames_count),
                'positions': positions
            })
            print(f"[Debug] Parsed anim '{ani_name}': {frames_count} frames, {ani_kps} kps, shape {positions.shape}")
        print(f"[Debug] Finished animations: {len(animations)} parsed, total frames {sum(a['frames_count'] for a in animations)}")
    else:
        print("[Debug] No animations (AniCount=0)")
    return animations

def skip_car_sounds_and_crossref(file, header, context):
    # Skip sounds (read headers, seek data)
    if header['sfx_count'] > 0:
        print(f"[Debug] Skipping {header['sfx_count']} sounds")
        for sfx_idx in range(header['sfx_count']):
            # Read 32-byte name + 4-byte length
            _ = np.fromfile(file, dtype='S32', count=1)  # name
            sfx_length = np.fromfile(file, dtype='<u4', count=1)[0]
            # Seek past data
            file.seek(sfx_length, 1)
            if sfx_idx < 3:  # Limit debug spam
                print(f"[Debug] Skipped sound {sfx_idx}: length {sfx_length} bytes")
    else:
        print("[Debug] No sounds (SfxCount=0)")

    # Skip cross-ref table (always 256 bytes)
    cross_ref_size = 256
    file.seek(cross_ref_size, 1)
    print(f"[Debug] Skipped cross-ref table: {cross_ref_size} bytes")

def parse_car_sounds_and_crossref(file, header, context, validate=True):
    """
    Returns:
        sounds   – list[dict]
        cross_ref – np.ndarray[int32] shape (64,)
    """
    sounds = []

    # ------------------- Sound blocks -------------------
    for sfx_idx in range(header['sfx_count']):
        name_raw = np.fromfile(file, dtype='S32', count=1)[0]
        # Sanitize string: split at first null byte
        name = name_raw.decode('ascii', errors='ignore').split('\x00')[0]
        if not name:
            name = f"Sound_{sfx_idx}"
            if validate:
                context.warnings.append(
                    f"Sound #{sfx_idx} has empty name; using placeholder."
                )

        length = np.fromfile(file, dtype='<u4', count=1)[0]
        expected_samples = length // 2
        data = np.fromfile(file, dtype='<i2', count=expected_samples)

        if data.dtype != np.int16:
            data = data.astype(np.int16)  # Ensure int16 for WAV

        if data.size != expected_samples:
            if validate:
                context.warnings.append(
                    f"Truncated sound '{name}': expected {expected_samples} samples, got {data.size}"
                )
            # Keep partial data — import step can skip if needed

        sounds.append({
            'name': name,
            'data': data,
            'length_bytes': length,
        })

    # ------------------- Cross-reference table -------------------
    cross_ref = np.fromfile(file, dtype='<i4', count=64)

    if header['ani_count'] > len(cross_ref):
        context.warnings.append(f"AniCount {header['ani_count']} exceeds cross-ref table size (64); extra animations will have no sound mapping.")

    # Always clamp invalid indices (safety repair)
    invalid = (cross_ref >= header['sfx_count']) | (cross_ref < -1)
    if np.any(invalid):
        cross_ref[invalid] = -1
        if validate:
            bad_count = np.count_nonzero(invalid)
            context.warnings.append(
                f"{bad_count} invalid sound indices in cross-ref table (clamped to -1)."
            )

    return sounds, cross_ref

@timed('parse_car')
def parse_car(filepath, validate=True, parse_texture=True, flip_handedness=True, import_sounds=True):
    context = ParserContext()
    with open(filepath, 'rb') as file:
        header, model_name, texture_height = parse_car_header(file)
        if validate:
            validator.validate_car_header(header, filepath, context)
        faces, uvs = parse_car_faces(file, header['face_count'], texture_height, flip_handedness)
        if validate:
            faces = validator.validate_3df_faces(faces, header['face_count'], header['vertex_count'], texture_height, context)
        vertices = parse_car_vertices(file, header['vertex_count'])
        if validate:
            vertices = validator.validate_car_vertices(vertices, header['vertex_count'], context)  # Now from validate.py
        vertices, bone_names = handle_car_owners(vertices, context)
        texture, texture_raw = (None, None) if not parse_texture else parse_car_texture(file, header['texture_size'], texture_height)
        if validate and texture_raw is not None:
            texture_raw = validator.validate_3df_texture(texture_raw, header['texture_size'], context)

        animations = parse_car_animations(file, header, context)
        
        sounds, cross_ref = parse_car_sounds_and_crossref(file, header, context, validate=validate)

    return (header, model_name, faces, uvs, vertices,
            bone_names, texture, texture_height,
            context.warnings, animations,
            sounds, cross_ref)