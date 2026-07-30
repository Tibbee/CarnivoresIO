FACE_FLAG_OPTIONS = [
    (1, 'Double Side', 'Render both sides of the face; C2 does not apply its normal back-face culling/light marker.'),
    (2, 'Dark Back', 'Use the C2 back-face culling test; a back-facing triangle can be omitted.'),
    (4, 'Opacity', 'Alpha-tested cutout: transparent texture pixels are discarded in the cutout render pass.'),
    (8, 'Transparent', 'Alpha-blended, non-solid face; rendered in the transparent pass and skipped by projectile trace tests.'),
    (16, 'Mortal', 'Mark a character hit on this face as mortal; eligible weapons can apply immediate lethal damage.'),
    (32, 'Phong', 'Select the face for C2\'s special Phong mapping pass when that pass is used; its extended mask is 0x0030.'),
    (64, 'Env Map', 'Select the face for C2\'s special environment-map pass when that pass is used; its extended mask is 0x0050.'),
    (128, 'Need VC', 'Legacy vertex-color/light and culling marker; C2 adds it automatically to non-double-sided faces.'),
    (32768, 'Dark', 'Legacy software-renderer darkening flag; it may have no effect in the current OpenGL path.')
]
TEXTURE_WIDTH = 256
