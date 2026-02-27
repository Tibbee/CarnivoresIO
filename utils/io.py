import bpy
import mathutils
import numpy as np
import os
import re
import bmesh
from ..core.constants import TEXTURE_WIDTH
from .common import timed
from .flags import assign_face_flag_int
from .logger import info, warn, error

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

@timed("smooth_vertex_weights")
def smooth_vertex_weights(obj, iterations=3, factor=0.5, joints_only=False):
    """
    Smooths vertex weights using a topology-based Laplacian approach.
    """
    if not obj.vertex_groups or iterations <= 0:
        return

    mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    
    deform_layer = bm.verts.layers.deform.active
    if not deform_layer:
        # Verify if any groups exist, if so verify the layer
        if obj.vertex_groups:
            deform_layer = bm.verts.layers.deform.verify()
        else:
            bm.free()
            return

    for _ in range(iterations):
        # Store changes to apply at the end of the iteration
        all_new_weights = []
        
        for v in bm.verts:
            if not v.link_edges:
                continue
            
            neighbors = [edge.other_vert(v) for edge in v.link_edges]
            
            # Find all group indices present in this vertex and its neighbors
            v_groups = set(v[deform_layer].keys())
            neighbor_groups = set()
            for n in neighbors:
                neighbor_groups.update(n[deform_layer].keys())
                
            all_groups = v_groups.union(neighbor_groups)
            
            # "Joints Only" logic:
            # If all neighbors and the vertex itself belong to the same SINGLE group, skip.
            if joints_only and len(all_groups) <= 1:
                continue

            new_v_weights = {}
            for g_idx in all_groups:
                self_w = v[deform_layer][g_idx] if g_idx in v[deform_layer] else 0.0
                neighbor_w_sum = sum(n[deform_layer][g_idx] if g_idx in n[deform_layer] else 0.0 for n in neighbors)
                avg_neighbor_w = neighbor_w_sum / len(neighbors)
                
                # Apply user-defined factor
                new_v_weights[g_idx] = self_w + factor * (avg_neighbor_w - self_w)
                
            all_new_weights.append((v, new_v_weights))
            
        # Apply updates
        for v, weights in all_new_weights:
            dv = v[deform_layer]
            for g_idx, w in weights.items():
                if w > 0.0001:
                    dv[g_idx] = w
                elif g_idx in dv:
                    del dv[g_idx]

    bm.to_mesh(mesh)
    bm.free()
            
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
def create_armature(bone_names, bonesTransformedPos, parent_indices, object_name, target_coll, 
                    verticesTransformedPos=None, vertex_owners=None):
    arm_data = bpy.data.armatures.new(f"{object_name}_Armature")
    arm_obj = bpy.data.objects.new(f"{object_name}_ArmatureObj", arm_data)
    coll = target_coll or bpy.context.scene.collection
    coll.objects.link(arm_obj)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')
    edit_bones = arm_obj.data.edit_bones

    # 1. Create all bones first (Heads only)
    bone_list = []
    for i, name in enumerate(bone_names):
        bone = edit_bones.new(name)
        x, y, z = bonesTransformedPos[i]
        bone.head = (x, y, z)
        bone_list.append(bone)

    # 2. Analyze Model Basis (Strict Grid Alignment)
    model_forward = mathutils.Vector((0, 1, 0)) # Default Blender Y-Forward
    model_side = mathutils.Vector((1, 0, 0))
    
    if verticesTransformedPos is not None and len(verticesTransformedPos) > 0:
        v_arr = np.array(verticesTransformedPos)
        v_min = np.min(v_arr, axis=0)
        v_max = np.max(v_arr, axis=0)
        v_size = v_max - v_min
        
        if v_size[0] > v_size[1]:
            model_forward = mathutils.Vector((1, 0, 0))
            model_side = mathutils.Vector((0, 1, 0))
        else:
            model_forward = mathutils.Vector((0, 1, 0))
            model_side = mathutils.Vector((1, 0, 0))
            
        v_center = (v_min + v_max) * 0.5
        v_mean = np.mean(v_arr, axis=0)
        if (v_mean - v_center).dot(np.array(model_forward)) < 0:
            model_forward *= -1

    # 3. Map children
    children_map = {i: [] for i in range(len(bone_names))}
    for i, parent_idx in enumerate(parent_indices):
        if parent_idx != -1:
            children_map[parent_idx].append(i)

    # 4. Global heuristics
    all_distances = []
    for i, bone in enumerate(bone_list):
        children = children_map[i]
        for c_idx in children:
            d = (mathutils.Vector(bonesTransformedPos[c_idx]) - mathutils.Vector(bone.head)).length
            if d > 0.001: all_distances.append(d)
    
    global_median = np.median(all_distances) if all_distances else 0.1
    min_len = 0.01 # Safety to prevent mesh corruption

    # 5. Set Parents and Calculate Tails
    for i, bone in enumerate(bone_list):
        parent_idx = parent_indices[i]
        if parent_idx != -1:
            bone.parent = bone_list[parent_idx]

        children = children_map[i]
        my_head = mathutils.Vector(bone.head)
        
        # Priority 1: Parent-Child Chain (Standard)
        if children:
            child_heads = [mathutils.Vector(bonesTransformedPos[c]) for c in children]
            if len(children) == 1:
                target_tail = child_heads[0]
                dist = (target_tail - my_head).length
                if dist > min_len:
                    bone.tail = target_tail
                    bone_list[children[0]].use_connect = True
                else:
                    # Bone too short to connect, fallback to heuristic
                    bone.tail = my_head + (model_forward * max(dist, min_len))
                    bone_list[children[0]].use_connect = False
            else:
                # Branching bone: Point toward centroid of children
                centroid = sum(child_heads, mathutils.Vector()) / len(children)
                vec = centroid - my_head
                if vec.length > min_len:
                    bone.tail = centroid
                else:
                    bone.tail = my_head + (model_forward * min_len)
                for c_idx in children:
                    bone_list[c_idx].use_connect = False
        
        # Priority 2: Leaf Bone (Pointing consistent with Parent)
        else:
            bone.use_connect = False
            if parent_idx != -1:
                p_bone = bone_list[parent_idx]
                p_head = mathutils.Vector(p_bone.head)
                # Direction from parent to me
                direction = my_head - p_head
                if direction.length > 0.001:
                    bone.tail = my_head + (direction.normalized() * max(direction.length * 0.5, min_len))
                else:
                    bone.tail = my_head + (model_forward * min_len)
            else:
                # Root leaf (rare): Floor bone or single bone model
                bone.tail = my_head + (model_forward * global_median * 0.5)

        # Final safety check for tail position
        if (mathutils.Vector(bone.tail) - my_head).length < min_len:
            bone.tail = my_head + (model_forward * min_len)

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

    info("Custom world shader setup complete.")

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
    bone_index_map = {} # Maps Blender Name -> Integer Index
    clean_name_map = {} # Maps Clean Name -> List of Indices

    if obj.parent and obj.parent.type == 'ARMATURE':
        try:
            arm = obj.parent
            bone_pos_array = np.empty((len(arm.data.bones), 3), dtype=np.float32)
            
            # 1. Collect Bones and Unique Positions (Armature-Space)
            for i, bone in enumerate(arm.data.bones):
                name = bone.name
                bone_names.append(name)
                
                # Use bones in their own local space (relative to Armature origin)
                bone_pos_array[i] = bone.head_local
                
                # Setup hierarchy
                parent_idx = arm.data.bones.find(bone.parent.name) if bone.parent else -1
                bone_parents.append(parent_idx)
                
                # Map for vertex ownership (Fuzzy matching support)
                bone_index_map[name] = i
                clean = name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', name) else name
                if clean not in clean_name_map:
                    clean_name_map[clean] = []
                clean_name_map[clean].append(i)

            # Transform all bone positions at once into the final export space (Scale/Axis)
            bone_positions = apply_import_matrix(bone_pos_array, export_matrix).tolist()

            # 2. Assign Vertex Owners (Dominant Weight + Fuzzy Matching)
            unmatched_vertices = []
            for v in obj.data.vertices:
                winning_bone_idx = -1
                highest_weight = -1.0
                
                for g in v.groups:
                    vg_name = obj.vertex_groups[g.group].name
                    
                    # Exact Match
                    target_idx = -1
                    if vg_name in bone_index_map:
                        target_idx = bone_index_map[vg_name]
                    else:
                        # Fuzzy Match (handles cases where groups or bones have different suffixes)
                        vg_clean = vg_name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', vg_name) else vg_name
                        if vg_clean in clean_name_map:
                            target_idx = clean_name_map[vg_clean][0]
                    
                    if target_idx != -1:
                        if g.weight > highest_weight:
                            highest_weight = g.weight
                            winning_bone_idx = target_idx
                
                if winning_bone_idx != -1:
                    vertex_owners[v.index] = winning_bone_idx
                elif v.groups:
                    unmatched_vertices.append(v.index)

            if unmatched_vertices:
                warn(f"{len(unmatched_vertices)} vertices assigned to root bone (no matching bone found).")
                vertex_owners[unmatched_vertices] = 0

            # 3. Cleanup Names for File Format (Strip suffixes ONLY at return)
            final_names = []
            for name in bone_names:
                clean = name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', name) else name
                final_names.append(clean[:31])

            return final_names, bone_positions, bone_parents, vertex_owners

        except Exception as e:
            error(f"Armature processing failed: {e}")
            return None

    elif obj.parent is None:  # Hooks case
        hook_mods = [m for m in obj.modifiers if m.type == 'HOOK' and m.object and m.vertex_group]
        if hook_mods:
            temp_list = []
            bone_pos_array = []
            
            # Use mesh's world matrix to bring hooks into local space
            world_to_obj = obj.matrix_world.inverted()
            
            for mod in hook_mods:
                hook_obj = mod.object
                name = hook_obj.name
                
                # Transform hook world position into mesh local space
                local_pos = world_to_obj @ hook_obj.matrix_world.translation
                bone_pos_array.append(local_pos)
                temp_list.append((name, hook_obj))
                
            bone_pos_array = np.array(bone_pos_array, dtype=np.float32)
            bone_positions = apply_import_matrix(bone_pos_array, export_matrix).tolist()

            bone_names = []
            bone_parents = [-1] * len(temp_list)
            vertex_owners = np.zeros(len(obj.data.vertices), dtype=np.uint16)
            bone_index_map = {}

            for i, (name, hook_obj) in enumerate(temp_list):
                bone_names.append(name)
                bone_index_map[name] = i

            for i, (name, hook_obj) in enumerate(temp_list):
                if hook_obj.parent:
                    p_name = hook_obj.parent.name
                    if p_name in bone_index_map:
                        bone_parents[i] = bone_index_map[p_name]

            for v in obj.data.vertices:
                winning_idx = 0
                max_w = -1.0
                for g in v.groups:
                    vg_name = obj.vertex_groups[g.group].name
                    if vg_name in bone_index_map:
                        if g.weight > max_w:
                            max_w = g.weight
                            winning_idx = bone_index_map[vg_name]
                vertex_owners[v.index] = winning_idx

            final_names = [n.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', n) else n for n in bone_names]
            return final_names, bone_positions, bone_parents, vertex_owners

    # Fallback for carbones / no bones
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