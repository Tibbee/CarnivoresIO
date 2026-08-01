"""Operators for opening and copying structured Carnivores reports."""

import bpy

from ..utils.reporting import get_report_text


class CARNIVORES_OT_open_report(bpy.types.Operator):
    """Open a generated report without replacing a 3D viewport."""

    bl_idname = "carnivores.open_report"
    bl_label = "Open Report"
    bl_description = "Open the complete Carnivores operation report without replacing the current viewport"

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
        if area is not None and area.type == 'TEXT_EDITOR':
            area.spaces.active.text = text
            self.report({'INFO'}, f"Opened report '{text.name}'.")
            return {'FINISHED'}

        # Prefer a Text Editor that is already visible in the current screen.
        screen = getattr(context, "screen", None)
        if screen is not None:
            text_area = next(
                (candidate for candidate in screen.areas if candidate.type == 'TEXT_EDITOR'),
                None,
            )
            if text_area is not None:
                text_area.spaces.active.text = text
                self.report({'INFO'}, f"Opened report '{text.name}'.")
                return {'FINISHED'}

        # Never replace a 3D View (especially a rendered one). Create a separate
        # window containing the report when no Text Editor is available.
        existing_windows = tuple(context.window_manager.windows)
        try:
            result = bpy.ops.wm.window_new()
            if 'FINISHED' not in result:
                raise RuntimeError("Blender could not create a report window")

            new_window = next(
                (
                    window for window in context.window_manager.windows
                    if window not in existing_windows
                ),
                None,
            )
            if new_window is None or not new_window.screen.areas:
                raise RuntimeError("The new report window has no editor area")

            report_area = new_window.screen.areas[0]
            report_area.type = 'TEXT_EDITOR'
            report_area.spaces.active.text = text
        except Exception as exc:
            self.report({'ERROR'}, f"Could not open report in a separate window: {exc}")
            return {'CANCELLED'}

        self.report({'INFO'}, f"Opened report '{text.name}' in a separate window.")
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
