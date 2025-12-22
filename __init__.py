bl_info = {
    "name": "CarnivoresIO",
    "author": "StriderTibe",
    "version": (2, 0, 0),
    "blender": (4, 0, 0),
    "location": "File > Import-Export",
    "description": "Import/Export Carnivores .3DF and .CAR models with animations",
    "category": "Import-Export",
}

import bpy
import os
from .operators import classes as operator_classes
from .operators import animation as anim_ops
from .utils import animation as anim_utils
from .operators.animation import set_kps_mode, get_kps_mode
from .utils.logger import info

class CarnivoresPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    debug_mode: bpy.props.BoolProperty(
        name="Debug Mode",
        description="Enable verbose logging in the console",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "debug_mode")

def register():
    # Register Classes
    bpy.utils.register_class(CarnivoresPreferences)
    for cls in operator_classes:
        bpy.utils.register_class(cls)

    # Register Menus
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    
    # Register Properties
    bpy.types.Scene.cf_flag_section = bpy.props.BoolProperty(default=False)
    bpy.types.Scene.cf_select_mode = bpy.props.EnumProperty(
        items=[('ANY', "Has Any", ""), ('ALL', "Has All", ""), ('NONE', "Has None", "")],
        name="Mode", default='ANY'
    )
    bpy.types.Scene.cf_select_action = bpy.props.EnumProperty(
        items=[('SELECT', "Select", ""), ('DESELECT', "Deselect", ""), ('INVERT', "Invert", "")],
        name="Action", default='SELECT'
    )
    # Register flags
    from .core.constants import FACE_FLAG_OPTIONS
    for i, (bit, _, _) in enumerate(FACE_FLAG_OPTIONS):
        setattr(bpy.types.Scene, f"cf_flag_{i}", bpy.props.BoolProperty(default=False))

    bpy.types.Object.carnivores_anim_source = bpy.props.EnumProperty(
        name="Animation Source",
        description="Choose where to read NLA tracks from",
        items=[
            ('AUTO', "Auto (Detect)", "Try Shape Keys first, then Object Animation"),
            ('SHAPE_KEYS', "Shape Keys", "Use Shape Key Animation Data (Typical for .CAR)"),
            ('OBJECT', "Object/Armature", "Use Object Level Animation Data")
        ],
        default='AUTO'
    )
    
    bpy.types.Object.carnivores_active_nla_index = bpy.props.IntProperty(name="Active NLA Track Index", default=0)
    
    # Register KPS Mode property
    bpy.types.Action.carnivores_kps_mode = bpy.props.EnumProperty(
        name="KPS Mode",
        items=[
            ('AUTO', "Auto (Scene FPS)", "Use the scene's frames per second (FPS) for this animation"),
            ('OVERRIDE', "Override", "Use a custom Keys Per Second (KPS) value for this animation")
        ],
        description="Control how the animation's KPS is determined on export",
        get=get_kps_mode,
        set=set_kps_mode
    )
    
    bpy.types.Action.carnivores_sound_ptr = bpy.props.PointerProperty(
        type=bpy.types.Sound,
        name="Linked Sound",
        description="Sound effect associated with this animation"
    )
    
    bpy.types.Scene.carnivores_nla_sound_enabled = bpy.props.BoolProperty(
        name="Enable NLA Sound",
        description="Play linked sounds when scrubbing NLA strips",
        default=True
    )
    
    # Register Handlers
    if anim_ops.carnivores_nla_sound_handler not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(anim_ops.carnivores_nla_sound_handler)
        
    if anim_ops.playback_started_handler not in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.append(anim_ops.playback_started_handler)

    if anim_ops.playback_stopped_handler not in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.append(anim_ops.playback_stopped_handler)
        
    if anim_ops.clear_aud_device_on_new_file not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(anim_ops.clear_aud_device_on_new_file)
        
    info("CarnivoresIO: Registered")

def unregister():
    info("CarnivoresIO: Unregistering...")
    
    # Cleanup Audio
    if anim_ops._aud_device:
        try:
             anim_ops._aud_device.stopAll()
        except:
            pass
            
    # Remove Handlers
    if anim_ops.carnivores_nla_sound_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(anim_ops.carnivores_nla_sound_handler)
        
    if anim_ops.playback_started_handler in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.remove(anim_ops.playback_started_handler)

    if anim_ops.playback_stopped_handler in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.remove(anim_ops.playback_stopped_handler)
        
    if anim_ops.clear_aud_device_on_new_file in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(anim_ops.clear_aud_device_on_new_file)
        
    # Unlink temporary sounds
    for path in anim_utils._temp_sound_files:
        try:
            if os.path.exists(path):
                os.remove(path)
                info(f"Removed temp sound: {path}")
        except Exception as e:
            info(f"Failed to remove temp sound {path}: {e}")
    anim_utils._temp_sound_files.clear()

    # Unregister Menus
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

    # Unregister Classes
    for cls in reversed(operator_classes):
        bpy.utils.unregister_class(cls)
    bpy.utils.unregister_class(CarnivoresPreferences)
        
    # Unregister Properties
    del bpy.types.Scene.cf_flag_section
    del bpy.types.Scene.cf_select_mode
    del bpy.types.Scene.cf_select_action
    
    from .core.constants import FACE_FLAG_OPTIONS
    for i in range(len(FACE_FLAG_OPTIONS)):
        delattr(bpy.types.Scene, f"cf_flag_{i}")
        
    del bpy.types.Object.carnivores_anim_source
    del bpy.types.Object.carnivores_active_nla_index
    del bpy.types.Action.carnivores_kps_mode
    del bpy.types.Action.carnivores_sound_ptr
    del bpy.types.Scene.carnivores_nla_sound_enabled
    
    info("CarnivoresIO: Unregistered")

def menu_func_import(self, context):
    self.layout.operator("carnivores.import_3df", text="Carnivores .3DF (.3df)")
    self.layout.operator("carnivores.import_car", text="Carnivores .CAR (.car)")

def menu_func_export(self, context):
    self.layout.operator("carnivores.export_3df", text="Carnivores .3DF (.3df)")
    self.layout.operator("carnivores.export_car", text="Carnivores .CAR (.car)")
    self.layout.operator("carnivores.export_3dn", text="Carnivores .3DN (.3dn)")

if __name__ == "__main__":
    register()