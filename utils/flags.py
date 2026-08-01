import bpy
import numpy as np
import bmesh
from ..core.constants import FACE_FLAG_OPTIONS
from .common import timed


# Applied in FACE_FLAG_OPTIONS order so overlapping flags always produce the
# same averaged result.  The final entry uses the serialized 0x8000 Dark bit,
# not a positional 0x0100 bit.
FLAG_TINTS = (
    (1, (1.0, 0.0, 1.0, 1.0)),      # Double Side: magenta
    (2, (0.0, 1.0, 0.0, 1.0)),      # Dark Back: green
    (4, (0.0, 0.0, 1.0, 1.0)),      # Opacity: blue
    (8, (1.0, 1.0, 0.0, 1.0)),      # Transparent: yellow
    (16, (1.0, 0.0, 0.0, 1.0)),     # Mortal: red
    (32, (0.0, 1.0, 1.0, 1.0)),     # Phong: cyan
    (64, (0.5, 0.5, 0.5, 1.0)),     # Env Map: gray
    (128, (1.0, 0.5, 0.0, 1.0)),    # Need VC: orange
    (32768, (0.0, 0.0, 0.0, 1.0)),  # Dark: black
)

# Human-readable names for the viewport legend. Keep these aligned with
# FLAG_TINTS so the legend describes the same deterministic colors generated
# by get_flag_color() and update_flag_colors().
FLAG_COLOR_NAMES = (
    (1, "Magenta"),
    (2, "Green"),
    (4, "Blue"),
    (8, "Yellow"),
    (16, "Red"),
    (32, "Cyan"),
    (64, "Gray"),
    (128, "Orange"),
    (32768, "Black"),
)


def _valid_flag_attribute(mesh, attr_name="3df_flags"):
    attr = mesh.attributes.get(attr_name) if mesh else None
    if not attr or attr.domain != 'FACE' or attr.data_type != 'INT':
        return False
    if len(attr.data) == len(mesh.polygons):
        return True

    # In Edit Mode Blender exposes the live attribute through BMesh while the
    # Mesh RNA attribute array can temporarily have zero elements.
    edit_obj = getattr(bpy.context, "edit_object", None)
    if edit_obj is None or edit_obj.type != 'MESH' or edit_obj.data != mesh:
        return False
    try:
        bm = bmesh.from_edit_mesh(mesh)
        return (
            len(bm.faces) == len(mesh.polygons)
            and bm.faces.layers.int.get(attr_name) is not None
        )
    except (ReferenceError, RuntimeError, TypeError):
        return False


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

    if not _valid_flag_attribute(mesh, attr_name):
        return counts, 0
    attr = mesh.attributes.get(attr_name)

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

@timed("count_matching_faces")
def count_matching_faces(obj, mask, mode='ANY', attr_name="3df_flags"):
    """Count faces matching a mask in the same all-face scope used by the selector."""
    if not obj or obj.type != 'MESH' or not _valid_flag_attribute(obj.data, attr_name):
        return 0, 0
    mask = int(mask)
    if mask == 0:
        return 0, 0

    mesh = obj.data
    if obj.mode == 'EDIT':
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        layer = bm.faces.layers.int.get(attr_name)
        if not layer:
            return 0, 0
        # The selector searches every face in Edit Mode and then applies the
        # selected action to matching faces.  The preview must use the same
        # scope rather than only the faces currently selected in the viewport.
        values = np.asarray([face[layer] for face in bm.faces], dtype=np.int64)
    else:
        values = np.empty(len(mesh.polygons), dtype=np.int32)
        mesh.attributes[attr_name].data.foreach_get("value", values)

    if mode == 'ANY':
        matches = (values & mask) != 0
    elif mode == 'ALL':
        matches = (values & mask) == mask
    elif mode == 'NONE':
        matches = (values & mask) == 0
    else:
        return 0, int(values.size)
    return int(np.count_nonzero(matches)), int(values.size)


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

def get_flag_color(flags):
    """
    Calculate RGBA color based on 3DF flags.
    Based on io_carnivores.py logic but adapted for bitmasks.
    """
    # Base color: White
    color = np.array([1.0, 1.0, 1.0, 1.0], dtype=np.float32)

    for mask, tint in FLAG_TINTS:
        if flags & mask:
            # Simple averaging blend (matches reference)
            # (current + tint) / 2
            color = (color + np.array(tint, dtype=np.float32)) * 0.5

    return color

@timed("update_flag_colors")
def update_flag_colors(mesh):
    """
    Updates the 'FlagColors' vertex color attribute based on '3df_flags'.
    Creates the attribute if it doesn't exist.
    """
    if not mesh:
        return

    # Ensure 3df_flags exists
    attr_flags = mesh.attributes.get("3df_flags")
    if not attr_flags or attr_flags.domain != 'FACE' or attr_flags.data_type != 'INT':
        return

    edit_obj = getattr(bpy.context, "edit_object", None)
    if edit_obj is not None and edit_obj.type == 'MESH' and edit_obj.data == mesh:
        # Mesh attribute arrays are not populated while their owner is in Edit
        # Mode. Read and write the live BMesh layers instead so flag editing can
        # refresh visualization without leaving Edit Mode.
        bm = bmesh.from_edit_mesh(mesh)
        bm.faces.ensure_lookup_table()
        flag_layer = bm.faces.layers.int.get("3df_flags")
        if flag_layer is None:
            return

        color_attr = mesh.attributes.get("FlagColors")
        if color_attr is None or color_attr.data_type == 'BYTE_COLOR':
            color_layer = bm.loops.layers.color.get("FlagColors")
            if color_layer is None:
                color_layer = bm.loops.layers.color.new("FlagColors")
        elif color_attr.data_type == 'FLOAT_COLOR':
            color_layer = bm.loops.layers.float_color.get("FlagColors")
            if color_layer is None:
                color_layer = bm.loops.layers.float_color.new("FlagColors")
        else:
            return

        for face in bm.faces:
            color = tuple(float(channel) for channel in get_flag_color(int(face[flag_layer])))
            for loop in face.loops:
                loop[color_layer] = color
        bmesh.update_edit_mesh(mesh, loop_triangles=False, destructive=False)
        return

    # Get flags as numpy array
    face_count = len(mesh.polygons)
    flags = np.zeros(face_count, dtype=np.int32)
    attr_flags.data.foreach_get("value", flags)

    # Ensure FlagColors attribute exists (Color Attribute in newer Blender)
    # domain='CORNER' is standard for vertex colors
    if "FlagColors" not in mesh.attributes:
        mesh.attributes.new(name="FlagColors", type='BYTE_COLOR', domain='CORNER')
    
    attr_colors = mesh.attributes["FlagColors"]

    # Calculate colors for all faces
    # Vectorization Strategy:
    # 1. Pre-calculate the color for every unique flag combination found?
    #    OR just iterate. Iterating Python might be slow for high poly.
    #    Let's try a numpy approach.
    
    # Construct an array of shape (face_count, 4) initialized to white
    colors = np.ones((face_count, 4), dtype=np.float32)

    # Apply tints vectorially
    # Tints logic: color = (color + tint) / 2  => color * 0.5 + tint * 0.5
    
    tints_map = tuple(
        (mask, np.asarray(tint, dtype=np.float32))
        for mask, tint in FLAG_TINTS
    )

    for mask, tint in tints_map:
        # Find indices where this flag is set
        mask_indices = (flags & mask) != 0
        if np.any(mask_indices):
            # Apply blend
            colors[mask_indices] = (colors[mask_indices] + tint) * 0.5

    # Prepare Loop Colors
    # Vertex Colors are stored per Loop (Corner).
    # We need to map Face Index -> Loop Indices.
    # Mesh.loops has 'vertex_index' and 'edge_index'.
    # Mesh.polygons has 'loop_start' and 'loop_total'.
    
    loop_count = len(mesh.loops)
    
    # We need an array of colors matching the loop count.
    # We can create a mapping from Loop Index -> Face Index.
    # Or simpler: Expand face colors to loops.
    
    # 1. Get loop counts per face to repeat the face colors appropriately
    # loop_totals = np.zeros(face_count, dtype=np.int32)
    # mesh.polygons.foreach_get("loop_total", loop_totals)
    
    # 2. Repeat face colors. `np.repeat` repeats elements.
    # loop_colors_float = np.repeat(colors, loop_totals, axis=0)
    
    # Wait! The above assumes the loops are stored contiguously per face (0,1,2 then 3,4,5).
    # Blender guarantees this for `mesh.loops` when accessed sequentially via polygon.loop_start
    # ONLY IF the mesh is compact. Usually yes.
    # Let's verify: mesh.polygons[i] uses loops[loop_start : loop_start + loop_total].
    # So yes, if we iterate faces in order, we encounter loops in order.
    # BUT `np.repeat` works on the array logic.
    
    # Let's check if loop_totals is constant? No, can be quads/tris.
    loop_totals = np.zeros(face_count, dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", loop_totals)
    
    loop_colors_float = np.repeat(colors, loop_totals, axis=0)
    
    # Flatten to 1D array for foreach_set
    loop_colors_flat = loop_colors_float.flatten()
    
    # Set the colors
    attr_colors.data.foreach_set("color", loop_colors_flat)