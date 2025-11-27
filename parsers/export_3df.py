import bpy
import numpy as np
from .. import utils
from ..core.core import HEADER_DTYPE, FACE_DTYPE, VERTEX_DTYPE, BONE_DTYPE
from ..core.constants import TEXTURE_WIDTH

import bpy_extras.io_utils

def gather_mesh_data(obj, export_matrix, export_textures=False, flip_u=False, flip_v=False, flip_handedness=True):
    """
    Gathers mesh data (vertices, faces, bones, texture) from a Blender object.
    Returns:
        (vertex_count, face_count, bone_count, texture_size, 
         faces_arr, verts_arr, bones_arr, texture_raw)
    """
    mesh = obj.data
    tmp_mesh = utils.triangulated_mesh_copy(mesh)
    is_tmp = (tmp_mesh != mesh)  # Track if we need to remove later

    try:
        vertex_count = len(tmp_mesh.vertices)
        face_count = len(tmp_mesh.polygons)

        # Bones + owners
        bone_names, bone_positions, bone_parents, vertex_owners = utils.collect_bones_and_owners(obj, export_matrix)
        bone_count = len(bone_names)

        # Texture
        texture_image, texture_height = (None, 0)
        texture_raw = None
        if export_textures:
            texture_image, texture_height = utils.find_texture_image(obj)
            if texture_image:
                if texture_image.size[0] != TEXTURE_WIDTH:
                    raise ValueError(f"Texture width {texture_image.size[0]} must be {TEXTURE_WIDTH} pixels.")
                texture_raw = utils.image_to_argb1555(texture_image)
                expected_size = TEXTURE_WIDTH * texture_height * 2
                if texture_height == 0 or len(texture_raw) != expected_size // 2:
                    raise ValueError(f"Texture data size mismatch: expected {expected_size // 2} pixels, got {len(texture_raw)} pixels.")
            else:
                raise ValueError("No suitable texture image found for export.")
        texture_size = TEXTURE_WIDTH * texture_height * 2 if texture_height else 0

        # Faces (vectorized for speed)
        faces_arr = np.zeros(face_count, dtype=FACE_DTYPE)

        # Get all face vertices at once
        face_verts_flat = np.empty(face_count * 3, dtype=np.uint32)
        tmp_mesh.polygons.foreach_get("vertices", face_verts_flat)
        faces_arr['v'] = face_verts_flat.reshape(face_count, 3)
        if flip_handedness:
            faces_arr['v'] = faces_arr['v'][:, ::-1]  # Reverse winding for left-handed mirror

        # UVs (vectorized)
        uv_layer = tmp_mesh.uv_layers.active
        if export_textures and not uv_layer:
            raise ValueError("No active UV layer found on mesh, but texture export is enabled.")
        if uv_layer and texture_height > 0:
            total_loops = face_count * 3
            all_uvs = np.empty(total_loops * 2, dtype=np.float32)
            uv_layer.data.foreach_get("uv", all_uvs)
            all_uvs = all_uvs.reshape(total_loops, 2)
            all_us, all_vs = all_uvs[:, 0].reshape(face_count, 3), all_uvs[:, 1].reshape(face_count, 3)
            all_vs = 1.0 - all_vs    
            
            if flip_handedness:
                all_us = all_us[:, ::-1]  # Reverse U to match new vertex order
                all_vs = all_vs[:, ::-1]  # Reverse V to match

            if flip_u:
                all_us = 1.0 - all_us
            if flip_v:
                all_vs = 1.0 - all_vs
            
            u_ints = np.clip(np.round(all_us * 255), 0, 255).astype(np.uint32)
            v_ints = np.clip(np.round(all_vs * texture_height), 0, texture_height).astype(np.uint32)
            faces_arr['u_tex'] = u_ints
            faces_arr['v_tex'] = v_ints
        else:
            faces_arr['u_tex'] = 0
            faces_arr['v_tex'] = 0

        # Flags (vectorized)
        faces_arr['flags'] = utils.get_face_attribute_int(tmp_mesh, "3df_flags", default=0)

        # Other face fields (zeroed by np.zeros)
        faces_arr['dmask'] = 0
        faces_arr['distant'] = 0
        faces_arr['next'] = 0
        faces_arr['group'] = 0
        faces_arr['reserv'] = np.zeros((face_count, 12), dtype=np.uint8)

        # Vertices (vectorized)
        verts_arr = np.zeros(vertex_count, dtype=VERTEX_DTYPE)

        # Get all coords at once
        verts_co_flat = np.empty(vertex_count * 3, dtype=np.float32)
        tmp_mesh.vertices.foreach_get("co", verts_co_flat)
        verts_co = verts_co_flat.reshape(vertex_count, 3)

        # Homogeneous transform
        verts_arr['coord'] = utils.apply_import_matrix(verts_co, export_matrix)
        verts_arr['owner'] = vertex_owners
        verts_arr['hide'] = 0

        # Bones (loop is fine, bone_count usually small)
        bones_arr = np.zeros(bone_count, dtype=BONE_DTYPE)
        for i, name in enumerate(bone_names):
            bones_arr['name'][i] = name.encode('ascii', 'ignore')[:32].ljust(32, b'\x00')
            bones_arr['pos'][i] = bone_positions[i]
            bones_arr['parent'][i] = bone_parents[i]
            bones_arr['hidden'][i] = 0
            
    finally:
        if is_tmp:
            bpy.data.meshes.remove(tmp_mesh, do_unlink=True)
            
    return vertex_count, face_count, bone_count, texture_size, faces_arr, verts_arr, bones_arr, texture_raw

def export_3df(filepath, obj, export_matrix, export_textures=False, flip_u=False, flip_v=False, flip_handedness=True):
    
    (vertex_count, face_count, bone_count, texture_size, 
     faces_arr, verts_arr, bones_arr, texture_raw) = gather_mesh_data(
        obj, export_matrix, export_textures, flip_u, flip_v, flip_handedness
    )

    # Header
    header = np.zeros(1, dtype=HEADER_DTYPE)
    header['vertex_count'] = vertex_count
    header['face_count'] = face_count
    header['bone_count'] = bone_count
    header['texture_size'] = texture_size

    # Write file
    with open(filepath, 'wb') as f:
        header.tofile(f)
        faces_arr.tofile(f)
        verts_arr.tofile(f)
        bones_arr.tofile(f)
        if texture_raw is not None:
            texture_raw.tofile(f)

    print(f"[Export] Finished: {filepath}")
