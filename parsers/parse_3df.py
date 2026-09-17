import numpy as np
import os

from . import validate as validator
from ..utils import timed
from ..core.core import HEADER_DTYPE, FACE_DTYPE, VERTEX_DTYPE, BONE_DTYPE
from ..core.constants import TEXTURE_WIDTH
from ..utils.logger import debug, warn, info, error
from ..utils.performance import current_session

class ParserContext:
    def __init__(self):
        self.warnings = []

def _warn(context, message):
    """Record a warning in the parser context and echo it to the log."""
    if message not in context.warnings:
        context.warnings.append(message)
    warn(message)

@timed('parse_3df.header')
def parse_3df_header(file):
    parsed = np.fromfile(file, dtype=HEADER_DTYPE, count=1)
    if parsed.size != 1:
        raise ValueError(f"Incomplete 3DF header: expected {HEADER_DTYPE.itemsize} bytes.")
    header = parsed[0]
    if HEADER_DTYPE.itemsize != 16:
        raise ValueError("Internal 3DF header definition must be 16 bytes.")
        
    # Texture width is always 256 and we have 2 bytes per pixel (256*2) so we divide the texture size with this to get texture height which can be variable.    
    texture_height = header['texture_size'] // (TEXTURE_WIDTH * 2) 
    return header, texture_height

@timed('parse_3df.faces')
def parse_3df_faces(file, face_count, texture_height, flip_handedness=True):
    
    faces = np.fromfile(file, dtype=FACE_DTYPE, count=face_count)
    if flip_handedness:
        faces['v'] = faces['v'][:, ::-1]  # Reverse winding for right-handed import
        faces['u_tex'] = faces['u_tex'][:, ::-1]  # Reverse U to match
        faces['v_tex'] = faces['v_tex'][:, ::-1]  # Reverse V to match
    uvs = np.empty((face_count * 3, 2), dtype=np.float32)
    uvs[:, 0] = (faces['u_tex'].astype(np.float32) / 255.0).ravel()
    uvs[:, 1] = 1.0 - (faces['v_tex'].astype(np.float32) / max((texture_height or 256), 1)).ravel()
    
    return faces, uvs

@timed('parse_3df.vertices')
def parse_3df_vertices(file, vertex_count):
    
    vertices = np.fromfile(file, dtype=VERTEX_DTYPE, count=vertex_count)
        
    return vertices

@timed('parse_3df.bones')
def parse_3df_bones(file, bone_count, context=None):
    bones = np.fromfile(file, dtype=BONE_DTYPE, count=bone_count)
    
    decoded = np.char.decode(bones['name'], 'ascii', errors='ignore')
    # Single shared rule: split at the first NUL byte (embedded padding must
    # not survive into Blender names) and truncate by bytes, matching what
    # validation and re-export do with the same field.
    bone_names = np.array([name.split('\x00', 1)[0] for name in decoded], dtype='U32')
    for i, name in enumerate(bone_names):
        if not name:
            bone_names[i] = f"Bone_{i}"
        else:
            clean, truncated = validator.truncate_serialized_name(name)
            if truncated:
                bone_names[i] = clean
                message = f"Bone name '{name}' exceeds {validator.SERIALIZED_NAME_BYTES} bytes; truncated."
                warn(message)
                if context is not None:
                    _warn(context, message)
            if not name.isascii():
                cleaned = ''.join(c for c in name if c.isascii())
                if not cleaned:
                    cleaned = f"Bone_{i}"
                    message = f"Bone #{i} name contains no ASCII characters; using placeholder."
                else:
                    message = f"Bone name '{name}' contains non-ASCII characters; cleaned to '{cleaned}'."
                bone_names[i] = validator.truncate_serialized_name(cleaned)[0]
                warn(message)
                if context is not None:
                    _warn(context, message)
    
    return bones, bone_names
    
@timed('parse_3df.texture')
def parse_3df_texture(file, texture_size, texture_height):
    
    expected_length = texture_size // 2
    texture_raw = np.fromfile(file, dtype='<u2', count=expected_length)
    if texture_raw.size != expected_length:
        raise ValueError(f"Texture data length {texture_raw.size} does not match expected {expected_length}.")
    if texture_size % (TEXTURE_WIDTH * 2) != 0:
        raise ValueError(f"Texture size {texture_size} not divisible by {TEXTURE_WIDTH * 2} (invalid height).")
    
    # Decode ARGB1555 to RGBA
    r = np.bitwise_and(np.right_shift(texture_raw, 10), 0x1F).astype(np.float32) / 31.0
    g = np.bitwise_and(np.right_shift(texture_raw, 5), 0x1F).astype(np.float32) / 31.0
    b = np.bitwise_and(texture_raw, 0x1F).astype(np.float32) / 31.0
    texture = np.stack([r, g, b, np.zeros_like(r)], axis=-1)
    
    # Flip pixels vertically for Blender (assumes .3df top-down)
    if texture_height > 0:
        texture = texture.reshape(texture_height, TEXTURE_WIDTH, 4)[::-1].ravel()
    
    return texture, texture_raw
    
#@utils.timed('print_parse_preview') 
"""def print_parse_preview(header, faces, uvs, vertices, bones, texture, texture_height):
    debug("=== PARSE PREVIEW ===")

    debug("\n[Header]")
    debug(header)

    debug("\n[Vertices]")
    debug(f"First: {vertices[0]}")
    debug(f"Last : {vertices[-1]}")

    debug("\n[Faces]")
    debug(f"First: {faces[0]}")
    debug(f"Last : {faces[-1]}")

    debug("\n[UVs]")
    debug(f"First: {uvs[0]}")
    debug(f"Last : {uvs[-1]}")

    debug("\n[Bones]")
    debug(f"First: {bones[0]}")
    debug(f"Last : {bones[-1]}")

    debug("\n[Texture]")
    debug(f"Shape: {texture.shape}, dtype: {texture.dtype}")

    # Find first and last non-zero pixel
    non_zero_indices = np.nonzero(np.any(texture != 0.0, axis=-1))[0]
    if non_zero_indices.size > 0:
        first_idx = non_zero_indices[0]
        last_idx = non_zero_indices[-1]
        debug(f"First non-zero pixel at {first_idx}: {texture[first_idx]}")
        debug(f"Last  non-zero pixel at {last_idx}: {texture[last_idx]}")
    else:
        debug("Texture is completely zero (black/transparent).")

    debug(f"\n[Texture Height Estimate] → {texture_height} px")
    debug("=========================")"""
    
@timed('parse_3df')      
def parse_3df(filepath, validate=True, parse_texture=True, flip_handedness=True):
    context = ParserContext()

    with open(filepath, 'rb') as file:
        header, texture_height = parse_3df_header(file)
        session = current_session()
        if session:
            session.add_metadata(
                format="3DF",
                file_size=os.path.getsize(filepath),
                vertices=int(header['vertex_count']),
                faces=int(header['face_count']),
                bones=int(header['bone_count']),
                texture_height=int(texture_height),
                parse_texture=bool(parse_texture),
            )

        # Structural validation is always active. The user-facing option only
        # enables additional engine/tool compatibility diagnostics.
        validator.validate_3df_header(header, filepath, context, compatibility=validate)

        faces, uvs = parse_3df_faces(
            file,
            header['face_count'],
            texture_height,
            flip_handedness=flip_handedness,
        )
        faces = validator.validate_3df_faces(
            faces,
            header['face_count'],
            header['vertex_count'],
            texture_height,
            context,
            compatibility=validate,
        )

        vertices = parse_3df_vertices(file, header['vertex_count'])
        vertices = validator.validate_3df_vertices(
            vertices,
            header['vertex_count'],
            header['bone_count'],
            context,
            compatibility=validate,
        )

        bones, bone_names = parse_3df_bones(file, header['bone_count'], context=context)
        bones = validator.validate_3df_bones(
            bones,
            header['bone_count'],
            context,
            compatibility=validate,
        )

        if parse_texture:
            if int(header['texture_size']) == 0:
                # Untextured model: no texture payload follows. Creating a
                # zero-height image datablock would be degenerate.
                texture, texture_raw = None, None
                _warn(context, "Model has no texture data (texture_size = 0); texture import skipped.")
            else:
                texture, texture_raw = parse_3df_texture(file, header['texture_size'], texture_height)
                texture_raw = validator.validate_3df_texture(
                    texture_raw,
                    header['texture_size'],
                    context,
                    compatibility=validate,
                )
        else:
            texture, texture_raw = None, None
            file.seek(header['texture_size'], 1)

    return header, faces, uvs, vertices, bones, bone_names, texture, texture_height, context.warnings