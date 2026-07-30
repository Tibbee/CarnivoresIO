"""Model Health and non-destructive export preflight operators."""

import bpy

from ..utils.logger import warn
from ..utils.reporting import write_report_text
from ..utils.validation import FORMAT_ITEMS, model_health_summary, validate_blender_model


def _poll_message(cls, message):
    try:
        cls.poll_message_set(message)
    except (AttributeError, TypeError, RuntimeError):
        pass
    return False


def _finalize_validation_report(report):
    try:
        text_name = write_report_text(report)
    except Exception as exc:
        text_name = ""
        report.text_name = ""
        # The validation result still remains available through the operator
        # message when a Text datablock cannot be created.
        warn(f"Could not write validation report: {exc}")

    try:
        bpy.ops.carnivores.modal_message(
            'INVOKE_DEFAULT',
            message=report.popup_summary(),
            report_text_name=text_name,
        )
    except Exception as exc:
        # Background and restricted contexts may not support a popup.  The
        # operator's report and Text datablock remain the fallback.
        warn(f"Could not display validation report: {exc}")


class CARNIVORES_OT_validate_model(bpy.types.Operator):
    """Run non-destructive model health and export compatibility checks."""

    bl_idname = "carnivores.validate_model"
    bl_label = "Validate Model"
    bl_description = "Run non-destructive mesh, texture, face-flag, animation, rig, and target-format preflight checks."
    bl_options = {'REGISTER'}

    target_format: bpy.props.EnumProperty(
        name="Target Format",
        description="Choose which export format's requirements and compatibility checks to apply.",
        items=FORMAT_ITEMS,
        default='AUTO',
    )
    export_textures: bpy.props.BoolProperty(
        name="Export Textures",
        description="Include texture and active-UV requirements for formats that export texture references.",
        default=True,
    )
    check_audio: bpy.props.BoolProperty(
        name="Check Audio Conversion",
        description="Verify linked CAR sounds can be converted to 22050Hz mono PCM16.",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = getattr(context, "active_object", None)
        if not obj or obj.type != 'MESH':
            return _poll_message(cls, "Select a mesh object before validating the model.")
        return True

    def execute(self, context):
        obj = context.active_object
        report = validate_blender_model(
            obj,
            self.target_format,
            export_textures=self.export_textures,
            check_audio=self.check_audio,
        )
        if report.status == "FAILED":
            self.report({'ERROR'}, f"{report.operation}: validation failed.")
        elif report.has_attention:
            self.report({'WARNING'}, f"{report.operation}: completed with warnings.")
        else:
            self.report({'INFO'}, f"{report.operation}: passed.")

        _finalize_validation_report(report)
        return {'CANCELLED'} if report.status == "FAILED" else {'FINISHED'}


class VIEW3D_PT_carnivores_model_health(bpy.types.Panel):
    """Compact model facts and the explicit preflight entry point."""

    bl_label = "Carnivores Model"
    bl_idname = "VIEW3D_PT_carnivores_model_health"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Carnivores'

    def draw(self, context):
        layout = self.layout
        obj = getattr(context, "active_object", None)
        if not obj or obj.type != 'MESH':
            layout.label(text="Select a mesh object to inspect Carnivores data.", icon='INFO')
            return

        scene = context.scene
        summary = model_health_summary(obj, scene.carnivores_validation_format)
        overview = layout.box()
        overview.label(text=obj.name, icon='MESH_DATA')
        row = overview.row(align=True)
        row.label(text=f"Vertices: {summary['vertices']}")
        row.label(text=f"Faces: {summary['faces']}")
        row = overview.row(align=True)
        row.label(text=f"UV map: {summary['texture']}")
        row.label(text=f"Flags: {summary['flags']}")
        row = overview.row(align=True)
        row.label(text=f"Animation: {summary['animation']}")
        status_icon = {
            "SUCCESS": 'CHECKMARK',
            "COMPLETED WITH WARNINGS": 'ERROR',
            "FAILED": 'CANCEL',
        }.get(summary['status'], 'INFO')
        row.label(text=f"Health: {summary['status']}", icon=status_icon)

        target = layout.box()
        target.label(text="Preflight Target", icon='VIEWZOOM')
        target.prop(scene, "carnivores_validation_format")
        target.prop(scene, "carnivores_validation_export_textures")
        target.prop(scene, "carnivores_validation_check_audio")
        operator = target.operator("carnivores.validate_model", text="Validate Model", icon='CHECKMARK')
        operator.target_format = scene.carnivores_validation_format
        operator.export_textures = scene.carnivores_validation_export_textures
        operator.check_audio = scene.carnivores_validation_check_audio

        if summary['flags'] == "Missing":
            target.operator("carnivores.create_3df_flags", text="Create 3df_flags", icon='ADD')
