import bpy
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

def menu_func_export(self, context):
    self.layout.operator(operators.CARNIVORES_OT_export_3df.bl_idname, text="Carnivores 3DF (.3df)")

    
classes = [
    operators.CARNIVORES_OT_import_3df,
    operators.CARNIVORES_OT_export_3df,
    operators.CARNIVORES_OT_create_3df_flags,
    operators.CARNIVORES_OT_modal_message,
    operators.CARNIVORES_OT_set_3df_flag,
    operators.CARNIVORES_OT_clear_3df_flag,
    operators.CARNIVORES_OT_toggle_3df_flag,
    operators.CARNIVORES_OT_select_by_flags,
    operators.VIEW3D_PT_carnivores_selection,
    operators.CARNIVORES_OT_clear_all_3df_flags,
    operators.CARNIVORES_OT_clear_flag_selections,
    operators.VIEW3D_PT_3df_face_flags,
]

def register():
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
    for i, (bit, label) in enumerate(FACE_FLAG_OPTIONS):
        prop_name = f"cf_flag_{i}"
        tooltip = {
            0x0001: "Marks face as textured on both sides",
            0x0002: "Marks face as having a dark back side",
            0x0004: "Marks face as transparent",
            0x0008: "Marks face as non-solid (bullets pass through harmlessly)",
            0x0010: "Marks face as a target zone",
            0x0020: "Marks face as Phong Mapped",
            0x0040: "Marks face as EnvironMent mapped",
            0x0080: "Unused",
            0x8000: "Marks face as having a dark front side",
        }.get(int(bit), f"Match faces with flag: {label}")
        if not hasattr(bpy.types.Scene, prop_name):
            setattr(
                bpy.types.Scene,
                prop_name,
                bpy.props.BoolProperty(
                    name=label,
                    description=tooltip,
                    default=False
                )
            )

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
    
    
def unregister():
    for i, (bit, label) in enumerate(FACE_FLAG_OPTIONS):
        prop_name = f"cf_flag_{i}"
        if hasattr(bpy.types.Scene, prop_name):
            try:
                delattr(bpy.types.Scene, prop_name)
            except Exception:
                pass

    if hasattr(bpy.types.Scene, "cf_flag_section"):
        try:
            delattr(bpy.types.Scene, "cf_flag_section")
        except Exception:
            pass

    if hasattr(bpy.types.Scene, "cf_select_mode"):
        try:
            delattr(bpy.types.Scene, "cf_select_mode")
        except Exception:
            pass

    if hasattr(bpy.types.Scene, "cf_select_action"):
        try:
            delattr(bpy.types.Scene, "cf_select_action")
        except Exception:
            pass

    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    
    
if __name__ == "__main__":
    register()
