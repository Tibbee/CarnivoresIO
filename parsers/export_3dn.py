import bpy
import numpy as np
from .. import utils
from ..core.core import THREEDN_HEADER_DTYPE, THREEDN_VERTEX_DTYPE, THREEDN_FACE_DTYPE, BONE_DTYPE
from ..utils.logger import info, debug, warn, error

def gather_3dn_data(obj, export_matrix, model_name="", has_sprite=False, sprite_name="", flip_u=False, flip_v=False, flip_handedness=True):
    """
    Gathers mesh data (vertices, faces, bones) from a Blender object for 3DN format.
    """
    mesh = obj.data
    tmp_mesh = utils.triangulated_mesh_copy(mesh)
    is_tmp = (tmp_mesh != mesh)

    try:
        vertex_count = len(tmp_mesh.vertices)
        face_count = len(tmp_mesh.polygons)

        # Bones + owners
        bone_names, bone_positions, bone_parents, vertex_owners = utils.collect_bones_and_owners(obj, export_matrix)
        bone_count = len(bone_names)

        # 3DN doesn't store texture size in header, but we need texture height for pixel UVs
        # We assume 256x256 or similar if not found, but spec says pixel values.
        # CAR/3DF usually use 256 width.
        texture_image, texture_height = utils.find_texture_image(obj)
        if not texture_height:
            texture_height = 256
            warn("No texture found for 3DN export, assuming 256 height for UVs.")

        # Vertices (vectorized)
        verts_arr = np.zeros(vertex_count, dtype=THREEDN_VERTEX_DTYPE)
        verts_co_flat = np.empty(vertex_count * 3, dtype=np.float32)
        tmp_mesh.vertices.foreach_get("co", verts_co_flat)
        verts_co = verts_co_flat.reshape(vertex_count, 3)

        full_matrix = export_matrix
        if obj.parent and obj.parent.type == 'ARMATURE':
            mesh_to_arm = np.array(obj.parent.matrix_world.inverted() @ obj.matrix_world)
            full_matrix = export_matrix @ mesh_to_arm

        verts_arr['coord'] = utils.apply_import_matrix(verts_co, full_matrix)
        verts_arr['owner'] = vertex_owners

        # Faces (vectorized)
        faces_arr = np.zeros(face_count, dtype=THREEDN_FACE_DTYPE)
        
        face_verts_flat = np.empty(face_count * 3, dtype=np.uint32)
        tmp_mesh.polygons.foreach_get("vertices", face_verts_flat)
        face_verts = face_verts_flat.reshape(face_count, 3)
        if flip_handedness:
            face_verts = face_verts[:, ::-1]
        
        faces_arr['v1'] = face_verts[:, 0]
        faces_arr['v2'] = face_verts[:, 1]
        faces_arr['v3'] = face_verts[:, 2]

        # UVs
        uv_layer = tmp_mesh.uv_layers.active
        if uv_layer:
            total_loops = face_count * 3
            all_uvs = np.empty(total_loops * 2, dtype=np.float32)
            uv_layer.data.foreach_get("uv", all_uvs)
            all_uvs = all_uvs.reshape(total_loops, 2)
            all_us, all_vs = all_uvs[:, 0].reshape(face_count, 3), all_uvs[:, 1].reshape(face_count, 3)
            all_vs = 1.0 - all_vs

            if flip_handedness:
                all_us = all_us[:, ::-1]
                all_vs = all_vs[:, ::-1]

            if flip_u:
                all_us = 1.0 - all_us
            if flip_v:
                all_vs = 1.0 - all_vs

            u_pixels = np.clip(np.round(all_us * 255), 0, 255).astype(np.int16)
            v_pixels = np.clip(np.round(all_vs * texture_height), 0, texture_height).astype(np.int16)
            
            faces_arr['tax'] = u_pixels[:, 0]
            faces_arr['tay'] = v_pixels[:, 0]
            faces_arr['tbx'] = u_pixels[:, 1]
            faces_arr['tby'] = v_pixels[:, 1]
            faces_arr['tcx'] = u_pixels[:, 2]
            faces_arr['tcy'] = v_pixels[:, 2]

        # Flags
        faces_arr['flags'] = utils.get_face_attribute_int(tmp_mesh, "3df_flags", default=0)

        # Bones
        bones_arr = np.zeros(bone_count, dtype=BONE_DTYPE)
        for i, name in enumerate(bone_names):
            bones_arr['name'][i] = name.encode('ascii', 'ignore')[:32].ljust(32, b'\x00')
            bones_arr['pos'][i] = bone_positions[i]
            bones_arr['parent'][i] = bone_parents[i]
            bones_arr['hidden'][i] = 0

    finally:
        if is_tmp:
            bpy.data.meshes.remove(tmp_mesh, do_unlink=True)

    return vertex_count, face_count, bone_count, verts_arr, faces_arr, bones_arr

def export_3dn(filepath, obj, export_matrix, model_name="", has_sprite=False, sprite_name="", flip_u=False, flip_v=False, flip_handedness=True):
    
    (vertex_count, face_count, bone_count, 
     verts_arr, faces_arr, bones_arr) = gather_3dn_data(
        obj, export_matrix, model_name, has_sprite, sprite_name, flip_u, flip_v, flip_handedness
    )

    header = np.zeros(1, dtype=THREEDN_HEADER_DTYPE)
    header['vertex_count'] = vertex_count
    header['face_count'] = face_count
    header['bone_count'] = bone_count
    header['model_name'] = model_name.encode('ascii', 'ignore')[:32].ljust(32, b'\x00')
    header['has_sprite'] = 1 if has_sprite else 0

    with open(filepath, 'wb') as f:
        header.tofile(f)
        if has_sprite:
            sprite_name_encoded = sprite_name.encode('ascii', 'ignore')[:32].ljust(32, b'\x00')
            f.write(sprite_name_encoded)
        
        verts_arr.tofile(f)
        faces_arr.tofile(f)
        bones_arr.tofile(f)

    info(f"Finished 3DN Export: {filepath}")
