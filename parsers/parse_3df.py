
import numpy as np
import os

from . import validate_3df as validator
from ..utils import timed
from ..core.core import HEADER_DTYPE, FACE_DTYPE, VERTEX_DTYPE, BONE_DTYPE
from ..core.constants import TEXTURE_WIDTH

class ParserContext:
    def __init__(self):
        self.warnings = []

#@utils.timed('parse_3df_header')
def parse_3df_header(file):
    header = np.fromfile(file, dtype=HEADER_DTYPE, count=1)[0]
    if HEADER_DTYPE.itemsize != 16:
        raise ValueError("Incomplete header: expected 16 bytes (4 uint32).")
        
    # Texture width is always 256 and we have 2 bytes per pixel (256*2) so we divide the texture size with this to get texture height which can be variable.    
    texture_height = header['texture_size'] // (TEXTURE_WIDTH * 2) 
    return header, texture_height

#@timed('parse_3df_faces')
def parse_3df_faces(file, face_count, texture_height):
    
    faces = np.fromfile(file, dtype=FACE_DTYPE, count=face_count)
    faces['v'] = faces['v'][:, ::-1]  # Reverse vertex indices (v1,v2,v3 -> v3,v2,v1)
    faces['u_tex'] = faces['u_tex'][:, ::-1]  # Reverse U to match vertex order
    faces['v_tex'] = faces['v_tex'][:, ::-1]  # Reverse V to match vertex order
    uvs = np.empty((face_count * 3, 2), dtype=np.float32)
    uvs[:, 0] = (faces['u_tex'].astype(np.float32) / 255.0).ravel()
    uvs[:, 1] = 1.0 - (faces['v_tex'].astype(np.float32) / max((texture_height or 256), 1)).ravel()
    
    return faces, uvs

#@utils.timed('parse_3df_vertices')
def parse_3df_vertices(file, vertex_count):
    
    vertices = np.fromfile(file, dtype=VERTEX_DTYPE, count=vertex_count)
        
    return vertices

#@utils.timed('parse_3df_bones')
def parse_3df_bones(file, bone_count):
    bones = np.fromfile(file, dtype=BONE_DTYPE, count=bone_count)
    
    decoded = np.char.decode(bones['name'], 'ascii', errors='ignore')
    bone_names = np.char.rstrip(decoded, '\x00')
    for i, name in enumerate(bone_names):
        if not name:
            bone_names[i] = f"Bone_{i}"
        elif len(name.encode('ascii', errors='ignore')) > 32:
            bone_names[i] = name[:32]
            print(f"[Warning] Bone name '{name}' truncated to 32 characters.")
        elif not name.isascii():
            bone_names[i] = ''.join(c for c in name if c.isascii())
            print(f"[Warning] Bone name '{name}' contains non-ASCII characters; cleaned.")
    
    return bones, bone_names
    
#@utils.timed('parse_3df_texture')     
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
    print("=== PARSE PREVIEW ===")

    print("\n[Header]")
    print(header)

    print("\n[Vertices]")
    print("First:", vertices[0])
    print("Last :", vertices[-1])

    print("\n[Faces]")
    print("First:", faces[0])
    print("Last :", faces[-1])

    print("\n[UVs]")
    print("First:", uvs[0])
    print("Last :", uvs[-1])

    print("\n[Bones]")
    print("First:", bones[0])
    print("Last :", bones[-1])

    print("\n[Texture]")
    print(f"Shape: {texture.shape}, dtype: {texture.dtype}")

    # Find first and last non-zero pixel
    non_zero_indices = np.nonzero(np.any(texture != 0.0, axis=-1))[0]
    if non_zero_indices.size > 0:
        first_idx = non_zero_indices[0]
        last_idx = non_zero_indices[-1]
        print(f"First non-zero pixel at {first_idx}: {texture[first_idx]}")
        print(f"Last  non-zero pixel at {last_idx}: {texture[last_idx]}")
    else:
        print("Texture is completely zero (black/transparent).")

    print(f"\n[Texture Height Estimate] → {texture_height} px")
    print("=========================")"""
    
@timed('parse_3df')      
def parse_3df(filepath, validate=True, parse_texture=True):
    context = ParserContext()

    with open(filepath, 'rb') as file:
        header, texture_height = parse_3df_header(file)

        if validate:
            validator.validate_3df_header(header, filepath, context)
            faces, uvs = parse_3df_faces(file, header['face_count'], texture_height)
            faces = validator.validate_3df_faces(faces, header['face_count'], header['vertex_count'], texture_height, context)
            vertices = parse_3df_vertices(file, header['vertex_count'])
            vertices = validator.validate_3df_vertices(vertices, header['vertex_count'], header['bone_count'], context)
            bones, bone_names = parse_3df_bones(file, header['bone_count'])
            bones = validator.validate_3df_bones(bones, header['bone_count'], context)
            texture, texture_raw = parse_3df_texture(file, header['texture_size'])
            texture_raw = validator.validate_3df_texture(texture_raw, header['texture_size'], context) 
     
            # print_parse_preview(header, faces, uvs, vertices, bones, texture, texture_height)
            
        else:
            # Skip all validation, just parse raw
            faces, uvs = parse_3df_faces(file, header['face_count'], texture_height)
            vertices = parse_3df_vertices(file, header['vertex_count'])
            bones, bone_names = parse_3df_bones(file, header['bone_count'])
            texture, texture_raw = (None, None) if not parse_texture else parse_3df_texture(file, header['texture_size'], texture_height)
            if not parse_texture:
                file.seek(header['texture_size'], 1)  # Skip texture
            
            # print_parse_preview(header, faces, uvs, vertices, bones, texture, texture_height)

    return header, faces, uvs, vertices, bones, bone_names, texture, texture_height, context.warnings
