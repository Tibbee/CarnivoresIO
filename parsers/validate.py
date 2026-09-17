"""Non-destructive structural and compatibility validation.

Structural checks protect the parser and Blender and are always run by the
importers. Optional compatibility checks report constraints of legacy tools and
the current C2 MEE loader; they never rewrite parsed source data.
"""

import os

import numpy as np

from ..core.constants import FACE_FLAG_OPTIONS, TEXTURE_WIDTH
from ..core.core import BONE_DTYPE, CAR_HEADER_DTYPE, FACE_DTYPE, HEADER_DTYPE, VERTEX_DTYPE


# Compatibility limits, not parser limits.
ALTEDIT_MESH_WARNING = 1024
C2_MEE_OBJECT_LIMIT = 1024
C2_MEE_ANIMATION_LIMIT = 64
C2_MEE_SOUND_LIMIT = 64
C2_MEE_TEXTURE_BYTES = TEXTURE_WIDTH * 256 * 2
TEXTURE_ROW_BYTES = TEXTURE_WIDTH * 2


def _warn(context, message):
    if message not in context.warnings:
        context.warnings.append(message)


# Serialized name fields (`.3df`/`.car`/`.3dn` model, bone, animation, and
# sound names) are 32-byte fixed-width ASCII. This is the single sanitization
# rule shared by parsers, validation, and exporters: decode ignoring invalid
# bytes, split at the first NUL (padding in the middle must not survive), then
# truncate by bytes — never by characters — to the field width.
SERIALIZED_NAME_BYTES = 32


def decode_serialized_name(raw_name):
    """Decode a fixed-width name field with the shared sanitization rule."""
    raw = np.asarray(raw_name).tobytes()
    return raw.decode('ascii', errors='ignore').split('\x00', 1)[0]


def truncate_serialized_name(name, max_bytes=SERIALIZED_NAME_BYTES):
    """Byte-aware ASCII-safe truncation for serialized name fields.

    Returns ``(name, truncated)`` where ``truncated`` is True when bytes were
    dropped. The result is always ASCII and never exceeds ``max_bytes``.
    """
    encoded = str(name).encode('ascii', errors='ignore')[:max_bytes]
    clean = encoded.decode('ascii', errors='ignore')
    return clean, len(clean) < len(str(name).encode('ascii', errors='ignore'))


def serialize_name(name, max_bytes=SERIALIZED_NAME_BYTES):
    """Encode a name into the fixed-width NUL-padded serialized form."""
    return str(name).encode('ascii', errors='ignore')[:max_bytes].ljust(max_bytes, b'\x00')


def _as_count(value, name):
    count = int(value)
    if count < 0:
        raise ValueError(f"{name} must not be negative (got {count}).")
    return count


def _require_file_bytes(filepath, required_size, section_name):
    file_size = os.path.getsize(filepath)
    if required_size > file_size:
        raise ValueError(
            f"File is truncated before {section_name}: requires at least "
            f"{required_size} bytes, got {file_size}."
        )
    return file_size


def _validate_texture_size(texture_size):
    texture_size = _as_count(texture_size, "Texture size")
    if texture_size % 2:
        raise ValueError("Texture size must contain complete 16-bit pixels.")
    if texture_size and texture_size % TEXTURE_ROW_BYTES:
        raise ValueError(
            f"Texture size {texture_size} is not a whole {TEXTURE_WIDTH}-pixel ARGB1555 row "
            f"(multiple of {TEXTURE_ROW_BYTES} bytes)."
        )
    return texture_size


def _report_mesh_compatibility(vertex_count, face_count, context):
    if vertex_count > ALTEDIT_MESH_WARNING:
        _warn(
            context,
            f"Model has {vertex_count} vertices; legacy AltEdit may not open models above "
            f"{ALTEDIT_MESH_WARNING}. The addon and current C2 MEE model arrays do not use this limit.",
        )
    if face_count > ALTEDIT_MESH_WARNING:
        _warn(
            context,
            f"Model has {face_count} faces; legacy AltEdit may not open models above "
            f"{ALTEDIT_MESH_WARNING}. The addon and current C2 MEE model arrays do not use this limit.",
        )


def _report_texture_compatibility(texture_size, context):
    if texture_size != C2_MEE_TEXTURE_BYTES:
        consequence = (
            "would overflow that buffer"
            if texture_size > C2_MEE_TEXTURE_BYTES
            else "does not initialize the complete buffer"
        )
        _warn(
            context,
            f"Texture contains {texture_size} bytes; the current C2 MEE OpenGL loader uses a "
            f"{TEXTURE_WIDTH}x256 buffer ({C2_MEE_TEXTURE_BYTES} bytes), so this file {consequence}. "
            "The software loader and addon can still handle complete variable-height rows.",
        )


def detect_bone_cycles(parents, bone_count):
    """Return one node participating in a parent cycle, or -1."""
    state = np.zeros(bone_count, dtype=np.uint8)  # 0=new, 1=active, 2=done

    for start in range(bone_count):
        if state[start] != 0:
            continue
        path = []
        node = start
        while node != -1 and state[node] == 0:
            state[node] = 1
            path.append(node)
            node = int(parents[node])
        if node != -1 and state[node] == 1:
            return node
        for visited in path:
            state[visited] = 2
    return -1


def validate_3df_header(header, filepath, context, compatibility=True):
    vertex_count = _as_count(header['vertex_count'], "Vertex count")
    face_count = _as_count(header['face_count'], "Face count")
    bone_count = _as_count(header['bone_count'], "Object/bone count")
    texture_size = _validate_texture_size(header['texture_size'])

    if vertex_count == 0 or face_count == 0:
        raise ValueError("3DF vertex and face counts must be greater than zero.")

    required_size = (
        HEADER_DTYPE.itemsize
        + face_count * FACE_DTYPE.itemsize
        + vertex_count * VERTEX_DTYPE.itemsize
        + bone_count * BONE_DTYPE.itemsize
        + texture_size
    )
    file_size = _require_file_bytes(filepath, required_size, "3DF payload")
    if file_size > required_size:
        _warn(context, f"3DF file has {file_size - required_size} trailing bytes; preserving them is not supported on export.")

    if compatibility:
        _report_mesh_compatibility(vertex_count, face_count, context)
        _report_texture_compatibility(texture_size, context)
        if bone_count > C2_MEE_OBJECT_LIMIT:
            _warn(
                context,
                f"Model has {bone_count} object/bone records; current C2 MEE stores standalone "
                f"model objects in gObj[{C2_MEE_OBJECT_LIMIT}]. Loading this file there is unsafe.",
            )
    return header


def validate_car_header(header, filepath, context, compatibility=True):
    animation_count = _as_count(header['ani_count'], "Animation count")
    sound_count = _as_count(header['sfx_count'], "Sound count")
    vertex_count = _as_count(header['vertex_count'], "Vertex count")
    face_count = _as_count(header['face_count'], "Face count")
    texture_size = _validate_texture_size(header['texture_size'])

    if vertex_count == 0 or face_count == 0:
        raise ValueError("CAR vertex and face counts must be greater than zero.")

    model_end = (
        CAR_HEADER_DTYPE.itemsize
        + face_count * FACE_DTYPE.itemsize
        + vertex_count * VERTEX_DTYPE.itemsize
        + texture_size
    )
    _require_file_bytes(filepath, model_end, "CAR model payload")

    if compatibility:
        _report_mesh_compatibility(vertex_count, face_count, context)
        _report_texture_compatibility(texture_size, context)
        if animation_count > C2_MEE_ANIMATION_LIMIT:
            _warn(
                context,
                f"CAR contains {animation_count} animations; current C2 MEE stores only "
                f"{C2_MEE_ANIMATION_LIMIT} TAni entries. Loading this file there is unsafe.",
            )
        if sound_count > C2_MEE_SOUND_LIMIT:
            _warn(
                context,
                f"CAR contains {sound_count} sounds; current C2 MEE stores only "
                f"{C2_MEE_SOUND_LIMIT} TSFX entries. Loading this file there is unsafe.",
            )
    return header


def _validate_vertex_fields(vertices, vertex_count, context, compatibility=True):
    if vertices.size != vertex_count:
        raise ValueError(f"Expected {vertex_count} vertices, but got {vertices.size}.")
    if not np.isfinite(vertices['coord']).all():
        raise ValueError("Vertex coordinates contain NaN or infinite values.")
    if compatibility and np.any(vertices['hide'] != 0):
        _warn(context, f"{np.count_nonzero(vertices['hide'])} vertices have non-zero hide values.")
    return vertices


def validate_3df_vertices(vertices, vertex_count, bone_count, context, compatibility=True):
    _validate_vertex_fields(vertices, vertex_count, context, compatibility)
    owners = np.asarray(vertices['owner'], dtype=np.int32)
    if bone_count > 0:
        invalid = (owners < -1) | (owners >= bone_count)
        if np.any(invalid):
            _warn(
                context,
                f"{np.count_nonzero(invalid)} vertices reference owners outside -1 or "
                f"0..{bone_count - 1}; values were preserved.",
            )
    elif np.any(owners < -1):
        _warn(context, f"{np.count_nonzero(owners < -1)} vertices have owner values below -1; values were preserved.")
    return vertices


def validate_car_vertices(vertices, vertex_count, context, compatibility=True):
    """Validate signed CAR owners without changing their source values."""
    _validate_vertex_fields(vertices, vertex_count, context, compatibility)

    owners = np.asarray(vertices['owner'], dtype=np.int32)
    invalid_negative = owners < -1
    if np.any(invalid_negative):
        _warn(
            context,
            f"{np.count_nonzero(invalid_negative)} CAR vertices have owner values below -1; values were preserved.",
        )

    owned_ids = np.unique(owners[owners >= 0])
    if owned_ids.size == 0:
        _warn(context, "CAR model has no non-negative owner IDs; all vertices are unowned.")
        return vertices

    unowned_count = int(np.count_nonzero(owners < 0))
    if unowned_count and compatibility:
        _warn(context, f"{unowned_count} CAR vertices use negative owner IDs and will remain unowned during reconstruction.")

    expected = np.arange(int(owned_ids[0]), int(owned_ids[-1]) + 1, dtype=owned_ids.dtype)
    if not np.array_equal(owned_ids, expected):
        _warn(context, "CAR owner IDs are sparse/noncontiguous; raw IDs will be preserved and compacted internally.")
    return vertices


def validate_3df_faces(faces, face_count, vertex_count, texture_height, context, compatibility=True):
    if faces.shape[0] != face_count:
        raise ValueError(f"Expected {face_count} faces, but parsed {faces.shape[0]}.")

    indices = faces['v']
    invalid_indices = (indices < 0) | (indices >= vertex_count)
    if np.any(invalid_indices):
        raise ValueError(
            f"{np.count_nonzero(invalid_indices)} face-vertex indices are outside 0..{vertex_count - 1}; "
            "source data was not clamped."
        )

    v1, v2, v3 = indices[:, 0], indices[:, 1], indices[:, 2]
    degenerate = (v1 == v2) | (v2 == v3) | (v1 == v3)
    if np.any(degenerate):
        _warn(context, f"{np.count_nonzero(degenerate)} degenerate faces contain duplicate vertex indices.")

    if compatibility:
        u_raw, v_raw = faces['u_tex'], faces['v_tex']
        uv_outside = (u_raw < 0) | (u_raw > 255)
        if np.any(uv_outside):
            _warn(context, f"{np.count_nonzero(uv_outside)} U coordinates are outside the legacy 0..255 range; values were preserved.")
        if texture_height > 0:
            v_outside = (v_raw < 0) | (v_raw > texture_height)
            if np.any(v_outside):
                _warn(
                    context,
                    f"{np.count_nonzero(v_outside)} V coordinates are outside the legacy 0..{texture_height} edge range; values were preserved.",
                )

        known_mask = 0
        for value, _name, _description in FACE_FLAG_OPTIONS:
            known_mask |= int(value)
        unknown = np.asarray(faces['flags'], dtype=np.uint16) & np.uint16(~known_mask & 0xFFFF)
        if np.any(unknown):
            first_unknown = int(unknown[unknown != 0][0])
            _warn(
                context,
                f"{np.count_nonzero(unknown)} faces use unrecognized flag bits (first mask 0x{first_unknown:04X}); bits were preserved.",
            )
    return faces


def validate_3df_bones(bones, bone_count, context, compatibility=True):
    if bones.shape[0] != bone_count:
        raise ValueError(f"Parsed {bones.shape[0]} object/bone records; expected {bone_count}.")

    parents = np.asarray(bones['parent'], dtype=np.int32)
    invalid_parent = ~((parents == -1) | ((parents >= 0) & (parents < bone_count)))
    if np.any(invalid_parent):
        raise ValueError(
            f"{np.count_nonzero(invalid_parent)} bones have parent indices outside -1 or 0..{bone_count - 1}; "
            "source data was not repaired."
        )

    cycle_start = detect_bone_cycles(parents, bone_count)
    if cycle_start != -1:
        raise ValueError(f"Bone hierarchy contains a cycle involving bone {cycle_start}; source data was not repaired.")

    if not np.isfinite(bones['pos']).all():
        raise ValueError("Bone positions contain NaN or infinite values.")

    if compatibility:
        decoded = []
        for index, raw_name in enumerate(bones['name']):
            name = decode_serialized_name(raw_name)
            if not name:
                _warn(context, f"Bone #{index} has an empty name; the importer will use a placeholder.")
                name = f"Bone_{index}"
            decoded.append(name)
        duplicates = sorted({name for name in decoded if decoded.count(name) > 1})
        if duplicates:
            _warn(context, f"Duplicate bone names {duplicates}; Blender will require unique names.")
    return bones


def validate_3df_texture(texture_raw, texture_size, context, compatibility=True):
    expected_length = int(texture_size) // 2
    if texture_raw.size != expected_length:
        raise ValueError(
            f"Texture contains {texture_raw.size} pixels; expected {expected_length} from the header."
        )
    if compatibility and texture_raw.size and not np.any(texture_raw):
        _warn(context, "Texture data is completely zero (transparent black).")
    return texture_raw
