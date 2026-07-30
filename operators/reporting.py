"""Operators for opening and copying structured Carnivores reports."""

import bpy

from ..utils.reporting import get_report_text


class CARNIVORES_OT_open_report(bpy.types.Operator):
    """Open a generated Carnivores report in the current editor area."""

    bl_idname = "carnivores.open_report"
    bl_label = "Open Report"
    bl_description = "Open the complete Carnivores operation report in a Text Editor"

    text_name: bpy.props.StringProperty(name="Report")

    @classmethod
    def poll(cls, context):
        # The report name is supplied by the popup operator at invocation time;
        # resolve the datablock in execute() so the button remains available in
        # Blender dialog contexts that do not expose an editor area.
        return True

    def execute(self, context):
        text = get_report_text(self.text_name)
        if text is None:
            self.report({'ERROR'}, f"Report text '{self.text_name}' was not found.")
            return {'CANCELLED'}

        area = getattr(context, "area", None)
        if area is None:
            self.report({'ERROR'}, "No editor area is available to open the report.")
            return {'CANCELLED'}

        area.type = 'TEXT_EDITOR'
        area.spaces.active.text = text
        self.report({'INFO'}, f"Opened report '{text.name}'.")
        return {'FINISHED'}


class CARNIVORES_OT_copy_report(bpy.types.Operator):
    """Copy a generated Carnivores report to the system clipboard."""

    bl_idname = "carnivores.copy_report"
    bl_label = "Copy Report"
    bl_description = "Copy the complete Carnivores operation report to the clipboard"

    text_name: bpy.props.StringProperty(name="Report")

    @classmethod
    def poll(cls, context):
        return True

    def execute(self, context):
        text = get_report_text(self.text_name)
        if text is None:
            self.report({'ERROR'}, f"Report text '{self.text_name}' was not found.")
            return {'CANCELLED'}

        context.window_manager.clipboard = text.as_string()
        self.report({'INFO'}, f"Copied report '{text.name}' to the clipboard.")
        return {'FINISHED'}
