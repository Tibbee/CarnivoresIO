import numpy as np
import os
from . import validate as validator  # Reuse 3DF validations
from ..utils import timed, handle_car_owners
from ..core.core import FACE_DTYPE, VERTEX_DTYPE, CAR_HEADER_DTYPE  # Reuse dtypes
from ..core.constants import TEXTURE_WIDTH
from ..utils.logger import debug, warn, info, error

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
    used_names = {}  # Map base_name -> count
    
    if header['ani_count'] > 0:
        debug(f"Starting animation parsing: {header['ani_count']} animations, {vcount} vertices")
        for anim_idx in range(header['ani_count']):
            # Read 32-byte name
            ani_name_raw = np.fromfile(file, dtype='S32', count=1)[0]
            # Sanitize string: split at first null byte
            ani_name = ani_name_raw.decode('ascii', errors='ignore').split('\x00')[0]
            if not ani_name:
                ani_name = f"Anim_{anim_idx}"
            
            # Enforce Uniqueness
            if ani_name in used_names:
                count = used_names[ani_name]
                used_names[ani_name] += 1
                unique_name = f"{ani_name}_{count}" # e.g., Ms_die_1, Ms_die_2
                debug(f"Renaming duplicate animation '{ani_name}' to '{unique_name}'")
                ani_name = unique_name
            else:
                used_names[ani_name] = 1
            
            # Read kps and frames_count
            ani_kps = np.fromfile(file, dtype='<u4', count=1)[0]
            frames_count = np.fromfile(file, dtype='<u4', count=1)[0]
            # Compute and read raw data
            data_size = frames_count * vcount * 6
            expected_count = data_size // 2  # int16 per coord
            raw_data = np.fromfile(file, dtype='<i2', count=expected_count)
            if raw_data.size != expected_count:
                context.warnings.append(f"Truncated data for {ani_name}: expected {expected_count}, got {raw_data.size}")
                warn(f"Truncated {ani_name} (skipping)")
                continue
            # Decode to absolute positions (float32)
            positions = raw_data.reshape(frames_count, vcount, 3).astype(np.float32) / 16.0
            animations.append({
                'name': ani_name,
                'kps': int(ani_kps),
                'frames_count': int(frames_count),
                'positions': positions
            })
            debug(f"Parsed anim '{ani_name}': {frames_count} frames, {ani_kps} kps, shape {positions.shape}")
        debug(f"Finished animations: {len(animations)} parsed, total frames {sum(a['frames_count'] for a in animations)}")
    else:
        debug("No animations (AniCount=0)")
    return animations

def skip_car_sounds_and_crossref(file, header, context):
    # Skip sounds (read headers, seek data)
    if header['sfx_count'] > 0:
        debug(f"Skipping {header['sfx_count']} sounds")
        for sfx_idx in range(header['sfx_count']):
            # Read 32-byte name + 4-byte length
            _ = np.fromfile(file, dtype='S32', count=1)  # name
            sfx_length = np.fromfile(file, dtype='<u4', count=1)[0]
            # Repair odd length to prevent misaligned seek
            if sfx_length % 2 != 0:
                context.warnings.append(
                    f"Skipped sound #{sfx_idx} has odd byte length {sfx_length}; repairing to {sfx_length - 1}."
                )
                sfx_length -= 1
            # Seek past data
            file.seek(sfx_length, 1)
            if sfx_idx < 3:  # Limit debug spam
                debug(f"Skipped sound {sfx_idx}: length {sfx_length} bytes")
    else:
        debug("No sounds (SfxCount=0)")

    # Skip cross-ref table (always 256 bytes)
    cross_ref_size = 256
    file.seek(cross_ref_size, 1)
    debug(f"Skipped cross-ref table: {cross_ref_size} bytes")

def parse_car_sounds_and_crossref(file, header, context, validate=True):
    """
    Returns:
        sounds   – list[dict]
        cross_ref – np.ndarray[int32] shape (64,)
    """
    import io
    sounds = []
    used_names = {} # Map name -> count

    # Determine remaining file size for boundary checks
    current_pos = file.tell()
    try:
        file.seek(0, io.SEEK_END)
        file_size = file.tell()
        file.seek(current_pos, io.SEEK_SET)
    except Exception:
        file_size = None

    # ------------------- Sound blocks -------------------
    for sfx_idx in range(header['sfx_count']):
        # Validate each sound header fits
        if file_size is not None and file.tell() + 36 > file_size:
            context.warnings.append(
                f"Sound block #{sfx_idx} header (36 bytes) exceeds file boundary; stopping sound parsing."
            )
            break

        name_raw = np.fromfile(file, dtype='S32', count=1)[0]
        name = name_raw.decode('ascii', errors='ignore').split('\x00')[0]
        if not name:
            name = f"Sound_{sfx_idx}"
            if validate:
                context.warnings.append(
                    f"Sound #{sfx_idx} has empty name; using placeholder."
                )

        if name in used_names:
            count = used_names[name]
            used_names[name] += 1
            unique_name = f"{name}_{count}"
            if validate:
                context.warnings.append(f"Renaming duplicate sound '{name}' to '{unique_name}'")
            name = unique_name
        else:
            used_names[name] = 1

        length = np.fromfile(file, dtype='<u4', count=1)[0]

        # Reject odd byte length — would misalign all subsequent reads
        if length % 2 != 0:
            if validate:
                context.warnings.append(
                    f"Sound '{name}' has odd byte length {length}; repair to {length - 1} and skip extra byte."
                )
            length -= 1
            file.seek(1, io.SEEK_CUR)  # consume the misaligned extra byte

        # Check against remaining file size
        if file_size is not None and file.tell() + length > file_size:
            remaining = file_size - file.tell()
            context.warnings.append(
                f"Sound '{name}' declared length {length} exceeds remaining file size ({remaining}); truncating."
            )
            length = max(0, remaining)
            if length < 2:
                continue  # no meaningful data left

        expected_samples = length // 2
        data = np.fromfile(file, dtype='<i2', count=expected_samples)

        if data.dtype != np.int16:
            data = data.astype(np.int16)

        if data.size != expected_samples:
            if validate:
                context.warnings.append(
                    f"Truncated sound '{name}': expected {expected_samples} samples, got {data.size}"
                )

        sounds.append({
            'name': name,
            'data': data,
            'length_bytes': length,
        })

    # ------------------- Cross-reference table -------------------
    # Verify 256 bytes remain
    if file_size is not None and file.tell() + 256 > file_size:
        remaining = max(0, file_size - file.tell())
        context.warnings.append(
            f"Only {remaining} bytes remain for cross-ref table (expected 256); reading partial table."
        )
        cross_ref = np.fromfile(file, dtype='<i4', count=max(0, remaining // 4))
        if cross_ref.size < 64:
            cross_ref = np.pad(cross_ref, (0, 64 - cross_ref.size), constant_values=-1)
    else:
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
        vertices, bone_names, owner_mapping = handle_car_owners(vertices, context)
        texture, texture_raw = (None, None) if not parse_texture else parse_car_texture(file, header['texture_size'], texture_height)
        if validate and texture_raw is not None:
            texture_raw = validator.validate_3df_texture(texture_raw, header['texture_size'], context)

        animations = parse_car_animations(file, header, context)
        
        sounds, cross_ref = parse_car_sounds_and_crossref(file, header, context, validate=validate)

    return (header, model_name, faces, uvs, vertices,
            bone_names, owner_mapping.raw_per_vertex, texture, texture_height,
            context.warnings, animations,
            sounds, cross_ref)
