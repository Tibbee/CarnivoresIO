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