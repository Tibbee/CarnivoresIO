import numpy as np
import os
from . import validate as validator  # Reuse 3DF validations
from ..utils import timed
from ..core.core import FACE_DTYPE, VERTEX_DTYPE, CAR_HEADER_DTYPE  # Reuse dtypes
from ..core.constants import TEXTURE_WIDTH

class ParserContext:
    def __init__(self):
        self.warnings = []

def parse_car_header(file):
    header = np.fromfile(file, dtype=CAR_HEADER_DTYPE, count=1)[0]
    if CAR_HEADER_DTYPE.itemsize != 52:
        raise ValueError('Incomplete CAR header: expected 52 bytes.')
    model_name = header['model_name'].decode('ascii', errors='ignore').rstrip('\x00')
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

def handle_car_owners(vertices, context):
    """
    Handle CAR-specific owner indexing: detect first non-zero owner, offset to 0-based,
    generate dummy bone names starting from original min, and warn as needed.
    Returns: (vertices with adjusted owners, bone_names array)
    """
    non_zero_owners = vertices['owner'][vertices['owner'] > 0]
    if non_zero_owners.size == 0:
        return vertices, np.array([], dtype='U32')
    
    min_non_zero = np.min(non_zero_owners)
    max_owner = int(np.max(vertices['owner']))
    
    # Offset: subtract min_non_zero from non-zero owners (shifts to 0-based)
    vertices['owner'][vertices['owner'] > 0] -= min_non_zero
    context.warnings.append(f"Owner indices started at {min_non_zero}; offset by -{min_non_zero} to start at index 0.")
    
    # Clamp negatives (safe guard) and recompute max
    vertices['owner'] = np.clip(vertices['owner'], 0, None)
    max_owner_adjusted = int(np.max(vertices['owner']))
    # Name starting from original min_non_zero (e.g., index 0 -> "CarBone_{min_non_zero}")
    bone_names = np.array([f"CarBone_{i + min_non_zero}" for i in range(max_owner_adjusted + 1)], dtype='U32')
    context.warnings.append(f"Created {len(bone_names)} dummy bones for vertex owners starting from {min_non_zero} (no positions/parents in .CAR).")
    
    return vertices, bone_names

@timed('parse_car')
def parse_car(filepath, validate=True, parse_texture=True, flip_handedness=True):
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
        # Skip animations/sounds/cross-ref: seek to end
        file.seek(0, 2)
    return header, model_name, faces, uvs, vertices, bone_names, texture, texture_height, context.warnings
