import numpy as np

HEADER_DTYPE = np.dtype([
    ('vertex_count', '<u4'),
    ('face_count',   '<u4'),
    ('bone_count',   '<u4'),
    ('texture_size', '<u4'),
])

FACE_DTYPE = np.dtype([
    ('v',       '<u4', (3,)),
    ('u_tex',   '<u4', (3,)),
    ('v_tex',   '<u4', (3,)),
    ('flags',   '<u2'),
    ('dmask',   '<u2'),
    ('distant', '<u4'),
    ('next',    '<u4'),
    ('group',   '<u4'),
    ('reserv',  'u1', (12,)),
])

VERTEX_DTYPE = np.dtype([
    ('coord', '<f4', (3,)),
    ('owner', '<u2'),
    ('hide',  '<u2'),
])

BONE_DTYPE = np.dtype([
    ("name", "S32"),
    ("pos", "<f4", (3,)),
    ("parent", "<i2"),
    ("hidden", "<u2"),
])

CAR_HEADER_DTYPE = np.dtype([
    ('model_name', 'S32'),  # ASCII string (often ends with "msc: #")
    ('ani_count', '<u4'), ('sfx_count', '<u4'), ('vertex_count', '<u4'),
    ('face_count', '<u4'), ('texture_size', '<u4')
])

THREEDN_HEADER_DTYPE = np.dtype([
    ('vertex_count', '<u4'),
    ('face_count',   '<u4'),
    ('bone_count',   '<u4'),
    ('model_name',   'S32'),
    ('has_sprite',   '<u4'),
])

THREEDN_VERTEX_DTYPE = np.dtype([
    ('coord', '<f4', (3,)),
    ('owner', '<i4'),
])

THREEDN_FACE_DTYPE = np.dtype([
    ('v1',      '<u4'),
    ('v2',      '<u4'),
    ('v3',      '<u4'),
    ('tax',     '<i2'),
    ('tay',     '<i2'),
    ('tbx',     '<i2'),
    ('tby',     '<i2'),
    ('tcx',     '<i2'),
    ('tcy',     '<i2'),
    ('flags',   '<u2'),
    ('dmask',   '<u2'),
    ('distant', '<u4'),
    ('next',    '<u4'),
    ('group',   '<u4'),
    ('reserv',  '<u4', (3,)),
])
