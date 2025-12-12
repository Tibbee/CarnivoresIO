import bpy
import os
from . import operators
from .core.constants import FACE_FLAG_OPTIONS

bl_info = {
    "name": "CarnivoresIO",
    "author": "Tibor Harsányi (Strider)",
    "version": (1,0,0),
    "blender": (4,0,0),
    "location": "File > Import/Export, View3D > Sidebar > Carnivores 3DF",
    "description": "Import and export custom Carnivores file formats",
    "category": "Import-Export",
    "support" : "COMMUNITY",
}

def menu_func_import(self, context):
    self.layout.operator(operators.CARNIVORES_OT_import_3df.bl_idname, text="Carnivores 3DF (.3df)")
    self.layout.operator(operators.CARNIVORES_OT_import_car.bl_idname, text='Carnivores CAR (.car)')

def menu_func_export(self, context):
    self.layout.operator(operators.CARNIVORES_OT_export_3df.bl_idname, text="Carnivores 3DF (.3df)")
    self.layout.operator(operators.CARNIVORES_OT_export_car.bl_idname, text="Carnivores CAR (.car)")
   
classes = [
    # Operators: Import/Export
    operators.CARNIVORES_OT_import_3df,
    operators.CARNIVORES_OT_import_car,
    operators.CARNIVORES_OT_export_3df,
    operators.CARNIVORES_OT_export_car,
    # Operators: Mesh Attribute Management
    operators.CARNIVORES_OT_create_3df_flags,
    operators.CARNIVORES_OT_modify_3df_flag,
    operators.CARNIVORES_OT_clear_flag_selections,
    # Operators: Selection
    operators.CARNIVORES_OT_select_by_flags,
    # Operators: UI Utilities
    operators.CARNIVORES_OT_modal_message,
    operators.CARNIVORES_OT_toggle_nla_sound_playback,
    operators.CARNIVORES_OT_import_sound_for_action,
    # Operators: Animation Management
    operators.CARNIVORES_OT_set_kps,
    operators.CARNIVORES_OT_reset_kps,
    operators.CARNIVORES_OT_resync_animation,
    operators.CARNIVORES_OT_play_track_preview,
    # UI Lists
    operators.CARNIVORES_UL_animation_list,
    # Panels
    operators.VIEW3D_PT_carnivores_selection,
    operators.VIEW3D_PT_3df_face_flags,
    operators.VIEW3D_PT_carnivores_animation,
]

def register():
    from . import operators # Import here to avoid circular dependencies during registration
    
    # Register Action properties
    bpy.types.Action.carnivores_sound_ptr = bpy.props.PointerProperty(
        type=bpy.types.Sound,
        name="Linked Sound",
        description="The sound data block linked to this action for Carnivores playback."
    )
    
    # Register Object property for UIList index
    bpy.types.Object.carnivores_active_nla_index = bpy.props.IntProperty(
        name="Active NLA Track Index",
        default=0
    )
    # New: Property for animation source selection
    bpy.types.Object.carnivores_anim_source = bpy.props.EnumProperty(
        name="Animation Source",
        items=[
            ('AUTO', "Auto-Detect", "Automatically choose Shape Keys if present, else Object animation"),
            ('SHAPE_KEYS', "Shape Keys", "Display Shape Key animations"),
            ('OBJECT', "Object", "Display Object/Armature animations"),
        ],
        default='AUTO',
        description="Select the type of animation data to display and manage"
    )
    # Register Action KPS mode property
    # This property is defined in operators.py, but needs to be added to bpy.types.Action here.


    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)

    # Add collapsible section property
    if not hasattr(bpy.types.Scene, "cf_flag_section"):
        bpy.types.Scene.cf_flag_section = bpy.props.BoolProperty(
            name="Flag Section",
            description="Show or hide flag selection options",
            default=True
        )

    # Bool props for each known flag (with tooltips)
    for (i, (bit, label, tooltip)) in enumerate(FACE_FLAG_OPTIONS):
        prop_name = f"cf_flag_{i}"
        if not hasattr(bpy.types.Scene, prop_name):
            setattr(bpy.types.Scene, prop_name, bpy.props.BoolProperty(name=label, description=tooltip, default=False))

    # Mode and action controls with clearer labels
    if not hasattr(bpy.types.Scene, "cf_select_mode"):
        bpy.types.Scene.cf_select_mode = bpy.props.EnumProperty(
            name="Mode",
            items=[
                ('ANY', "Has Any (OR)", "Match faces that have at least one of the selected flags"),
                ('ALL', "Has All (AND)", "Match faces that have every selected flag"),
                ('NONE', "Has None (NOT)", "Match faces that have none of the selected flags"),
            ],
            default='ANY'
        )

    if not hasattr(bpy.types.Scene, "cf_select_action"):
        bpy.types.Scene.cf_select_action = bpy.props.EnumProperty(
            name="Action",
            items=[
                ('SELECT', "Select", "Select matched faces"),
                ('DESELECT', "Deselect", "Deselect matched faces"),
                ('INVERT', "Invert", "Invert selection for matched faces"),
            ],
            default='SELECT'
        )
    
    if not hasattr(bpy.types.Scene, "carnivores_nla_sound_enabled"):
        bpy.types.Scene.carnivores_nla_sound_enabled = bpy.props.BoolProperty(
            name="Enable NLA Sound Playback",
            description="Enable automatic sound playback based on active NLA strips",
            default=False
        )
    
    # Ensure the handler is always registered while the addon is enabled.
    if operators.carnivores_nla_sound_handler not in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.append(operators.carnivores_nla_sound_handler)
    if operators.playback_started_handler not in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.append(operators.playback_started_handler)
    if operators.playback_stopped_handler not in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.append(operators.playback_stopped_handler)
    
    if operators.clear_aud_device_on_new_file not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(operators.clear_aud_device_on_new_file)
    
def unregister():
    print("DEBUG: CarnivoresIO unregister() called.")
    for attr in [f"cf_flag_{i}" for i, _ in enumerate(FACE_FLAG_OPTIONS)] + ['cf_flag_section', 'cf_select_mode', 'cf_select_action', 'carnivores_nla_sound_enabled']:
        if hasattr(bpy.types.Scene, attr):
            try:
                delattr(bpy.types.Scene, attr)
            except Exception as e:
                print(f"[CarnivoresIO] Failed to remove scene property {attr}: {e}")
    
    # Remove Action property
    if hasattr(bpy.types.Action, "carnivores_sound_ptr"):
        del bpy.types.Action.carnivores_sound_ptr
        
    # Remove Object property
    if hasattr(bpy.types.Object, "carnivores_active_nla_index"):
        del bpy.types.Object.carnivores_active_nla_index
    # New: Remove animation source property
    if hasattr(bpy.types.Object, "carnivores_anim_source"):
        del bpy.types.Object.carnivores_anim_source
    # Remove Action KPS mode property
    if hasattr(bpy.types.Action, "carnivores_kps_mode"):
        del bpy.types.Action.carnivores_kps_mode

    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    # Unregister the handler if it was registered
    if operators.carnivores_nla_sound_handler in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(operators.carnivores_nla_sound_handler)
    if operators.playback_started_handler in bpy.app.handlers.animation_playback_pre:
        bpy.app.handlers.animation_playback_pre.remove(operators.playback_started_handler)
    if operators.playback_stopped_handler in bpy.app.handlers.animation_playback_post:
        bpy.app.handlers.animation_playback_post.remove(operators.playback_stopped_handler)
    
    if operators.clear_aud_device_on_new_file in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(operators.clear_aud_device_on_new_file)
    
    # Cleanup temporary sound files
    print("DEBUG: Cleaning up temporary sound files...")
    for filepath in operators._temp_sound_files:
        if os.path.exists(filepath):
            try:
                os.remove(filepath)
                print(f"DEBUG: Removed temp sound file: {filepath}")
            except Exception as e:
                print(f"WARNING: Failed to remove temp sound file {filepath}: {e}")
    operators._temp_sound_files.clear()
    
    for cls in reversed(classes):
        try:
            bpy.utils.unregister_class(cls)
        except Exception as e:
            print(f"[CarnivoresIO] Failed to unregister class {cls.__name__}: {e}")
    
    
if __name__ == "__main__":
    register()

