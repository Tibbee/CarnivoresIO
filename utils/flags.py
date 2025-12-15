import bpy
import numpy as np
import bmesh
from ..core.constants import FACE_FLAG_OPTIONS
from .common import timed

@timed("assign_face_flag")
def assign_face_flag_int(mesh: bpy.types.Mesh, face_flags, attr_name="3df_flags"):
    # Create or get the attribute
    attr = mesh.attributes.new(name=attr_name, type='INT', domain='FACE')

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
