"""Explicit preference maintenance operators."""

import bpy

from ..utils.addon import get_addon_preferences


class CARNIVORES_OT_restore_preferences(bpy.types.Operator):
    """Restore CarnivoresIO add-on preferences without changing scene data."""

    bl_idname = "carnivores.restore_preferences"
    bl_label = "Restore CarnivoresIO Defaults"
    bl_description = "Restore CarnivoresIO preference defaults; scene data and existing operator settings are not changed."
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return get_addon_preferences(__package__) is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        preferences = get_addon_preferences(__package__)
        if preferences is None:
            self.report({'ERROR'}, "CarnivoresIO preferences are unavailable in this context.")
            return {'CANCELLED'}

        defaults = {
            "debug_mode": False,
            "performance_mode": False,
            "auto_select_imported": True,
            "auto_frame_imported": False,
            "default_import_scale": 0.01,
            "default_export_scale": 100.0,
            "default_flip_handedness": True,
            "default_bone_import_type": 'HOOKS',
            "show_advanced_options": True,
            "documentation_url": "https://github.com/Tibbee/CarnivoresIO/blob/main/README.md",
            "issue_tracker_url": "https://github.com/Tibbee/CarnivoresIO/issues",
        }
        for name, value in defaults.items():
            setattr(preferences, name, value)
        self.report({'INFO'}, "CarnivoresIO preferences restored to defaults.")
        return {'FINISHED'}
