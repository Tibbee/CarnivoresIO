import bpy
op = bpy.context.active_operator

op.scale = 0.01
op.import_textures = True
op.create_materials = True
op.normal_smooth = True
op.validate = False
op.flip_handedness = True
op.import_animations = True
op.use_absolute_shape_keys = True
op.use_kps_timing = False
op.import_sounds = True
op.smooth_weights = False
op.smooth_iterations = 3
op.smooth_factor = 0.5
op.smooth_joints_only = True
