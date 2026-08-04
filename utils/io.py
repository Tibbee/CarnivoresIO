import bpy
import mathutils
import numpy as np
import os
import re
import json
import bmesh
from ..core.constants import TEXTURE_WIDTH
from .common import timed
from .flags import assign_face_flag_int
from .logger import info, warn, error
from .rig_reconstruction import build_owner_mapping

# UPDATE_GENERATED swaps a new armature data block into an existing object. Keep
# the previous data alive until the reconstruction adapter has committed all
# later metadata, weight, and modifier operations.
_PENDING_ARMATURE_UPDATES = {}


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
    """Apply an affine 4x4 transform without allocating homogeneous Nx4 arrays."""
    vertices = np.asarray(vertices)
    matrix = np.asarray(import_matrix)
    return vertices @ matrix[:3, :3].T + matrix[:3, 3]
    
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
    owners = np.asarray(vertex_owners).reshape(-1)
    valid_vertices = np.flatnonzero((owners >= 0) & (owners < len(bone_names)))

    # Sort once rather than scanning every vertex separately for every bone.
    vertices_by_owner = {}
    if valid_vertices.size:
        order = np.argsort(owners[valid_vertices], kind='stable')
        sorted_vertices = valid_vertices[order]
        sorted_owners = owners[sorted_vertices]
        boundaries = np.flatnonzero(np.diff(sorted_owners)) + 1
        for indices in np.split(sorted_vertices, boundaries):
            vertices_by_owner[int(owners[indices[0]])] = indices

    for bone_index, bone_name in enumerate(bone_names):
        if not bone_name:
            continue  # Skip empty names

        vg = obj.vertex_groups.new(name=bone_name)
        vertex_groups_by_index[bone_index] = vg
        vertex_indices = vertices_by_owner.get(bone_index)
        if vertex_indices is not None:
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
    
def _calculate_pca_direction(verts, fallback_dir):
    """
    Computes the principal component (longest axis) of a set of 3D points.
    Returns: mathutils.Vector direction, aligned with fallback_dir.
    """
    if len(verts) < 2:
        return fallback_dir
        
    # Center the vertices
    mean = np.mean(verts, axis=0)
    centered = verts - mean
    
    # Singular Value Decomposition (SVD)
    try:
        _, _, vh = np.linalg.svd(centered, full_matrices=False)
        direction = mathutils.Vector(vh[0])
        
        # Align direction with fallback_dir so it points away from parent
        if direction.dot(fallback_dir) < 0:
            direction *= -1
            
        return direction.normalized()
    except Exception:
        return fallback_dir

def _capture_blender_context():
    active = bpy.context.view_layer.objects.active
    return {
        "active": active,
        "active_selected": active.select_get() if active else False,
        "selected": tuple(bpy.context.selected_objects),
        "mode": active.mode if active else "OBJECT",
    }


def _restore_blender_context(snapshot):
    """Best-effort restoration of selection, active object, and mode."""
    try:
        current_active = bpy.context.view_layer.objects.active
        if current_active and current_active.mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        for selected in list(bpy.context.selected_objects):
            selected.select_set(False)
        valid_selected = [
            obj for obj in snapshot["selected"]
            if obj and obj.name in bpy.data.objects
        ]
        for selected in valid_selected:
            selected.select_set(True)

        active = snapshot["active"]
        if active and active.name in bpy.data.objects:
            active.select_set(True)
            bpy.context.view_layer.objects.active = active
            if snapshot["mode"] != 'OBJECT' and active.mode == 'OBJECT':
                bpy.ops.object.mode_set(mode=snapshot["mode"])
            if not snapshot["active_selected"]:
                active.select_set(False)
        else:
            bpy.context.view_layer.objects.active = None
    except Exception as exc:
        warn(f"Could not fully restore Blender context after armature construction: {exc}")


def _activate_armature_for_edit(arm_obj):
    active = bpy.context.view_layer.objects.active
    if active and active.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    for selected in list(bpy.context.selected_objects):
        selected.select_set(False)
    arm_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj
    bpy.ops.object.mode_set(mode='EDIT')


def _commit_pending_armature_update(armature_obj):
    """Discard the old data block after an UPDATE_GENERATED commit."""
    pending = _PENDING_ARMATURE_UPDATES.pop(armature_obj.as_pointer(), None)
    if not pending:
        return
    old_data, _new_data, _old_matrix = pending
    if old_data and old_data.name in bpy.data.armatures and old_data.users == 0:
        bpy.data.armatures.remove(old_data)


def rollback_pending_armature_update(armature_obj, original_data=None, original_matrix=None):
    """Restore an UPDATE_GENERATED object after a later adapter failure."""
    pending = _PENDING_ARMATURE_UPDATES.pop(armature_obj.as_pointer(), None)
    if pending:
        old_data, new_data, old_matrix = pending
        if armature_obj.name in bpy.data.objects and armature_obj.data == new_data:
            armature_obj.data = old_data
        if new_data and new_data.name in bpy.data.armatures and new_data.users == 0:
            bpy.data.armatures.remove(new_data)
        original_data = old_data
        original_matrix = old_matrix
    if (
        original_data is not None
        and armature_obj.name in bpy.data.objects
        and armature_obj.data != original_data
    ):
        armature_obj.data = original_data
    if original_matrix is not None and armature_obj.name in bpy.data.objects:
        armature_obj.matrix_world = original_matrix


def _remove_armature_object(arm_obj):
    """Remove a partially-created armature without touching shared data."""
    if not arm_obj or arm_obj.name not in bpy.data.objects:
        return
    arm_data = arm_obj.data if arm_obj.type == 'ARMATURE' else None
    if bpy.context.view_layer.objects.active == arm_obj and arm_obj.mode != 'OBJECT':
        bpy.ops.object.mode_set(mode='OBJECT')
    bpy.data.objects.remove(arm_obj, do_unlink=True)
    if arm_data and arm_data.name in bpy.data.armatures and arm_data.users == 0:
        bpy.data.armatures.remove(arm_data)


@timed("create_armature")
def _create_armature_impl(bone_names, bonesTransformedPos, parent_indices, object_name, target_coll,
                    verticesTransformedPos=None, vertex_owners=None,
                    explicit_tail_positions=None, roll_reference_vectors=None,
                    creation_policy="CREATE_NEW", existing_armature=None,
                    world_matrix=None):
    """Create an armature object with optional explicit tails and roll references.

    ``creation_policy`` selects how an existing generated armature is handled:
    - ``CREATE_NEW``: always create a fresh armature (default, preserves any old rig).
    - ``REPLACE_GENERATED``: build a replacement for a previously generated
      armature; the adapter removes the old object only after assignment succeeds.
    - ``UPDATE_GENERATED``: rebuild a temporary data block and swap it into the
      existing generated armature object after construction succeeds.
    - ``CANCEL_IF_RIGGED``: refuse creation when the mesh already has a generated armature.

    ``world_matrix`` sets the armature's world transform; bones are authored in the
    mesh's local space (matching ``bonesTransformedPos``), so the armature must share
    the mesh's world matrix for the resulting pose to match the mesh in world space.
    """
    if creation_policy == "CANCEL_IF_RIGGED" and existing_armature is not None:
        return None
    if len(bone_names) == 0:
        raise ValueError("Cannot create an armature without bones.")
    if len(bonesTransformedPos) != len(bone_names) or len(parent_indices) != len(bone_names):
        raise ValueError("Armature bone, position, and parent arrays must have matching lengths.")
    for parent_idx in parent_indices:
        if int(parent_idx) < -1 or int(parent_idx) >= len(bone_names):
            raise ValueError(f"Invalid armature parent index: {parent_idx}")

    update_target = None

    # UPDATE_GENERATED builds a temporary armature first. The existing object
    # is swapped to the new data only after all edit-bone operations succeed.
    # This keeps a failed update from destroying the previous skeleton.
    if creation_policy == "UPDATE_GENERATED" and existing_armature is not None:
        if existing_armature.type != 'ARMATURE':
            raise ValueError("UPDATE_GENERATED requires an armature object.")
        update_target = existing_armature

    # CREATE_NEW, REPLACE_GENERATED, and UPDATE_GENERATED all build a fresh
    # data block here. REPLACE cleanup is intentionally deferred until the new
    # rig has been assigned successfully by the reconstruction adapter.
    arm_data = bpy.data.armatures.new(f"{object_name}_Armature")
    arm_obj = bpy.data.objects.new(f"{object_name}_ArmatureObj", arm_data)
    coll = target_coll or bpy.context.scene.collection
    coll.objects.link(arm_obj)
    _activate_armature_for_edit(arm_obj)
    edit_bones = arm_obj.data.edit_bones
    bone_list = []
    for i, name in enumerate(bone_names):
        bone = edit_bones.new(name)
        x, y, z = bonesTransformedPos[i]
        bone.head = (x, y, z)
        bone_list.append(bone)

    # Gather vertices for each bone group to compute PCA if they are leaf bones
    group_vertices_map = {}
    if verticesTransformedPos is not None and vertex_owners is not None:
        v_arr = np.array(verticesTransformedPos, dtype=np.float64)
        owners_arr = np.array(vertex_owners, dtype=np.int32).reshape(-1)
        for i in range(len(bone_names)):
            bone_v_indices = np.where(owners_arr == i)[0]
            if bone_v_indices.size >= 2:
                group_vertices_map[i] = v_arr[bone_v_indices]

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
    
    model_extent = 0.0
    if verticesTransformedPos is not None and len(verticesTransformedPos) > 0:
        model_extent = float(np.linalg.norm(np.ptp(np.asarray(verticesTransformedPos, dtype=np.float64), axis=0)))
    global_median = float(np.median(all_distances)) if all_distances else max(model_extent * 0.5, 1e-8)
    scale = max(global_median, model_extent, 1e-8)
    min_len = max(scale * 1e-4, 1e-8)

    # 5. Set Parents and Calculate Tails
    explicit_tails = None
    if explicit_tail_positions is not None:
        candidate_tails = np.asarray(explicit_tail_positions, dtype=np.float64)
        if candidate_tails.shape == (len(bone_names), 3) and np.isfinite(candidate_tails).all():
            explicit_tails = candidate_tails

    for i, bone in enumerate(bone_list):
        parent_idx = parent_indices[i]
        if parent_idx != -1:
            bone.parent = bone_list[parent_idx]

        children = children_map[i]
        my_head = mathutils.Vector(bone.head)
        used_pca = False  # Tracks whether we used a local PCA-derived direction

        # Topology proposals provide deterministic heads and tails directly.
        if explicit_tails is not None:
            target_tail = mathutils.Vector(explicit_tails[i])
            direction = target_tail - my_head
            if direction.length <= min_len:
                direction = model_forward * min_len
                target_tail = my_head + direction
            bone.tail = target_tail
            bone.use_connect = False
            if parent_idx != -1:
                parent_tail = mathutils.Vector(explicit_tails[parent_idx])
                bone.use_connect = (parent_tail - my_head).length <= 1e-5
            if roll_reference_vectors is not None and i < len(roll_reference_vectors):
                _align_bone_roll(bone, roll_reference_vectors[i])
            continue

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
        
        # Priority 2: Leaf Bone (Pointing consistent with PCA or Parent)
        else:
            bone.use_connect = False
            if parent_idx != -1:
                p_bone = bone_list[parent_idx]
                p_head = mathutils.Vector(p_bone.head)
                # Direction from parent to me
                direction = my_head - p_head
                
                # SVD PCA-based leaf direction
                local_dir = None
                if i in group_vertices_map:
                    local_dir = _calculate_pca_direction(group_vertices_map[i], direction)
                    
                if local_dir is not None:
                    # Point along PCA direction; length scales with overall bone sizes
                    len_val = max(direction.length * 0.5, global_median * 0.3)
                    bone.tail = my_head + (local_dir * max(len_val, min_len))
                    used_pca = True
                elif direction.length > 0.001:
                    bone.tail = my_head + (direction.normalized() * max(direction.length * 0.5, min_len))
                else:
                    bone.tail = my_head + (model_forward * min_len)
            else:
                # Root leaf (rare): Floor bone or single bone model
                bone.tail = my_head + (model_forward * global_median * 0.5)

        # Final safety check is per-bone. A PCA failure must never leave a
        # zero-length EditBone, and the old function-scope check only examined
        # the final bone in the loop.
        if (mathutils.Vector(bone.tail) - my_head).length < min_len:
            bone.tail = my_head + (model_forward * min_len)

    # Apply world transform (mesh-local bone space, armature shares mesh matrix).
    if world_matrix is not None:
        arm_obj.matrix_world = world_matrix

    bpy.ops.object.mode_set(mode='OBJECT')

    if update_target is not None:
        old_data = update_target.data
        new_data = arm_obj.data
        update_key = update_target.as_pointer()
        if update_key in _PENDING_ARMATURE_UPDATES:
            raise RuntimeError("Armature already has a pending reconstruction update.")
        _PENDING_ARMATURE_UPDATES[update_key] = (
            old_data,
            new_data,
            update_target.matrix_world.copy(),
        )
        update_target.data = new_data
        update_target.matrix_world = world_matrix if world_matrix is not None else arm_obj.matrix_world
        _remove_armature_object(arm_obj)
        # Keep the old data, action, and constraints until the adapter commits.
        # A later weight/metadata/modifier failure can then restore the exact rig.
        arm_obj = update_target

    return arm_obj


def create_armature(bone_names, bonesTransformedPos, parent_indices, object_name, target_coll,
                    verticesTransformedPos=None, vertex_owners=None,
                    explicit_tail_positions=None, roll_reference_vectors=None,
                    creation_policy="CREATE_NEW", existing_armature=None,
                    world_matrix=None):
    """Construct an armature transactionally and restore the Blender context."""
    snapshot = _capture_blender_context()
    existing_object_names = {obj.name for obj in bpy.data.objects}
    try:
        return _create_armature_impl(
            bone_names,
            bonesTransformedPos,
            parent_indices,
            object_name,
            target_coll,
            verticesTransformedPos=verticesTransformedPos,
            vertex_owners=vertex_owners,
            explicit_tail_positions=explicit_tail_positions,
            roll_reference_vectors=roll_reference_vectors,
            creation_policy=creation_policy,
            existing_armature=existing_armature,
            world_matrix=world_matrix,
        )
    except Exception:
        # The implementation builds replacement data before touching an
        # existing UPDATE target. Remove only objects created by this call.
        active = bpy.context.view_layer.objects.active
        if active and active.mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
        for arm_obj in list(bpy.data.objects):
            if (
                arm_obj.name not in existing_object_names
                and arm_obj.type == 'ARMATURE'
            ):
                _remove_armature_object(arm_obj)
        raise
    finally:
        _restore_blender_context(snapshot)


def get_bone_roll(bone):
    """Return the final stored roll for a data Bone without entering Edit Mode."""
    try:
        _, roll = bone.AxisRollFromMatrix(
            bone.matrix_local.to_3x3(), axis=bone.y_axis
        )
        return float(roll)
    except (AttributeError, TypeError, ValueError, RuntimeError):
        return 0.0


def _align_bone_roll(bone, reference):
    """Align roll to a projected reference with a deterministic fallback."""
    reference = mathutils.Vector(reference)
    axis = mathutils.Vector(bone.tail) - mathutils.Vector(bone.head)
    if reference.length <= 1e-8 or axis.length <= 1e-8:
        return
    axis.normalize()
    projected = reference - axis * reference.dot(axis)
    reference_sign = 1.0
    for component in reference:
        if abs(component) > 1e-8:
            reference_sign = 1.0 if component > 0.0 else -1.0
            break
    if projected.length <= 1e-8:
        candidates = (
            mathutils.Vector((1.0, 0.0, 0.0)),
            mathutils.Vector((0.0, 1.0, 0.0)),
            mathutils.Vector((0.0, 0.0, 1.0)),
        )
        fallback = min(candidates, key=lambda candidate: abs(axis.dot(candidate)))
        projected = axis.cross(fallback) * reference_sign
    if projected.length <= 1e-8:
        return
    try:
        bone.align_roll(projected.normalized())
    except ValueError:
        # Blender can reject a numerically parallel vector. Try the other
        # canonical axes before accepting the stable default roll.
        for candidate in (
            mathutils.Vector((1.0, 0.0, 0.0)),
            mathutils.Vector((0.0, 1.0, 0.0)),
            mathutils.Vector((0.0, 0.0, 1.0)),
        ):
            projected = axis.cross(candidate) * reference_sign
            if projected.length <= 1e-8:
                continue
            try:
                bone.align_roll(projected.normalized())
                return
            except ValueError:
                continue


@timed("assign_armature_modifier")
def assign_armature_modifier(mesh_obj, armature_obj):
    """Attach one armature modifier while preserving the mesh world matrix."""
    matching = [
        modifier for modifier in mesh_obj.modifiers
        if modifier.type == 'ARMATURE' and modifier.object == armature_obj
    ]
    mod = matching[0] if matching else mesh_obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = armature_obj
    for duplicate in matching[1:]:
        mesh_obj.modifiers.remove(duplicate)

    world_matrix = mesh_obj.matrix_world.copy()
    mesh_obj.parent = armature_obj
    mesh_obj.matrix_parent_inverse = armature_obj.matrix_world.inverted()
    mesh_obj.matrix_world = world_matrix
    return mod


def finalize_reconstruction_lifecycle(mesh_obj, old_armature, new_armature, creation_policy):
    """Commit generated-rig replacement after the new binding is successful.

    The old object is never touched until the caller has created and assigned
    the replacement. CREATE_NEW keeps the old armature datablock available but
    removes its active modifier from this mesh; REPLACE_GENERATED removes the
    old object only when no other object still references it.
    """
    if old_armature is None:
        return

    if old_armature == new_armature:
        if creation_policy == "UPDATE_GENERATED":
            if old_armature.animation_data:
                old_armature.animation_data_clear()
            for constraint in list(old_armature.constraints):
                old_armature.constraints.remove(constraint)
            _commit_pending_armature_update(old_armature)
        return

    if creation_policy in {"CREATE_NEW", "REPLACE_GENERATED"}:
        for modifier in list(mesh_obj.modifiers):
            if modifier.type == 'ARMATURE' and modifier.object == old_armature:
                mesh_obj.modifiers.remove(modifier)

    if creation_policy != "REPLACE_GENERATED":
        return

    other_users = []
    for obj in bpy.data.objects:
        if obj == mesh_obj or obj == old_armature:
            continue
        if obj.parent == old_armature or any(
            modifier.type == 'ARMATURE' and modifier.object == old_armature
            for modifier in obj.modifiers
        ):
            other_users.append(obj)
            continue
        for constraint in obj.constraints:
            direct_target = getattr(constraint, "target", None)
            if direct_target == old_armature:
                other_users.append(obj)
                break
            targets = getattr(constraint, "targets", None)
            if targets and any(getattr(target, "target", None) == old_armature for target in targets):
                other_users.append(obj)
                break
        if obj in other_users:
            continue
        animation_data = getattr(obj, "animation_data", None)
        if animation_data:
            driver_target_found = any(
                getattr(target, "id", None) == old_armature
                for fcurve in animation_data.drivers
                for variable in fcurve.driver.variables
                for target in variable.targets
            )
            if driver_target_found:
                other_users.append(obj)
    if other_users:
        warn(
            f"Keeping replaced armature '{old_armature.name}' because it is still "
            f"referenced by {len(other_users)} other object(s)."
        )
        return

    if old_armature.animation_data:
        old_armature.animation_data_clear()
    for constraint in list(old_armature.constraints):
        old_armature.constraints.remove(constraint)
    old_data = old_armature.data if old_armature.type == 'ARMATURE' else None
    if old_armature.name in bpy.data.objects:
        bpy.data.objects.remove(old_armature, do_unlink=True)
    if old_data and old_data.name in bpy.data.armatures and old_data.users == 0:
        bpy.data.armatures.remove(old_data)


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
    image.pixels.foreach_set(np.asarray(texture, dtype=np.float32).ravel())
    # Bulk pixel writes do not fully invalidate Blender's display/GPU cache for
    # generated images. Flush the buffer, pack it, then reload the packed image;
    # this is the programmatic equivalent of the Alt+R required to clear a
    # black texture in the Image Editor.
    image.update()
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
    # Exporters only read the result, so an already-triangular source needs no
    # full mesh copy at all.
    loop_totals = np.empty(len(mesh.polygons), dtype=np.int32)
    if loop_totals.size:
        mesh.polygons.foreach_get("loop_total", loop_totals)
    if np.all(loop_totals == 3):
        return mesh

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        if bm.faces:
            bmesh.ops.triangulate(
                bm,
                faces=bm.faces[:],
                quad_method='BEAUTY',
                ngon_method='BEAUTY',
            )
        tmp = bpy.data.meshes.new(f"{mesh.name}_triangulated")
        try:
            bm.to_mesh(tmp)
        except Exception:
            bpy.data.meshes.remove(tmp)
            raise
        return tmp
    finally:
        bm.free()

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
    
    # Bulk-read pixels without first materializing a Python sequence.
    expected_len = width * height * 4
    pixels = np.empty(expected_len, dtype=np.float32)
    image.pixels.foreach_get(pixels)
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
    vertex_owners = np.zeros(len(obj.data.vertices), dtype=np.int16)
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

            # 2. Resolve each vertex-group name once, then assign dominant owners.
            group_target_indices = {}
            for vertex_group in obj.vertex_groups:
                vg_name = vertex_group.name
                target_idx = bone_index_map.get(vg_name, -1)
                if target_idx == -1:
                    vg_clean = vg_name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', vg_name) else vg_name
                    candidates = clean_name_map.get(vg_clean)
                    if candidates:
                        target_idx = candidates[0]
                group_target_indices[vertex_group.index] = target_idx

            unmatched_vertices = []
            for v in obj.data.vertices:
                winning_bone_idx = -1
                highest_weight = -1.0

                for g in v.groups:
                    target_idx = group_target_indices.get(g.group, -1)
                    if target_idx != -1 and g.weight > highest_weight:
                        highest_weight = g.weight
                        winning_bone_idx = target_idx
                
                if winning_bone_idx != -1:
                    vertex_owners[v.index] = winning_bone_idx
                elif v.groups:
                    unmatched_vertices.append(v.index)

            if unmatched_vertices:
                warn(f"{len(unmatched_vertices)} vertices assigned to root bone (no matching bone found).")
                vertex_owners[unmatched_vertices] = 0

            # 3. Use the reconstruction mapping when available. Generated
            # duplicate names such as ``.1`` are intentional and must not be
            # stripped as if they were Blender's legacy ``.001`` recycling.
            name_map = {}
            try:
                entries = json.loads(arm.get("carnivores_reconstruct_bone_name_map", "[]"))
                if isinstance(entries, list) and len(entries) == len(bone_names):
                    candidate_map = {
                        str(entry["blender_name"]): str(entry["export_name"])
                        for entry in entries
                        if (
                            isinstance(entry, dict)
                            and entry.get("blender_name")
                            and entry.get("export_name")
                        )
                    }
                    if set(candidate_map) == set(bone_names):
                        name_map = candidate_map
            except (TypeError, ValueError, KeyError):
                name_map = {}

            final_names = []
            for name in bone_names:
                if name in name_map:
                    export_name = name_map[name]
                    final_names.append(export_name.encode("ascii", "ignore")[:31].decode("ascii"))
                else:
                    clean = name.rsplit('.', 1)[0] if re.match(r'.*\.\d{3}$', name) else name
                    final_names.append(clean.encode("ascii", "ignore")[:31].decode("ascii"))

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
            vertex_owners = np.zeros(len(obj.data.vertices), dtype=np.int16)
            bone_index_map = {}

            for i, (name, hook_obj) in enumerate(temp_list):
                bone_names.append(name)
                bone_index_map[name] = i

            for i, (name, hook_obj) in enumerate(temp_list):
                if hook_obj.parent:
                    p_name = hook_obj.parent.name
                    if p_name in bone_index_map:
                        bone_parents[i] = bone_index_map[p_name]

            group_target_indices = {
                vertex_group.index: bone_index_map.get(vertex_group.name, -1)
                for vertex_group in obj.vertex_groups
            }
            for v in obj.data.vertices:
                winning_idx = 0
                max_w = -1.0
                for g in v.groups:
                    target_idx = group_target_indices.get(g.group, -1)
                    if target_idx != -1 and g.weight > max_w:
                        max_w = g.weight
                        winning_idx = target_idx
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
    """Build a lossless raw-to-compact CAR owner mapping.

    The parsed structured vertex array remains unchanged. Signed owner -1 stays
    unowned, while owner zero remains a valid source group.
    """
    mapping = build_owner_mapping(vertices['owner'])
    if mapping.group_count == 0:
        return vertices, np.array([], dtype='U32'), mapping

    bone_names = np.asarray(mapping.bone_names, dtype='U32')
    return vertices, bone_names, mapping