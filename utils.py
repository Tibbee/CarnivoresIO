import bpy
import mathutils
import os
import numpy as np
import bmesh
import re

import wave
import tempfile
import os

from . import operators
from .core.constants import FACE_FLAG_OPTIONS, TEXTURE_WIDTH

import time
import functools

def timed(label="Function", is_operator=False):
    def decorator(func):
        if is_operator:
            @functools.wraps(func)
            def wrapper(self, context, *args, **kwargs):
                start = time.perf_counter()
                result = func(self, context, *args, **kwargs)
                end = time.perf_counter()
                print(f"[Timing] {label} took {end - start:.6f} seconds")
                return result
        else:
            @functools.wraps(func)
            def wrapper(*args, **kwargs):
                start = time.perf_counter()
                result = func(*args, **kwargs)
                end = time.perf_counter()
                print(f"[Timing] {label} took {end - start:.6f} seconds")
                return result
        return wrapper
    return decorator

@timed("assign_face_flag")
def assign_face_flag_int(mesh: bpy.types.Mesh, face_flags, attr_name="3df_flags"):
    # Create or get the attribute
    attr = mesh.attributes.new(name=attr_name, type='INT', domain='FACE')

    '''if not np.any(face_flags):
        # All flags zero, skip attribute creation
        return'''

    # Fast assignment using foreach_set
    attr.data.foreach_set("value", face_flags)

@timed("get_face_attribute_int")
def get_face_attribute_int(mesh, attr_name, default=0):
    """Read an integer face attribute into a NumPy array; return default if missing."""
    attr = mesh.attributes.get(attr_name)
    if not attr or attr.domain != 'FACE' or attr.data_type != 'INT':
        return np.full(len(mesh.polygons), default, dtype=np.uint16)
    vals = np.empty(len(mesh.polygons), dtype=np.int32)
    attr.data.foreach_get("value", vals)
    return vals.astype(np.uint16)
    
#@timed("count_flag_hits")
def count_flag_hits(obj, attr_name="3df_flags"):
    """
    Return (counts, total)
      - counts: dict mapping bit -> number of faces (numerator)
      - total: number of selected faces (EDIT mode) or total faces (OBJECT mode)
    """
    counts = {bit: 0 for bit, _, _ in FACE_FLAG_OPTIONS}
    mesh = obj.data
    face_count = len(mesh.polygons)
    if face_count == 0:
        return counts, 0

    attr = mesh.attributes.get(attr_name)
    if not attr:
        return counts, 0

    if obj.mode == 'EDIT':
        # Use BMesh for EDIT mode to ensure UI updates correctly
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        layer = bm.faces.layers.int.get(attr_name)
        if not layer:
            return counts, 0

        total = 0
        for f in bm.faces:
            if not f.select:
                continue
            total += 1
            val = f[layer]
            for bit in counts:
                if val & bit:
                    counts[bit] += 1
        return counts, total
    else:
        # In OBJECT mode, always use ALL faces, ignoring any prior selection
        total = face_count

        vals = np.empty(face_count, dtype=np.int32)
        attr.data.foreach_get("value", vals)

        # Count flags for all faces
        for bit in counts:
            counts[bit] = int(np.count_nonzero((vals & bit) != 0))

        return counts, total

@timed("create_mesh_object")
def create_mesh_object(mesh_name, verticesTransformedPos, faces, object_name, smooth_faces, face_flags):
    mesh = bpy.data.meshes.new(mesh_name)
    mesh.vertices.add(len(verticesTransformedPos))
    flat_vertices = verticesTransformedPos.ravel()
    mesh.vertices.foreach_set("co", flat_vertices)

    num_faces = len(faces)
    mesh.loops.add(num_faces * 3)
    flat_faces = faces.ravel()
    mesh.loops.foreach_set("vertex_index", flat_faces)
    mesh.polygons.add(num_faces)

    # loop_start & loop_total
    starts = np.arange(0, num_faces * 3, 3, dtype=np.int32)
    totals = np.full(num_faces, 3, dtype=np.int32)
    mesh.polygons.foreach_set("loop_start", starts)
    mesh.polygons.foreach_set("loop_total", totals)

    if np.any(face_flags):
        assign_face_flag_int(mesh, face_flags)

    mesh.update(calc_edges=False)

    if not smooth_faces:
        mesh.polygons.foreach_set("use_smooth", [False] * num_faces)

    return bpy.data.objects.new(object_name, mesh)
    
#@timed("apply_import_matrix")    
def apply_import_matrix(vertices, import_matrix):
    # Add homogeneous coordinate
    homogenous = np.column_stack((vertices, np.ones(len(vertices))))
    
    # Batch transformation
    transformed = homogenous @ import_matrix.T
    
    # Return only XYZ components
    return transformed[:, :3]
    
@timed("generate_names")        
def generate_names(filepath):
    basename = os.path.splitext(os.path.basename(filepath))[0]
    mesh_name = f"{basename}_Mesh"
    
    return mesh_name, basename

@timed("create_import_collection")
def create_import_collection(object_name, parent_collection=None):
    coll = bpy.data.collections.new(object_name)
    parent = parent_collection or bpy.context.scene.collection
    parent.children.link(coll)
    return coll
    
@timed("create_vertex_groups_from_bones")
def create_vertex_groups_from_bones(obj, bone_names, vertex_owners):
    vertex_groups_by_index = {}
    
    for bone_index, bone_name in enumerate(bone_names):
        if not bone_name:
            continue  # Skip empty names

        # Create vertex group named exactly after the bone
        vg = obj.vertex_groups.new(name=bone_name)
        vertex_groups_by_index[bone_index] = vg
        
        # Find vertices owned by this bone
        vertex_indices = np.where(vertex_owners == bone_index)[0]
        if vertex_indices.size > 0:
            vg.add(vertex_indices.tolist(), 1.0, 'REPLACE')
        
    return vertex_groups_by_index
            
@timed("create_hooks")
def create_hooks(bone_names, bonesTransformedPos, parent_indices, object_name, mesh_obj, target_coll):
    
    hook_objects = {i: bpy.data.objects.new(name, None) for i, name in enumerate(bone_names)}
    
    for i, obj in hook_objects.items():
        obj.empty_display_type = 'SPHERE'
        obj.empty_display_size = 0.1
        obj.show_in_front = True
        # empty.matrix_world = mathutils.Matrix.Translation(bonesTransformedPos[i])
        obj.location = bonesTransformedPos[i]
        obj.parent = mesh_obj
        obj.matrix_parent_inverse = mesh_obj.matrix_world.inverted()
        obj["bone_index"] = i
        # Link to custom collection
        target_coll.objects.link(obj)
        hook_objects[i] = obj
        
    bpy.context.view_layer.update()

    for i, parent_idx in enumerate(parent_indices):
        if parent_idx != -1 and parent_idx in hook_objects:
            child = hook_objects[i]
            parent = hook_objects[parent_idx]

            child.parent = parent
            child.matrix_parent_inverse = parent.matrix_world.inverted()
            
    return hook_objects
    
@timed("create_armature")
def create_armature(bone_names, bonesTransformedPos, parent_indices, object_name, target_coll):
    arm_data = bpy.data.armatures.new(f"{object_name}_Armature")
    arm_obj = bpy.data.objects.new(f"{object_name}_ArmatureObj", arm_data)
    coll = target_coll or bpy.context.scene.collection
    coll.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm_obj.data.edit_bones

    # Precompute first child for each bone
    first_child = [-1] * len(bone_names)
    for i, parent_idx in enumerate(parent_indices):
        if parent_idx != -1 and first_child[parent_idx] == -1:
            first_child[parent_idx] = i

    # Create bones in bulk
    bone_list = [None] * len(bone_names)
    for i, name in enumerate(bone_names):
        bone = edit_bones.new(name)
        x, y, z = bonesTransformedPos[i]
        bone.head = (x, y, z)
        
        if first_child[i] != -1:
            cx, cy, cz = bonesTransformedPos[first_child[i]]
            bone.tail = (cx, cy, cz)
            bone.use_connect = True
        else:
            bone.tail = (x, y, z + 0.1)
        
        bone_list[i] = bone

    # Assign parents in bulk
    for i, parent_idx in enumerate(parent_indices):
        if parent_idx != -1:
            bone_list[i].parent = bone_list[parent_idx]

    bpy.ops.object.mode_set(mode='OBJECT')
    return arm_obj

@timed("assign_armature_modifier")
def assign_armature_modifier(mesh_obj, armature_obj):
    mod = mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = armature_obj
    mesh_obj.parent = armature_obj
    mesh_obj.matrix_parent_inverse = armature_obj.matrix_world.inverted()

@timed("assign_hook_modifiers")
def assign_hook_modifiers(obj, hook_objects, vertex_groups_by_index):
    for hook_obj in hook_objects.values():
        bone_index = hook_obj.get("bone_index")
        if bone_index is None:
            continue

        vg = vertex_groups_by_index.get(bone_index)
        if vg:
            mod = obj.modifiers.new(name=f"Hook_{vg.name}", type='HOOK')
            mod.object = hook_obj
            mod.vertex_group = vg.name      
            
@timed("create_uv_map")     
def create_uv_map(mesh, uvs):

    uv_layer = mesh.uv_layers.new(name="UV Map")
    flat_uvs = np.asarray(uvs, dtype=np.float32).ravel()
    uv_layer.data.foreach_set('uv', flat_uvs)
    
    return uv_layer

@timed("create_image_texture") 
def create_image_texture(texture, texture_height, object_name):
    image = bpy.data.images.new(
        name=f"{object_name}_Texture",
        width=TEXTURE_WIDTH,
        height=texture_height
    )
    image.pixels = texture
    image.pack()
    image.reload()
    
    return image
    
@timed("create_texture_material")   
def create_texture_material(image, object_name):

    material = bpy.data.materials.new(name=f"{object_name}_Material")
    material.use_nodes = True
    nodes = material.node_tree.nodes
    links = material.node_tree.links
    nodes.clear()

    # Nodes
    image_texture = nodes.new("ShaderNodeTexImage")
    image_texture.image = image
    image_texture.name = "Image Texture"

    image_texture_001 = nodes.new("ShaderNodeTexImage")
    image_texture_001.image = image
    image_texture_001.interpolation = 'Closest'
    image_texture_001.name = "Image Texture.001"

    diffuse_bsdf = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse_bsdf.name = "Diffuse BSDF"
    diffuse_bsdf.inputs[1].default_value = 0.0

    diffuse_bsdf_001 = nodes.new("ShaderNodeBsdfDiffuse")
    diffuse_bsdf_001.name = "Diffuse BSDF.001"
    diffuse_bsdf_001.inputs[1].default_value = 0.0

    transparent_bsdf = nodes.new("ShaderNodeBsdfTransparent")
    transparent_bsdf.name = "Transparent BSDF"
    transparent_bsdf.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)

    math = nodes.new("ShaderNodeMath")
    math.name = "Less Than"
    math.operation = 'LESS_THAN'
    math.inputs[1].default_value = 0.001

    attribute = nodes.new("ShaderNodeAttribute")
    attribute.name = "Attribute"
    attribute.attribute_name = "3df_flags"
    attribute.attribute_type = 'GEOMETRY'

    math_001 = nodes.new("ShaderNodeMath")
    math_001.name = "Compare"
    math_001.operation = 'COMPARE'
    math_001.inputs[1].default_value = 5.0
    math_001.inputs[2].default_value = 1.0

    mix_shader = nodes.new("ShaderNodeMixShader")
    mix_shader.name = "Pre Mix"
    
    mix_shader_001 = nodes.new("ShaderNodeMixShader")
    mix_shader_001.name = "Final Mix"

    material_output = nodes.new("ShaderNodeOutputMaterial")

    #Set locations
    image_texture.location = (-860.0, 40.0)
    image_texture_001.location = (-860.0, -260.0)
    diffuse_bsdf.location = (-580.0, 40.0)
    diffuse_bsdf_001.location = (-580.0, -320.0)
    transparent_bsdf.location = (-580.0, -460.0)
    math.location = (-580.0, -160.0)
    attribute.location = (-1030.0, 190.0)
    math_001.location = (-858.0, 220.0)
    mix_shader.location = (-378.0, -274.0)
    mix_shader_001.location = (-240.0, 240.0)
    material_output.location = (-60.0, 260.0)
    
    #links
    links.new(image_texture.outputs[0], diffuse_bsdf.inputs[0])
    links.new(image_texture_001.outputs[0], diffuse_bsdf_001.inputs[0])
    links.new(image_texture_001.outputs[0], math.inputs[0])
    links.new(math.outputs[0], mix_shader.inputs[0])
    links.new(diffuse_bsdf_001.outputs[0], mix_shader.inputs[1])
    links.new(transparent_bsdf.outputs[0], mix_shader.inputs[2])
    links.new(attribute.outputs[2], math_001.inputs[0])
    links.new(math_001.outputs[0], mix_shader_001.inputs[0])
    links.new(mix_shader_001.outputs[0], material_output.inputs[0])
    links.new(diffuse_bsdf.outputs[0], mix_shader_001.inputs[1])
    links.new(mix_shader.outputs[0], mix_shader_001.inputs[2])
    
    return material
    
@timed("setup_custom_world_shader") 
def setup_custom_world_shader():

    world = bpy.data.worlds.get("CustomWorld")
    if not world:
        world = bpy.data.worlds.new("CustomWorld")
        bpy.context.scene.world = world
    else:
        bpy.context.scene.world = world

    world.use_nodes = True
    nodes = world.node_tree.nodes
    links = world.node_tree.links
    
    if "CustomWorldShaderSet" in world:
        return  # Already set up
    nodes.clear()

    bg01 = nodes.new("ShaderNodeBackground")
    bg01.name = "Camera Ray Background"
    bg01.inputs[0].default_value = (1.0, 1.0, 1.0, 1.0)
    bg01.location = (-600, 150)

    bg02 = nodes.new("ShaderNodeBackground")
    bg02.name = "Other Rays Background"
    bg02.inputs[0].default_value = (0.08, 0.08, 0.08, 1.0)
    bg02.location = (-600, -100)

    light_path = nodes.new("ShaderNodeLightPath")
    light_path.name = "Light Path"
    light_path.location = (-900, 0)

    mix_shader = nodes.new("ShaderNodeMixShader")
    mix_shader.name = "Mix Shader"
    mix_shader.location = (-300, 0)

    output = nodes.new("ShaderNodeOutputWorld")
    output.location = (100, 0)

    links.new(light_path.outputs['Is Camera Ray'], mix_shader.inputs[0])
    links.new(bg01.outputs['Background'], mix_shader.inputs[1])
    links.new(bg02.outputs['Background'], mix_shader.inputs[2])
    links.new(mix_shader.outputs['Shader'], output.inputs['Surface'])

    world["CustomWorldShaderSet"] = True

    print("✅ Custom world shader setup complete.")
@timed("get_selected_face_indices")    
def get_selected_face_indices(obj):
    """Return numpy array of selected face indices (int32). In OBJECT mode, return all faces if none selected."""
    mesh = obj.data
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        sel = [f.index for f in bm.faces if f.select]
        return np.array(sel, dtype=np.int32)
    else:
        face_count = len(mesh.polygons)
        if face_count == 0:
            return np.zeros(0, dtype=np.int32)
        sel_flags = np.empty(face_count, dtype=np.int8)
        mesh.polygons.foreach_get('select', sel_flags)
        selected_indices = np.nonzero(sel_flags)[0]
        return selected_indices.astype(np.int32)
@timed("bulk_modify_flag")
def bulk_modify_flag(mesh, selected_indices, mask, op):
    """
    Perform a bulk modify on mesh.attributes['3df_flags'].
    Returns the number of faces actually changed.
    op: 'set' | 'clear' | 'toggle'
    """
    attr = mesh.attributes.get("3df_flags")
    if not attr:
        raise RuntimeError("'3df_flags' attribute missing")

    face_count = len(mesh.polygons)
    if face_count == 0 or selected_indices.size == 0:
        return 0

    # Verify attribute data length
    if len(attr.data) != face_count:
        raise RuntimeError(f"'3df_flags' attribute data length ({len(attr.data)}) does not match face count ({face_count})")

    # Read current values into numpy array
    vals = np.empty(face_count, dtype=np.int32)
    attr.data.foreach_get("value", vals)

    # Copy the portion we will compare to compute changed count
    before_sel = vals[selected_indices].copy()

    if op == 'set':
        vals[selected_indices] |= mask
    elif op == 'clear':
        vals[selected_indices] &= ~mask
    elif op == 'toggle':
        vals[selected_indices] ^= mask
    else:
        raise ValueError(f"Unknown op: {op}")

    # Write back in a single C call
    attr.data.foreach_set("value", vals)
    mesh.update()

    after_sel = vals[selected_indices]
    changed = int(np.count_nonzero(before_sel != after_sel))
    return changed
@timed("triangulated_mesh_copy")    
def triangulated_mesh_copy(mesh):
    # Quick check: Skip triangulation if already all tris (faster for pre-tri meshes)
    all_tris = all(p.loop_total == 3 for p in mesh.polygons)
    if all_tris:
        return mesh.copy()

    tmp = mesh.copy()
    bm = bmesh.new()
    bm.from_mesh(tmp)
    if bm.faces:
        bmesh.ops.triangulate(bm, faces=bm.faces[:], quad_method='BEAUTY', ngon_method='BEAUTY')
    bm.to_mesh(tmp)
    bm.free()
    return tmp

@timed("find_texture_image")
def find_texture_image(mesh_obj):
    if not mesh_obj.material_slots:
        return None, 0
    for slot in mesh_obj.material_slots:
        mat = slot.material
        if mat and mat.use_nodes:
            for node in mat.node_tree.nodes:
                if node.type == 'TEX_IMAGE' and node.image:
                    img = node.image
                    return img, img.size[1]
    return None, 0

@timed("image_to_argb1555")
def image_to_argb1555(image):
    width, height = image.size
    if width != TEXTURE_WIDTH:
        raise ValueError(f"Texture width {width} must be {TEXTURE_WIDTH} pixels.")
    
    # Get pixels and validate length
    pixels = np.array(image.pixels[:], dtype=np.float32)
    expected_len = width * height * 4
    if pixels.size != expected_len:
        raise ValueError(f"Image pixel data length {pixels.size} does not match expected {expected_len} (width={width}, height={height}).")
    
    pixels = pixels.reshape(height, width, 4)[::-1].ravel()
    #pixels = np.flipud(pixels).ravel()
    #pixels = pixels.ravel()
    
    # Extract RGBA and convert to ARGB1555
    r = np.clip((pixels[::4] * 31).round(), 0, 31).astype(np.uint16)
    g = np.clip((pixels[1::4] * 31).round(), 0, 31).astype(np.uint16)
    b = np.clip((pixels[2::4] * 31).round(), 0, 31).astype(np.uint16)
    a = 0  # Alpha always 0 for ARGB1555
    packed = (a << 15) | (r << 10) | (g << 5) | b
    return packed.astype('<u2')

@timed("collect_bones_and_owners")
def collect_bones_and_owners(obj, export_matrix):
    bone_names = []
    bone_positions = []
    bone_parents = []
    vertex_owners = np.zeros(len(obj.data.vertices), dtype=np.uint16)
    bone_index_map = {}  # For quick lookup

    if obj.parent and obj.parent.type == 'ARMATURE':
        try:
            arm = obj.parent
            bone_index_map = {}  # Maps clean bone name to list of (index, blender_name) pairs to handle duplicates
            bone_pos_array = np.empty((len(arm.data.bones), 3), dtype=np.float32)
            
            # Collect bones in original order
            for i, bone in enumerate(arm.data.bones):
                # Strip Blender's .001, .002, etc. suffixes
                name = bone.name
                clean_name = name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', name) else name
                bone_names.append(clean_name)
                bone_pos_array[i] = arm.matrix_world @ bone.head_local
                bone_parents.append(arm.data.bones.find(bone.parent.name) if bone.parent else -1)
                # Store both index and Blender name to handle duplicates
                if clean_name not in bone_index_map:
                    bone_index_map[clean_name] = []
                bone_index_map[clean_name].append((i, name))

            # Transform all bone positions at once
            bone_positions = apply_import_matrix(bone_pos_array, export_matrix).tolist()

            # Assign vertex owners, handling duplicates and unmatched groups
            unmatched_vertices = []
            for v in obj.data.vertices:
                assigned = False
                for g in v.groups:
                    vg_name = obj.vertex_groups[g.group].name
                    vg_name_clean = vg_name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', vg_name) else vg_name
                    if vg_name_clean in bone_index_map:
                        # Find the correct bone index by matching the exact Blender vertex group name
                        for bone_idx, blender_name in bone_index_map[vg_name_clean]:
                            if blender_name == vg_name:
                                vertex_owners[v.index] = bone_idx
                                assigned = True
                                break
                if not assigned and v.groups:
                    unmatched_vertices.append(v.index)

            if unmatched_vertices:
                print(f"[Export] Warning: {len(unmatched_vertices)} vertices have vertex groups not matching any bone names: {unmatched_vertices}")
                # Assign unmatched vertices to bone 0 to avoid invalid owners
                vertex_owners[unmatched_vertices] = 0

            return bone_names, bone_positions, bone_parents, vertex_owners

        except Exception as e:
            print(f"[Export] Armature processing failed: {e}")
            return None  # Keep this to maintain compatibility, but handled in operator

    elif obj.parent is None:  # Hooks case (no armature)
        hook_mods = [m for m in obj.modifiers if m.type == 'HOOK' and m.object and m.vertex_group]
        if hook_mods:
            temp_list = []
            bone_pos_array = []
            for mod in hook_mods:
                hook_obj = mod.object
                # Strip suffix from name
                name = hook_obj.name
                clean_name = name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', name) else name
                bone_pos_array.append(hook_obj.matrix_world.translation)
                temp_list.append((clean_name, hook_obj))
            bone_pos_array = np.array(bone_pos_array, dtype=np.float32)
            bone_positions = apply_import_matrix(bone_pos_array, export_matrix).tolist()
            temp_list = [(name, pos, hook_obj) for (name, hook_obj), pos in zip(temp_list, bone_positions)]

            # Do NOT sort temp_list to preserve original .3df bone order
            bone_names = []
            bone_positions = []
            bone_parents = [-1] * len(temp_list)
            vertex_owners = np.zeros(len(obj.data.vertices), dtype=np.uint16)
            bone_index_map = {}  # Maps clean_name to list of (index, blender_name) pairs

            for i, (name, pos, hook_obj) in enumerate(temp_list):
                bone_names.append(name)
                bone_positions.append(pos)
                # Store both index and Blender name to handle duplicates
                if name not in bone_index_map:
                    bone_index_map[name] = []
                bone_index_map[name].append((i, hook_obj.name))

            # Set parents
            for i, (name, pos, hook_obj) in enumerate(temp_list):
                if hook_obj.parent:
                    parent_name = hook_obj.parent.name
                    clean_parent_name = parent_name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', parent_name) else parent_name
                    if clean_parent_name in bone_index_map:
                        # Find the exact parent by matching Blender name
                        for parent_idx, blender_name in bone_index_map[clean_parent_name]:
                            if blender_name == parent_name:
                                bone_parents[i] = parent_idx
                                break

            # Assign vertex owners, handling duplicates by matching Blender vertex group names
            for v in obj.data.vertices:
                for g in v.groups:
                    vg_name = obj.vertex_groups[g.group].name
                    vg_name_clean = vg_name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', vg_name) else vg_name
                    if vg_name_clean in bone_index_map:
                        # Find the correct bone index by matching the exact Blender vertex group name
                        for bone_idx, blender_name in bone_index_map[vg_name_clean]:
                            if blender_name == vg_name:
                                vertex_owners[v.index] = bone_idx
                                break

            return bone_names, bone_positions, bone_parents, vertex_owners

    # If no armature or hook modifiers, but has vertex groups,
    # interpret these vertex groups as "bones" for the .car format.
    # Vertex owner indices will be based on the alphabetical order of vertex group names.
    if not obj.parent or obj.parent.type != 'ARMATURE': # No armature parent
        hook_mods = [m for m in obj.modifiers if m.type == 'HOOK' and m.object and m.vertex_group]
        if not hook_mods and obj.vertex_groups: # No hooks, but has vertex groups
            
            # Filter for non-empty vertex groups and sort them alphabetically for consistent indexing
            # We assume any existing VG could be used for ownership.
            active_vgs = []
            for vg in obj.vertex_groups:
                # To check if a VG is empty without iterating all vertices,
                # we could check if vg.add or vg.remove has been called, but this is more complex.
                # For now, we'll assume any existing VG is a candidate for owner.
                active_vgs.append(vg)
            
            if active_vgs: # Only proceed if there are actual vertex groups
                # Helper for natural sorting (handles CarBone_2 vs CarBone_10 correctly)
                def natural_sort_key(vg):
                    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', vg.name)]

                # Sort naturally to get a deterministic index for 'bone'
                active_vgs.sort(key=natural_sort_key)
                
                bone_names = [vg.name for vg in active_vgs]
                
                # Create a map for quick lookup from vg.index (Blender's internal VG index)
                # to our new 'bone_idx' (based on sorted alphabetical order)
                vg_blender_idx_to_bone_idx = {vg.index: i for i, vg in enumerate(active_vgs)}
                
                bone_positions = [(0.0, 0.0, 0.0)] * len(bone_names) # Dummy positions for carbones
                bone_parents = [-1] * len(bone_names) # No parents for carbones
                
                vertex_owners = np.zeros(len(obj.data.vertices), dtype=np.uint16)
                
                for v_idx, vertex in enumerate(obj.data.vertices):
                    winning_bone_idx = 0 # Default to bone 0 if no recognized group found for this vertex
                    highest_weight = 0.0
                    
                    for g in vertex.groups: # Iterate groups vertex belongs to
                        if g.group in vg_blender_idx_to_bone_idx: # Check if this group is one of our active_vgs
                            if g.weight > highest_weight:
                                highest_weight = g.weight
                                winning_bone_idx = vg_blender_idx_to_bone_idx[g.group]
                    
                    vertex_owners[v_idx] = winning_bone_idx
                
                return bone_names, bone_positions, bone_parents, vertex_owners
    
    # Fallback: No bones at all (original behavior for truly bone-less meshes)
    bone_names = ["Default"]
    bone_positions = [(0.0, 0.0, 0.0)]
    bone_parents = [-1]
    vertex_owners[:] = 0
    return bone_names, bone_positions, bone_parents, vertex_owners

def handle_car_owners(vertices, context):
    non_zero_owners = vertices['owner'][vertices['owner'] > 0]
    if non_zero_owners.size == 0:
        return vertices, np.array([], dtype='U32')
    min_non_zero = np.min(non_zero_owners)
    max_owner = int(np.max(vertices['owner']))
    vertices['owner'][vertices['owner'] > 0] -= min_non_zero
    vertices['owner'] = np.clip(vertices['owner'], 0, None)
    max_owner_adjusted = int(np.max(vertices['owner']))
    bone_names = np.array([f"CarBone_{i + min_non_zero}" for i in range(max_owner_adjusted + 1)], dtype='U32')
    return vertices, bone_names

@timed('create_shape_keys_from_car_animations')
def create_shape_keys_from_car_animations(obj, animations, import_matrix_np):
    if not animations:
        print("[ShapeKeys] No animations to import")
        return
    mesh = obj.data
    vcount = len(mesh.vertices)
    total_keys = 0
    # Initialize shape keys if needed (first add creates Basis from current verts)
    if mesh.shape_keys is None:
        print("[ShapeKeys] Initializing shape keys (creating Basis)")
        obj.shape_key_add(name="Basis")  # Auto-creates obj.data.shape_keys
    sk_data = mesh.shape_keys
    for anim in animations:
        anim_name = anim['name']
        frames_count = anim['frames_count']
        positions = anim['positions']  # (frames_count, vcount, 3) float32
        if positions.shape != (frames_count, vcount, 3):
            print(f"[ShapeKeys] Warning: Skipping {anim_name} (invalid positions shape {positions.shape})")
            continue
        for frame_i in range(frames_count):  # All frames as keys (Basis is static verts)
            key_name = f"{anim_name}.Frame_{frame_i+1:03d}"
            # Transform this frame's positions
            frame_pos = apply_import_matrix(positions[frame_i], import_matrix_np)  # Note: Use direct call (utils. not needed internally)
            # Add new key (from_mix=False to base on Basis)
            key = obj.shape_key_add(name=key_name, from_mix=False)
            flat_pos = frame_pos.ravel()
            key.data.foreach_set('co', flat_pos)
            total_keys += 1
        print(f"[ShapeKeys] Added {frames_count} keys for '{anim_name}'")
    mesh.update()
    print(f"[ShapeKeys] Total keys added: {total_keys} across {len(animations)} animations")

@timed('create_shape_key_action')
def create_shape_key_action(obj, action_name="CarAnimation"):
    """Ensure the object has a shape key action assigned."""
    if not obj or obj.type != 'MESH':
        print("[Error] Selected object is not a mesh")
        return None

    mesh = obj.data
    if not mesh.shape_keys:
        print("[Error] Object has no shape keys")
        return None

    sk_data = mesh.shape_keys
    sk_data.animation_data_create()

    # Create or reuse action
    action = bpy.data.actions.get(action_name)
    if action is None:
        action = bpy.data.actions.new(name=action_name)
        print(f"[Action] Created new action: {action.name}")
    else:
        print(f"[Action] Reusing existing action: {action.name}")

    sk_data.animation_data.action = action
    print(f"[Action] Assigned to shape keys of '{obj.name}'")
    return action

@timed('keyframe_shape_key_animation_as_action')
def keyframe_shape_key_animation_as_action(obj, anim_name, frame_start=1, frame_step=1, kps=None):
    if not obj or obj.type != 'MESH':
        print('[Error] Selected object is not a mesh')
        return
    mesh = obj.data
    if not mesh.shape_keys:
        print('[Error] Object has no shape keys')
        return
    sk_data = mesh.shape_keys
    sk_data.animation_data_create()
    action_name = f"{anim_name}_Action"
    action = bpy.data.actions.new(name=action_name)
    
    # Store Original KPS if provided
    if kps is not None:
        action["carnivores_kps"] = int(kps)
        
    sk_data.animation_data.action = action
    key_blocks = [kb for kb in sk_data.key_blocks if re.match(f"^{re.escape(anim_name)}\\.Frame_\\d+", kb.name)]
    key_blocks.sort(key=lambda kb: kb.name)
    if not key_blocks:
        print(f"[Warning] No shape keys found for animation '{anim_name}'")
        return
    print(f"[Keyframe] Creating Action '{action_name}' with {len(key_blocks)} frames")
    
    # Identify all shape keys, excluding Basis
    reference_key = sk_data.reference_key
    all_keys = [kb for kb in sk_data.key_blocks if kb != reference_key]
    group_keys = key_blocks
    other_keys = [kb for kb in all_keys if kb not in group_keys]
    
    # Create F-Curves upfront for all relevant keys (one per shape key)
    fcurves = {}
    for kb in other_keys + group_keys:
        data_path = f'key_blocks["{kb.name}"].value'
        fc = action.fcurves.new(data_path=data_path, index=-1)
        fc.extrapolation = 'CONSTANT'  # Hold values constant between keys
        fcurves[kb.name] = fc
    
    frame = frame_start
    num_frames = len(group_keys)
    
    # At first frame: Other keys to 0, this group's future to 0, first to 1
    # Batch insert and set CONSTANT interp
    for kb in other_keys + group_keys[1:]:
        fcurves[kb.name].keyframe_points.insert(frame, 0.0)
        fcurves[kb.name].keyframe_points[-1].interpolation = 'CONSTANT'
    fcurves[group_keys[0].name].keyframe_points.insert(frame, 1.0)
    fcurves[group_keys[0].name].keyframe_points[-1].interpolation = 'CONSTANT'
    
    # Subsequent frames: Only this group (prev=0, curr=1)
    prev = group_keys[0]
    for i in range(1, num_frames):
        frame += frame_step
        fcurves[prev.name].keyframe_points.insert(frame, 0.0)
        fcurves[prev.name].keyframe_points[-1].interpolation = 'CONSTANT'
        curr = group_keys[i]
        fcurves[curr.name].keyframe_points.insert(frame, 1.0)
        fcurves[curr.name].keyframe_points[-1].interpolation = 'CONSTANT'
        prev = curr
    
    # Update all curves (bulk op, fast)
    for fc in fcurves.values():
        fc.update()
    
    frame_end = frame_start + (num_frames - 1) * frame_step
    print(f"[Keyframe] Done: Action '{action_name}' for '{anim_name}' (frames {frame_start}-{frame_end})")
    return action

@timed('push_shape_key_action_to_nla')
def push_shape_key_action_to_nla(obj, strip_name=None, frame_start=1, frame_end=None):
    """
    Pushes the current shape key Action of the object into the NLA as a new strip.
    """
    if not obj or obj.type != 'MESH':
        print("[Error] Selected object is not a mesh")
        return None

    sk_data = obj.data.shape_keys
    if not sk_data or not sk_data.animation_data or not sk_data.animation_data.action:
        print("[Error] No active Action on shape keys. Create and keyframe first.")
        return None

    anim_data = sk_data.animation_data
    action = anim_data.action
    nla_tracks = anim_data.nla_tracks

    if strip_name is None:
        strip_name = action.name

    # Create or reuse a track
    if not nla_tracks:
        track = nla_tracks.new()
        track.name = f"{strip_name}_Track"
    else:
        # Reuse last or create new if overlapping
        track = nla_tracks[-1]
        if track.strips and track.strips[-1].frame_end > frame_start:
            track = nla_tracks.new()
            track.name = f"{strip_name}_Track"

    # Determine frame range dynamically
    if frame_end is None:
        start, end = get_action_frame_range(action)
        frame_start, frame_end = start, end

    # Add the strip to the NLA
    strip = track.strips.new(strip_name, frame_start, action)
    strip.frame_end = frame_end
    anim_data.action = None  # Unlink active action (push down)

    print(f"[NLA] Action '{action.name}' pushed to NLA strip '{strip.name}' ({frame_start}-{frame_end})")
    return strip

@timed('auto_create_shape_key_actions_from_car')
def auto_create_shape_key_actions_from_car(obj, frame_step=1, parsed_animations=None):
    if not obj or obj.type != 'MESH':
        print('[Error] Selected object is not a mesh')
        return
    mesh = obj.data
    if not mesh.shape_keys:
        print('[Info] No shape keys on object; skipping animation setup.')
        return
    sk_data = mesh.shape_keys
    names = [kb.name for kb in sk_data.key_blocks if '.' in kb.name]
    
    # Create KPS lookup map if animations provided
    kps_map = {}
    if parsed_animations:
        for anim in parsed_animations:
            kps_map[anim['name']] = anim['kps']
    
    # Preserve order: iterate names, extract base, add to list if not seen
    base_names = []
    seen = set()
    for n in names:
        if '.Frame_' in n:
            base = n.split('.Frame_')[0]
            if base not in seen:
                seen.add(base)
                base_names.append(base)
                
    if not base_names:
        print('[Info] No animation-style shape keys found (no .Frame_### pattern).')
        return
    print(f"[AutoAction] Found {len(base_names)} animation groups: {base_names}")
    
    actions = []
    for anim_name in base_names:
        print(f"[AutoAction] Processing animation '{anim_name}'...")
        # Retrieve KPS from map or default to None
        anim_kps = kps_map.get(anim_name)
        
        action = keyframe_shape_key_animation_as_action(obj, anim_name, frame_start=1, frame_step=frame_step, kps=anim_kps)
        if action:
            actions.append(action)
    
    # Batch NLA push: Inline overlap checks per action (no manual indexing)
    try:
        anim_data = sk_data.animation_data
        if actions:
            nla_tracks = anim_data.nla_tracks
            track = None
            num_tracks_used = 0
            # Reverse order so first animation in list becomes the top-most NLA track
            # (Tracks are added bottom-to-top, 0..N)
            for action in reversed(actions):
                strip_name = action.name.replace('_Action', '')
                start_frame, _ = get_action_frame_range(action)
                
                # Get last track or create first
                if track is None:
                    if nla_tracks:
                        track = nla_tracks[-1]
                    else:
                        track = nla_tracks.new()
                        track.name = strip_name
                        num_tracks_used += 1
                
                # Overlap check: If last strip ends after start_frame, new track
                if track.strips and track.strips[-1].frame_end > start_frame:
                    track = nla_tracks.new()
                    track.name = f'{strip_name}.{num_tracks_used + 1:03d}'
                    num_tracks_used += 1
                
                # Add strip
                strip = track.strips.new(strip_name, start_frame, action)
                strip.use_sync_length = True  # Tighten eval for discrete steps
            
            anim_data.action = None  # Clear active action
            print(f"[AutoAction] Pushed {len(actions)} actions to NLA batch (using {num_tracks_used} tracks).")
    except Exception as e:
        print(f"[AutoAction] NLA batch push failed (non-fatal): {e}")
        import traceback
        traceback.print_exc()  # Log full stack for debug (remove if noisy)
    
    # Single update at end (key for perf)
    bpy.context.view_layer.update()
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)
    print('[AutoAction] Completed all animations.')
    return actions

def get_action_frame_range(action):
    """Return the min/max frame numbers for keyframes in this Action."""
    if not action or not action.fcurves:
        return (1, 1)
    frames = [kp.co[0] for fc in action.fcurves for kp in fc.keyframe_points]
    return (int(min(frames)), int(max(frames)))

def import_car_sounds(self, sounds, model_name, context):
    imported_sounds = []
    for idx, s in enumerate(sounds):
        sound_name = s['name']
        data = s['data']
        if data.size == 0:
            print({'WARNING'}, f"Skipping empty sound '{sound_name}' (0 samples).")
            continue
        # Create temp WAV
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as temp_file:
            temp_path = temp_file.name
        try:
            with wave.open(temp_path, 'wb') as wf:
                wf.setnchannels(1)  # Mono
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(22050)
                wf.setnframes(data.size)
                wf.writeframes(data.tobytes())
            # Load into Blender
            sound_block = bpy.data.sounds.load(temp_path)
            # Set the name before doing anything else
            sound_block.name = sound_name
            # Pack to embed
            sound_block.pack()
            # Unpack and repack to force Blender to update
            if sound_block.packed_file:
                sound_block.unpack(method='USE_LOCAL')
                # Add the path of the created file to our set for later cleanup
                unpacked_filepath = bpy.path.abspath(sound_block.filepath)
                operators._temp_sound_files.add(unpacked_filepath)
                sound_block.pack()

            imported_sounds.append(sound_block)
            print({'INFO'}, f"Imported and packed sound '{sound_block.name}' ({data.size} samples).")
        except Exception as e:
            print({'ERROR'}, f"Failed to import sound '{sound_name}': {str(e)}")
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)  # Cleanup (safe now that it's packed)
    return imported_sounds

def associate_sounds_with_animations(self, obj, animations, cross_ref, imported_sounds, actions=None):
    if not animations or cross_ref is None or not imported_sounds:
        return

    # If actions are not passed directly, fall back to the old lookup method
    action_list = actions or bpy.data.actions

    for anim_idx, anim in enumerate(animations):
        if anim_idx >= len(cross_ref):
            break  # Table size limit
        
        sound_idx = cross_ref[anim_idx]
        if sound_idx == -1 or sound_idx >= len(imported_sounds):
            continue

        linked_sound = imported_sounds[sound_idx]
        action_name = f"{anim['name']}_Action"
        
        # Find the action in the provided list or the fallback list
        action = next((act for act in action_list if act.name == action_name), None)

        if action:
            # Use the PointerProperty registered on bpy.types.Action
            action.carnivores_sound_ptr = linked_sound
            print({'INFO'}, f"Associated sound '{linked_sound.name}' with animation '{anim['name']}'.")
        else:
            print({'WARNING'}, f"Action '{action_name}' not found for sound association.")
