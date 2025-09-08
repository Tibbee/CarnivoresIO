
import numpy as np
import os

def detect_bone_cycles(parents, bone_count):
    def has_cycle(node, visited, path):
        if node in path:
            return True
        if node in visited or node == -1:
            return False
        visited.add(node)
        path.add(node)
        if node < bone_count:
            if has_cycle(parents[node], visited, path):
                return True
        path.remove(node)
        return False

    for i in range(bone_count):
        visited = set()
        path = set()
        if has_cycle(i, visited, path):
            return i
    return -1

def validate_3df_header(header, filepath, context):
    # All counts must be non-negative (redundant for uint32 but explicit)
    if any(val < 0 for val in header):
        raise ValueError("Header contains negative values (corrupted file?).")

    # Zero or Negative Checks
    if header['vertex_count'] <= 0 or header['face_count'] <= 0 or header['bone_count'] < 0:
        raise ValueError("Counts must be non-negative; vertex/face >0.")

    # Bounds checks (aligned with spec/tool limits)
    if header['vertex_count'] > 2048:
        raise ValueError("Vertex count exceeds max allowed (2048).")
    elif header['vertex_count'] > 1024:
        context.warnings.append(
            f"High vertex count: {header['vertex_count']}. Above 1024 AltEdit cannot open the file."
        )

    if header['face_count'] > 2048:
        raise ValueError("Face count exceeds max allowed (2048).")
    elif header['face_count'] > 1024:
        context.warnings.append(
            f"High face count: {header['face_count']}. Above 1024 AltEdit cannot open the file."
        )

    if header['bone_count'] > 2048:
        raise ValueError("Bone count exceeds reasonable max (2048).")
    elif header['bone_count'] > 1024:
        context.warnings.append(
            f"High bone count: {header['bone_count']}. Above 1024 may cause issues in tools."
        )

    if header['texture_size'] > 131072:
        raise ValueError("Texture size exceeds max allowed (131072 bytes).")

    # Texture alignment and height sanity
    if header['texture_size'] % 512 != 0:
        raise ValueError("Texture size not aligned to 256-pixel-wide ARGB1555 format (must be multiple of 512).")
    texture_height = header['texture_size'] // 512
    if texture_height > 512:
        context.warnings.append(
            f"Unusually high texture height: {texture_height}px (may indicate corruption)."
        )

    # File size check (expanded for trailing bytes)
    file_size = os.path.getsize(filepath)
    expected_size = (
        16 +
        header['vertex_count'] * 16 +
        header['face_count'] * 64 +
        header['bone_count'] * 48 +
        header['texture_size']
    )
    if file_size < expected_size:
        raise ValueError(
            f"File too small. Expected ≥ {expected_size} bytes, got {file_size}."
        )
    elif file_size > expected_size:
        context.warnings.append(
            f"File has {file_size - expected_size} extra bytes at end (trailing garbage or corruption?)."
        )

def validate_3df_vertices(vertices, vertex_count, bone_count, context):
    if vertices.size != vertex_count:
        raise ValueError(f"Expected {vertex_count} vertices, but got {vertices.size}")

    if not np.isfinite(vertices['coord']).all():
        raise ValueError("Vertex coordinates contain NaN or infinite values.")

    # Check hide field (warn if non-zero, as it has no in-game effect)
    if np.any(vertices['hide'] != 0):
        count_hidden = np.count_nonzero(vertices['hide'])
        context.warnings.append(
            f"{count_hidden} vertices have non-zero 'hide' values (no in-game effect, likely editor-specific)."
        )

    if bone_count > 0:
        invalid_owner = (vertices['owner'] >= bone_count)
        if invalid_owner.any():
            count_invalid = np.count_nonzero(invalid_owner)
            context.warnings.append(
                f"{count_invalid} vertices have invalid owner indices (≥ {bone_count}); clamped to 0."
            )
            vertices['owner'][invalid_owner] = 0
    else:
        if np.any(vertices['owner'] != 0):
            context.warnings.append("Bone count is zero; all vertex owners set to 0.")
            vertices['owner'][:] = 0

    return vertices  # In-place modified if needed

def validate_3df_faces(faces, face_count, vertex_count, texture_height, context):
    # Face count sanity
    if faces.shape[0] != face_count:
        raise ValueError(f"Expected {face_count} faces, but parsed {faces.shape[0]}")

    # Vertex index bounds
    idx = faces['v']
    if (idx < 0).any() or (idx >= vertex_count).any():
        bad = np.logical_or(idx < 0, idx >= vertex_count)
        n_bad = np.count_nonzero(bad)
        context.warnings.append(
            f"{n_bad} face-vertex indices out of range [0, {vertex_count-1}]; clamped."
        )
        faces['v'][bad] = np.clip(faces['v'][bad], 0, vertex_count - 1)

    # Degenerate faces (two or three identical vertex indices)
    v1, v2, v3 = idx[:, 0], idx[:, 1], idx[:, 2]
    degenerate = (v1 == v2) | (v2 == v3) | (v1 == v3)
    if degenerate.any():
        count_deg = np.count_nonzero(degenerate)
        context.warnings.append(f"{count_deg} degenerate faces detected (duplicate vertex indices).")

    # Raw UV range checks
    u_raw, v_raw = faces['u_tex'], faces['v_tex']
    if (u_raw > 255).any():
        n_bad = np.count_nonzero(u_raw > 255)
        context.warnings.append(f"{n_bad} U coords >255; clipped.")
        faces['u_tex'] = np.clip(u_raw, 0, 255)
    max_v = max(texture_height - 1, 0)
    if (v_raw > max_v).any():
        n_bad = np.count_nonzero(v_raw > max_v)
        context.warnings.append(f"{n_bad} V coords >{max_v}; clipped.")
        faces['v_tex'] = np.clip(v_raw, 0, max_v)

    # Flags field: warn if any unknown bits set
    known_mask = 0x0001 | 0x0002 | 0x0004 | 0x0008 | 0x0010 | 0x0020 | 0x0040 | 0x0080 | 0x8000
    flags = faces['flags']
    unknown = flags & ~known_mask
    if unknown.any():
        n_bad = np.count_nonzero(unknown != 0)
        context.warnings.append(f"{n_bad} faces have unknown flag bits set (mask: 0x{unknown[unknown != 0][0]:04X}).")

    # Check unused fields (dmask, distant, next, group, reserv)
    for field in ('dmask', 'distant', 'next', 'group'):
        arr = faces[field]
        nz = np.count_nonzero(arr)
        if nz:
            context.warnings.append(f"{nz} faces have non-zero '{field}' values (likely unused).")
    if np.any(faces['reserv'] != 0):
        context.warnings.append("Non-zero 'reserv' values detected in faces (likely unused).")

    return faces

def validate_3df_bones(bones, bone_count, context):
    # Count check
    parents = bones['parent']
    if bones.shape[0] != bone_count:
        raise ValueError(f"Parsed {bones.shape[0]} bones; expected {bone_count}.")

    # Decode names and check duplicates/empties
    decoded = []
    for i, raw in enumerate(bones['name']):
        s = raw.decode('ascii', errors='ignore').split('\x00', 1)[0]
        if not s:
            context.warnings.append(f"Bone #{i} has an empty name; using placeholder.")
            s = f"Bone_{i}"
        decoded.append(s)
    dupes = {n for n in decoded if decoded.count(n) > 1}
    if dupes:
        context.warnings.append(f"Duplicate bone names: {sorted(dupes)}. Blender may merge these.")

    # In validate_3df_bones, after parent index validation:
    # Existing parent validation
    invalid_parent = ~((parents == -1) | ((parents >= 0) & (parents < bone_count)))
    if invalid_parent.any():
        cnt = np.count_nonzero(invalid_parent)
        raise ValueError(f"{cnt} bones have invalid parent indices (must be -1 or 0..{bone_count-1}).")

    # Add cycle detection
    # Replace the cycle detection section
    cycle_start = detect_bone_cycles(bones['parent'], bone_count)
    if cycle_start != -1:
        context.warnings.append(f"Cycle detected in bone hierarchy starting at bone {cycle_start}. Clamping to -1.")
        # Break cycles by setting parent to -1 for any bone that would cause a cycle
        visited = set()
        path = set()
        def break_cycles(node):
            if node in path:
                bones['parent'][node] = -1
                return
            if node in visited or node == -1:
                return
            visited.add(node)
            path.add(node)
            if node < bone_count:
                break_cycles(bones['parent'][node])
            path.remove(node)
        for i in range(bone_count):
            visited.clear()
            path.clear()
            break_cycles(i)

    # Position sanity
    pos = bones['pos']
    if not np.isfinite(pos).all():
        raise ValueError("Bone positions contain NaN or infinite values.")

    # Hidden field (warn if non-zero, as it has no in-game effect)
    if np.any(bones['hidden'] != 0):
        count_hidden = np.count_nonzero(bones['hidden'])
        context.warnings.append(
            f"{count_hidden} bones have non-zero 'hidden' values (no in-game effect, likely editor-specific)."
        )

    return bones

def validate_3df_texture(texture_raw, texture_size, context):
    expected_length = texture_size // 2
    actual_length = texture_raw.size

    if actual_length != expected_length:
        raise ValueError(f"Texture data length {actual_length} does not match expected {expected_length}.")

    # Alpha channel check: bit 15 only 0 or 1
    alpha_bits = (texture_raw >> 15) & 0x1
    if not np.all(np.isin(alpha_bits, [0, 1])):
        context.warnings.append("Texture contains alpha bits outside expected 0 or 1.")

    # RGB channel range check (0..31)
    r = (texture_raw >> 10) & 0x1F
    g = (texture_raw >> 5) & 0x1F
    b = texture_raw & 0x1F
    if (r > 31).any() or (g > 31).any() or (b > 31).any():
        context.warnings.append("Texture RGB values out of expected range 0..31.")

    # Check for completely zeroed texture (potential corruption)
    if not np.any(texture_raw):
        context.warnings.append("Texture data is completely zero (black/transparent); possible corruption.")

    return texture_raw