import bpy
import bpy_extras.io_utils
import os
import mathutils
import numpy as np
from ..utils import io as io_utils
from ..utils import animation as anim_utils
from ..utils import common
from ..utils.addon import get_addon_preferences
from ..utils.logger import info, debug, warn, error
from ..utils.reporting import OperationReport, write_report_text
from ..utils.performance import record_operator_options
from ..utils.validation import validate_blender_model
from ..utils.rig_reconstruction import (
    OWNER_MAPPING_PROPERTY,
    build_owner_mapping,
    owner_mapping_to_metadata,
)
from ..parsers.parse_3df import parse_3df
from ..parsers.parse_car import parse_car
from ..parsers.export_3df import export_3df
from ..parsers.export_car import export_car
from ..parsers.export_3dn import export_3dn
from ..parsers.export_vtl import export_vtl


def _poll_message(cls, message):
    """Set a Blender operator poll explanation when the API supports it."""
    try:
        cls.poll_message_set(message)
    except (AttributeError, TypeError, RuntimeError):
        # Blender versions without poll_message_set can still use the boolean
        # result of poll().
        pass
    return False


def _active_mesh(context):
    obj = getattr(context, "active_object", None)
    return obj if obj and obj.type == 'MESH' else None


def _get_addon_preferences():
    # Blender's background extension-build process can tear down the Addon
    # preferences registry while operators are being inspected.  Scripted and
    # background operations should keep their explicit operator defaults.
    if getattr(bpy.app, 'background', False):
        return None
    return get_addon_preferences(__package__)


def _apply_import_focus_preferences(operator):
    """Initialize per-invocation focus and import defaults from preferences."""
    preferences = _get_addon_preferences()
    if preferences is not None:
        operator.select_imported = bool(preferences.auto_select_imported)
        operator.frame_imported = bool(preferences.auto_frame_imported)
    _apply_operation_preferences(operator, 'IMPORT')


def _apply_operation_preferences(operator, direction):
    """Apply opt-in dialog defaults while preserving scripted operator defaults."""
    preferences = _get_addon_preferences()
    if preferences is None:
        return
    if hasattr(operator, 'scale'):
        operator.scale = float(
            preferences.default_import_scale if direction == 'IMPORT' else preferences.default_export_scale
        )
    if hasattr(operator, 'flip_handedness'):
        operator.flip_handedness = bool(preferences.default_flip_handedness)
    if direction == 'IMPORT' and hasattr(operator, 'bone_import_type'):
        operator.bone_import_type = preferences.default_bone_import_type


def _configure_operator_layout(layout):
    """Use Blender's standard compact property presentation in file dialogs."""
    layout.use_property_split = True
    layout.use_property_decorate = False
    return layout


def _draw_operator_title(layout, title, icon='INFO'):
    """Add a small orientation line without competing with the file browser title."""
    row = layout.row()
    row.label(text=title, icon=icon)
    layout.separator(factor=0.35)


def _operator_panel(
    layout,
    panel_id,
    title,
    *,
    default_closed=False,
    icon='NONE',
    header_prop=None,
):
    """Draw a native collapsible options panel with a narrow-sidebar fallback.

    ``UILayout.panel`` is intentionally used at the root of the operator
    options layout: Blender only supports native panels when they have the
    full width of their region.  The fallback keeps the add-on usable on
    Blender versions without that API.
    """
    panel_method = getattr(layout, "panel", None)
    if callable(panel_method):
        header, body = panel_method(panel_id, default_closed=default_closed)
        header.use_property_split = False
        header.use_property_decorate = False
        header.label(text=title, icon=icon)
        if header_prop is not None:
            data, property_name, property_text = header_prop
            header.prop(data, property_name, text=property_text)
        if body is not None:
            body.use_property_split = True
            body.use_property_decorate = False
        return body

    box = layout.box()
    box.label(text=title, icon=icon)
    if header_prop is not None:
        data, property_name, property_text = header_prop
        box.prop(data, property_name, text=property_text)
    return box


def _draw_advanced_coordinate_conversion(layout, operator):
    preferences = _get_addon_preferences()
    if preferences is not None and not preferences.show_advanced_options:
        layout.label(text="Coordinates hidden in Preferences.", icon='INFO')
        return

    panel_id = f"{operator.bl_idname.replace('.', '_')}_coordinates"
    body = _operator_panel(
        layout,
        panel_id,
        "Coordinates",
        default_closed=True,
        icon='WORLD',
        header_prop=(operator, "flip_handedness", "Carnivores"),
    )
    if body:
        body.label(text="Defaults target Carnivores files.", icon='INFO')
        body.prop(operator, "axis_forward", text="Forward")
        body.prop(operator, "axis_up", text="Up")


def _post_import_focus(context, objects, *, select_imported=True, frame_imported=False):
    """Select imported primary meshes and optionally frame them in this area.

    Framing is deliberately limited to the invoking VIEW_3D area.  A file
    import should never change an unrelated editor or fail because no viewport
    is available (for example in background mode).
    """
    live_objects = []
    for obj in objects:
        try:
            if obj and obj.name in bpy.data.objects and obj.type == 'MESH':
                live_objects.append(obj)
        except ReferenceError:
            continue
    if not live_objects:
        return {"selected": 0, "framed": False, "message": "No imported mesh remained available for post-import focus."}

    previous_selected = []
    previous_active = None
    temporary_selection = bool(frame_imported and not select_imported)
    if temporary_selection:
        previous_selected = list(getattr(context, "selected_objects", ()))
        previous_active = getattr(context.view_layer.objects, "active", None)

    if select_imported or temporary_selection:
        try:
            for selected in context.selected_objects:
                selected.select_set(False)
            for obj in live_objects:
                obj.select_set(True)
            context.view_layer.objects.active = live_objects[-1]
        except (ReferenceError, RuntimeError) as exc:
            if temporary_selection:
                try:
                    for obj in context.selected_objects:
                        obj.select_set(False)
                    for obj in previous_selected:
                        if obj and obj.name in bpy.data.objects:
                            obj.select_set(True)
                    context.view_layer.objects.active = previous_active
                except (ReferenceError, RuntimeError):
                    pass
            return {"selected": 0, "framed": False, "message": f"Could not select imported objects: {exc}"}

    framed = False
    frame_message = ""
    if frame_imported:
        area = getattr(context, "area", None)
        region = None
        if area and area.type == 'VIEW_3D':
            region = next((item for item in area.regions if item.type == 'WINDOW'), None)
        if region is None:
            frame_message = "Viewport framing was skipped because the invoking area is not a compatible 3D View."
        else:
            try:
                with context.temp_override(area=area, region=region):
                    result = bpy.ops.view3d.view_selected(use_all_regions=False)
                framed = 'FINISHED' in result
                if not framed:
                    frame_message = "Viewport framing was unavailable in the invoking 3D View."
            except (RuntimeError, AttributeError) as exc:
                frame_message = f"Viewport framing was skipped: {exc}"

    if temporary_selection:
        try:
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in previous_selected:
                if obj and obj.name in bpy.data.objects:
                    obj.select_set(True)
            context.view_layer.objects.active = previous_active if previous_active and previous_active.name in bpy.data.objects else None
        except (ReferenceError, RuntimeError) as exc:
            frame_message = f"Imported objects were framed, but the previous selection could not be restored: {exc}"

    message = frame_message or ""
    return {"selected": len(live_objects) if select_imported else 0, "framed": framed, "message": message}


def _report_import_summary(report, collections, objects, animations=0, sounds=0, parsed_animations=None, parsed_sounds=None):
    """Record stable, actionable content counts in an import report."""
    collections = list(dict.fromkeys(str(name) for name in collections))
    objects = list(dict.fromkeys(str(name) for name in objects))
    collection_text = ", ".join(collections) if collections else "none"
    object_text = ", ".join(objects) if objects else "none"
    report.info(
        "Import summary",
        f"Created {len(collections)} collection(s): {collection_text}.",
    )
    report.info(
        "Import summary",
        f"Created {len(objects)} primary mesh object(s): {object_text}.",
    )
    if parsed_animations is None:
        parsed_animations = animations
    if parsed_sounds is None:
        parsed_sounds = sounds
    report.info(
        "Import summary",
        f"Animation data: {animations} created ({parsed_animations} parsed).",
    )
    report.info(
        "Import summary",
        f"Sounds: {sounds} imported ({parsed_sounds} parsed).",
    )


def _draw_scale_note(layout, direction):
    standard = "0.01" if direction == "Import" else "100.0"
    layout.label(text=f"Standard: {standard}", icon='INFO')


def _remove_failed_import_collection(collection):
    """Remove a collection created for an import that did not complete."""
    if collection is None:
        return
    try:
        collection_name = collection.name
    except ReferenceError:
        collection_name = "<invalid collection>"

    try:
        live_collection = bpy.data.collections.get(collection_name)
        if live_collection is not None:
            bpy.data.collections.remove(live_collection, do_unlink=True)
    except Exception as cleanup_error:
        warn(f"Failed to remove partial import collection '{collection_name}': {cleanup_error}")


def _report_batch_summary(operator, operation, attempted, succeeded, failed, report=None):
    """Report a consistent result for single-file and batch operations."""
    success_count = len(succeeded)
    failure_count = len(failed)
    if report is not None:
        report.set_outcome(attempted, success_count, failure_count)

    if failure_count == 0:
        operator.report({'INFO'}, f"{operation}: {success_count}/{attempted} succeeded.")
        return True

    failed_names = ", ".join(failed[:3])
    if failure_count > 3:
        failed_names += f", +{failure_count - 3} more"
    message = (
        f"{operation}: {success_count}/{attempted} succeeded; "
        f"{failure_count} failed"
    )
    if failed_names:
        message += f" ({failed_names})"
    operator.report({'WARNING' if success_count else 'ERROR'}, message + ".")
    return success_count > 0


def _append_preflight_report(report, validation_report, source, destination=""):
    """Copy reusable validation diagnostics into an export operation report."""
    for entry in validation_report.entries:
        report.add(
            entry.severity,
            f"Preflight / {entry.category}",
            entry.message,
            source=source,
            destination=destination,
            suggested_action=entry.suggested_action,
        )
    return validation_report.counts()["ERROR"] == 0


def _finalize_operation_report(operator, report):
    """Persist a report and show one concise final summary popup."""
    try:
        text_name = write_report_text(report)
    except Exception as report_error:
        warn(f"Could not write {report.operation} report: {report_error}")
        report.text_name = ""
        text_name = ""

    try:
        bpy.ops.carnivores.modal_message(
            'INVOKE_DEFAULT',
            message=report.popup_summary(),
            report_text_name=text_name,
        )
    except Exception as popup_error:
        warn(f"Could not display {report.operation} report: {popup_error}")

    return text_name


@bpy_extras.io_utils.orientation_helper(axis_forward='Z', axis_up='Y')
class CARNIVORES_OT_import_3df(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "carnivores.import_3df"
    bl_label = "Import .3DF Model"
    bl_description = "Import a Carnivores .3df model file"
    bl_options = {'PRESET'}
    
    filename_ext = ".3df"
    filter_glob: bpy.props.StringProperty(
        default="*.3df",
        options={'HIDDEN'},
        maxlen=255,
    )
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype='DIR_PATH')
    scale: bpy.props.FloatProperty(
        name="Import Scale",
        description="Scale factor applied to imported coordinates. The standard Carnivores counterpart is Export Scale 100.0.",
        default=0.01,
        min=0.01,
        max=100.0,
    )

    import_textures: bpy.props.BoolProperty(
        name="Import Textures",
        description="Import textures",
        default=True
    )
    create_materials: bpy.props.BoolProperty(
        name="Create Materials",
        description="Create materials for the mesh and the world",
        default=True
    )
    normal_smooth: bpy.props.BoolProperty(
        name="Smooth Faces",
        description="Whether to smooth out faces or leave them flat (faceted) at import",
        default=True
    )
    select_imported: bpy.props.BoolProperty(
        name="Select Imported Objects",
        description="Select imported primary mesh objects and make the last one active after a successful import.",
        default=True,
    )
    frame_imported: bpy.props.BoolProperty(
        name="Frame Imported Objects",
        description="Frame imported objects in the invoking 3D View when the import is run from a compatible viewport.",
        default=False,
    )
    bone_import_type: bpy.props.EnumProperty(
        name="Bone Import Type",
        description="Choose whether to import no deformation objects, lightweight hooks, or one editable armature",
        items=[
            ('NONE', "None", "Import the mesh without bone deformation objects"),
            ('ARMATURE', "Armature", "Create one editable Blender armature with vertex groups"),
            ('HOOKS', "Hooks", "Create lightweight hook modifiers and control objects instead of one armature"),
        ],
        default='HOOKS'
    )
    validate: bpy.props.BoolProperty(
        name="Compatibility Checks",
        description="Report legacy AltEdit and current C2 MEE compatibility constraints; structural safety checks always run",
        default=True
    )
    flip_handedness: bpy.props.BoolProperty(
        name='Use Carnivores Coordinate Conversion',
        description='Apply the default Carnivores file-to-Blender coordinate conversion. Disable only for a deliberately custom coordinate workflow.',
        default=True
    )
    smooth_weights: bpy.props.BoolProperty(
        name="Smooth Weights",
        description="Procedurally smooth vertex weights for more organic deformation",
        default=False
    )
    smooth_iterations: bpy.props.IntProperty(
        name="Smoothing Iterations",
        description="Number of smoothing passes. Higher values mean softer joints",
        default=3,
        min=1,
        max=10
    )
    smooth_factor: bpy.props.FloatProperty(
        name="Smoothing Factor",
        description="Intensity of smoothing per pass (0.1 = subtle, 0.9 = aggressive)",
        default=0.5,
        min=0.01,
        max=1.0
    )
    smooth_joints_only: bpy.props.BoolProperty(
        name="Smooth Joints Only",
        description="Only smooth areas where different bone influences meet (preserves limb rigidity)",
        default=True
    )
    
    def invoke(self, context, event):
        _apply_import_focus_preferences(self)
        return bpy_extras.io_utils.ImportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = _configure_operator_layout(self.layout)
        _draw_operator_title(layout, ".3DF Import Options", icon='IMPORT')
        _draw_advanced_coordinate_conversion(layout, self)

        content = _operator_panel(
            layout,
            "carnivores_import_3df_content",
            "Content",
            icon='MATERIAL',
            header_prop=(self, "import_textures", "Textures"),
        )
        if content:
            content.enabled = self.import_textures
            content.prop(self, "create_materials", text="Materials")

        geometry = _operator_panel(
            layout,
            "carnivores_import_3df_geometry",
            "Geometry",
            icon='MESH_DATA',
        )
        if geometry:
            geometry.prop(self, "scale", text="Scale")
            _draw_scale_note(geometry, "Import")
            geometry.prop(self, "normal_smooth", text="Smooth Faces")

        rig = _operator_panel(
            layout,
            "carnivores_import_3df_rig",
            "Rig / Deformation",
            default_closed=True,
            icon='ARMATURE_DATA',
            header_prop=(self, "bone_import_type", "Rig"),
        )
        if rig:
            rig.label(text="Hooks are lightweight; Armature creates an editable skeleton.", icon='INFO')
            if self.bone_import_type != 'NONE':
                rig.prop(self, "smooth_weights", text="Smooth Weights")
                if self.smooth_weights:
                    rig.prop(self, "smooth_iterations", text="Iterations")
                    rig.prop(self, "smooth_factor", text="Strength")
                    rig.prop(self, "smooth_joints_only", text="Joints Only")

        compatibility = _operator_panel(
            layout,
            "carnivores_import_3df_compatibility",
            "Compatibility",
            default_closed=True,
            icon='CHECKMARK',
            header_prop=(self, "validate", "Checks"),
        )
        if compatibility:
            compatibility.label(text="Adds legacy and C2 diagnostics.", icon='INFO')

        post_import = _operator_panel(
            layout,
            "carnivores_import_3df_after_import",
            "After Import",
            default_closed=True,
            icon='IMPORT',
        )
        if post_import:
            post_import.prop(self, "select_imported", text="Select Objects")
            post_import.prop(self, "frame_imported", text="Frame Objects")
        
    @common.timed("CARNIVORES_OT_import_3df.execute", is_operator=True)
    def execute(self, context):
        
        record_operator_options(
            self,
            [
                "scale",
                "import_textures",
                "create_materials",
                "normal_smooth",
                "select_imported",
                "frame_imported",
                "bone_import_type",
                "validate",
                "flip_handedness",
                "smooth_weights",
                "smooth_iterations",
                "smooth_factor",
                "smooth_joints_only",
                "axis_forward",
                "axis_up",
            ],
        )
        handedness_matrix = mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) if self.flip_handedness else mathutils.Matrix.Identity(4)
        import_matrix = (
            mathutils.Matrix.Scale(self.scale, 4) 
            @ handedness_matrix 
            @ bpy_extras.io_utils.axis_conversion(
                from_forward=self.axis_forward,
                from_up=self.axis_up,
                to_forward='Y',
                to_up='Z'
            ).to_4x4()  
        )
        import_matrix_np = np.array(import_matrix)
        
        report = OperationReport(".3DF import", text_name="Carnivores_Import_Report")
        filepaths = [os.path.join(self.directory, f.name) for f in self.files]
        valid_paths = [fp for fp in filepaths if os.path.isfile(fp)]
        if not valid_paths:
            message = "No valid .3df files selected."
            report.error(
                "Input",
                message,
                suggested_action="Select one or more existing .3df files.",
            )
            report.set_outcome(0, 0, 0)
            self.report({'ERROR'}, message)
            _finalize_operation_report(self, report)
            return {'CANCELLED'}
        imported_files = []
        failed_files = []
        imported_objects = []
        created_collections = []

        for filepath in valid_paths:
            coll = None
            filename = os.path.basename(filepath)
            try:
                # Your existing parsing and importing logic here
                mesh_name, object_name = io_utils.generate_names(filepath)
                coll = io_utils.create_import_collection(object_name)
                header, faces, uvs, vertices, bones, bone_names, texture, texture_height, warnings = parse_3df(filepath, self.validate, self.import_textures, flip_handedness=self.flip_handedness)
                verticesTransformedPos = io_utils.apply_import_matrix(vertices['coord'], import_matrix_np)
                bonesTransformedPos = io_utils.apply_import_matrix(bones['pos'], import_matrix_np)

                obj = io_utils.create_mesh_object(
                    mesh_name,
                    verticesTransformedPos,
                    faces['v'],
                    object_name,
                    self.normal_smooth,
                    faces['flags']
                )

                coll.objects.link(obj)
                obj.carnivores_reconstruct_smooth_weights = self.smooth_weights
                obj.carnivores_reconstruct_smooth_iterations = self.smooth_iterations
                obj.carnivores_reconstruct_smooth_factor = self.smooth_factor
                obj.carnivores_reconstruct_smooth_joints_only = self.smooth_joints_only
                io_utils.create_uv_map(obj.data, uvs)
                if self.import_textures and texture is not None:
                    image = io_utils.create_image_texture(texture, texture_height, object_name)
                    if self.create_materials:
                        material = io_utils.create_texture_material(image, object_name)
                        obj.data.materials.append(material)

                if self.bone_import_type == 'HOOKS':
                    vertex_groups_by_index = io_utils.create_vertex_groups_from_bones(obj, bone_names, vertices['owner'])
                    if self.smooth_weights:
                        io_utils.smooth_vertex_weights(obj, iterations=self.smooth_iterations, factor=self.smooth_factor, joints_only=self.smooth_joints_only)
                    hook_objects = io_utils.create_hooks(bone_names, bonesTransformedPos, bones['parent'], object_name, obj, coll)
                    io_utils.assign_hook_modifiers(obj, hook_objects, vertex_groups_by_index)

                elif self.bone_import_type == 'ARMATURE':
                    io_utils.create_vertex_groups_from_bones(obj, bone_names, vertices['owner'])
                    if self.smooth_weights:
                        io_utils.smooth_vertex_weights(obj, iterations=self.smooth_iterations, factor=self.smooth_factor, joints_only=self.smooth_joints_only)
                    armature_obj = io_utils.create_armature(
                        bone_names, 
                        bonesTransformedPos, 
                        bones['parent'], 
                        object_name, 
                        coll,
                        verticesTransformedPos=verticesTransformedPos,
                        vertex_owners=vertices['owner']
                    )
                    io_utils.assign_armature_modifier(obj, armature_obj)

                if warnings:
                    for warning in warnings:
                        report.warning(
                            "Parser warning",
                            warning,
                            source=filename,
                            destination=coll.name if coll else "",
                            suggested_action="Review the complete report before export.",
                        )
                imported_files.append(filename)
                imported_objects.append(obj)
                if coll:
                    created_collections.append(coll.name)
                report.info(
                    "Import",
                    "Imported successfully.",
                    source=filename,
                    destination=coll.name if coll else "",
                )

            except Exception as exc:
                message = f"Failed to import {filename}: {exc}"
                error(f"[Import .3DF] {message}")
                report.error(
                    "Import",
                    str(exc),
                    source=filename,
                    suggested_action="Check the file and import options, then retry.",
                )
                failed_files.append(filename)
                _remove_failed_import_collection(coll)
                continue

        if self.create_materials and self.import_textures and imported_files:
            io_utils.setup_custom_world_shader()

        completed = _report_batch_summary(
            self,
            ".3DF import",
            len(valid_paths),
            imported_files,
            failed_files,
            report=report,
        )
        if imported_objects:
            _report_import_summary(report, created_collections, [obj.name for obj in imported_objects])
            focus = _post_import_focus(
                context,
                imported_objects,
                select_imported=self.select_imported,
                frame_imported=self.frame_imported,
            )
            if self.select_imported:
                report.info("Post-import", f"Selected {focus['selected']} imported primary mesh object(s); the last imported mesh is active.")
            if self.frame_imported:
                if focus["framed"]:
                    report.info("Post-import", "Framed imported objects in the invoking 3D View.")
                elif focus["message"]:
                    report.info("Post-import", focus["message"], suggested_action="Run the import from a 3D View if viewport framing is desired.")
        _finalize_operation_report(self, report)

        return {'FINISHED'} if completed else {'CANCELLED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='Z', axis_up='Y')
class CARNIVORES_OT_export_3df(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "carnivores.export_3df"
    bl_label = "Export .3DF Model(s)"
    bl_description = "Export selected mesh objects as Carnivores .3df model file(s)"
    bl_options = {'PRESET'}
    filename_ext = ".3df"
    filter_glob: bpy.props.StringProperty(default="*.3df", options={'HIDDEN'}, maxlen=255)
    scale: bpy.props.FloatProperty(
        name="Export Scale",
        description="Scale factor applied to exported coordinates. The standard Carnivores counterpart is Import Scale 0.01.",
        default=100.0,
        min=1.0,
        max=1000.0,
    )
    export_textures: bpy.props.BoolProperty(
        name="Export Textures",
        description="Export texture(s) if a suitable image is found",
        default=True,
    )
    flip_u: bpy.props.BoolProperty(
        name="Flip U on export",
        description="Flip U coordinate integers (useful if the map tool expects mirrored U)",
        default=False,
    )
    flip_v: bpy.props.BoolProperty(
        name="Flip V on export",
        description="Flip V coordinate integers",
        default=False,
    )
    use_multi_export: bpy.props.BoolProperty(
        name="Export Multiple Files",
        description="Export each selected mesh to a separate file using object names (filename as prefix); otherwise, export active object to the specified filename",
        default=False,
    )
    flip_handedness: bpy.props.BoolProperty(
        name='Use Carnivores Coordinate Conversion',
        description='Apply the default Carnivores Blender-to-file coordinate conversion. Disable only for a deliberately custom coordinate workflow.',
        default=True
    )
    preflight_validation: bpy.props.BoolProperty(
        name="Preflight Validation",
        description="Run non-destructive model checks before each .3DF export and block structurally unsafe files.",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        if _active_mesh(context):
            return True
        return _poll_message(cls, "Select an active mesh object to export as .3DF.")

    def invoke(self, context, event):
        _apply_operation_preferences(self, 'EXPORT')
        return bpy_extras.io_utils.ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = _configure_operator_layout(self.layout)
        _draw_operator_title(layout, ".3DF Export Options", icon='EXPORT')
        _draw_advanced_coordinate_conversion(layout, self)

        content = _operator_panel(
            layout,
            "carnivores_export_3df_content",
            "Content",
            icon='MATERIAL',
            header_prop=(self, "export_textures", "Textures"),
        )
        if content:
            content.enabled = self.export_textures
            row = content.row(align=True)
            row.prop(self, "flip_u", text="Flip U")
            row.prop(self, "flip_v", text="Flip V")

        geometry = _operator_panel(
            layout,
            "carnivores_export_3df_geometry",
            "Geometry",
            icon='MESH_DATA',
            header_prop=(self, "use_multi_export", "Multiple"),
        )
        if geometry:
            geometry.prop(self, "scale", text="Scale")
            _draw_scale_note(geometry, "Export")
            geometry.label(text="Active object or each selected mesh.", icon='INFO')

        compatibility = _operator_panel(
            layout,
            "carnivores_export_3df_compatibility",
            "Compatibility",
            default_closed=True,
            icon='CHECKMARK',
            header_prop=(self, "preflight_validation", "Checks"),
        )
        if compatibility:
            compatibility.label(text="Warnings allow export; errors block unsafe output.", icon='INFO')

    @common.timed("CARNIVORES_OT_export_3df.execute", is_operator=True)
    def execute(self, context):
        record_operator_options(
            self,
            [
                "scale",
                "export_textures",
                "flip_u",
                "flip_v",
                "use_multi_export",
                "flip_handedness",
                "preflight_validation",
                "axis_forward",
                "axis_up",
            ],
        )
        handedness_matrix = mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) if self.flip_handedness else mathutils.Matrix.Identity(4)
        export_matrix = (
            bpy_extras.io_utils.axis_conversion(
                from_forward='Y',
                from_up='Z',
                to_forward=self.axis_forward,
                to_up=self.axis_up
            ).to_4x4()
            @ handedness_matrix
            @ mathutils.Matrix.Scale(self.scale, 4) 
        )
        export_matrix_np = np.array(export_matrix)
        base_filepath = self.filepath
        base_dir = os.path.dirname(base_filepath)
        base_name = os.path.splitext(os.path.basename(base_filepath))[0]
        report = OperationReport(".3DF export", text_name="Carnivores_Export_Report")
        exported_files = []
        failed_files = []
        attempted_count = 0
        if self.use_multi_export:
            mesh_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
            if not mesh_objects:
                message = "No mesh objects selected for export."
                report.error(
                    "Input",
                    message,
                    suggested_action="Select at least one mesh object and retry.",
                )
                report.set_outcome(0, 0, 0)
                self.report({'ERROR'}, message)
                _finalize_operation_report(self, report)
                return {'CANCELLED'}
            attempted_count = len(mesh_objects)
            for obj in mesh_objects:
                obj_name = obj.name.replace('.', '_')  # Sanitize object name
                # If no base_name provided, use object name directly; otherwise, use as prefix
                filename = obj_name if not base_name else f"{base_name}_{obj_name}"
                filepath = os.path.join(base_dir, f"{filename}.3df")
                try:
                    if self.preflight_validation:
                        validation = validate_blender_model(
                            obj,
                            "3DF",
                            export_textures=self.export_textures,
                            filepath=filepath,
                            export_matrix=export_matrix_np,
                        )
                        if not _append_preflight_report(report, validation, obj.name, os.path.basename(filepath)):
                            failed_files.append(obj.name)
                            continue
                    export_3df(
                        filepath,
                        obj,
                        export_matrix_np,
                        export_textures=self.export_textures,
                        flip_u=self.flip_u,
                        flip_v=self.flip_v,
                        flip_handedness=self.flip_handedness
                    )
                    destination = os.path.basename(filepath)
                    exported_files.append(destination)
                    report.info(
                        "Export",
                        "Exported successfully.",
                        source=obj.name,
                        destination=destination,
                    )
                except Exception as exc:
                    message = f"Failed to export {obj.name} to {os.path.basename(filepath)}: {exc}"
                    error(f"[Export .3DF] {message}")
                    report.error(
                        "Export",
                        str(exc),
                        source=obj.name,
                        destination=os.path.basename(filepath),
                        suggested_action="Review the complete report and export settings.",
                    )
                    failed_files.append(obj.name)
        else:
            obj = context.active_object
            if not obj or obj.type != 'MESH':
                message = "No active mesh object selected for single-file export."
                report.error(
                    "Input",
                    message,
                    suggested_action="Select an active mesh object and retry.",
                )
                report.set_outcome(0, 0, 0)
                self.report({'ERROR'}, message)
                _finalize_operation_report(self, report)
                return {'CANCELLED'}
            attempted_count = 1
            filepath = base_filepath if base_name else os.path.join(base_dir, f"{obj.name.replace('.', '_')}.3df")
            try:
                preflight_ok = True
                if self.preflight_validation:
                    validation = validate_blender_model(
                        obj,
                        "3DF",
                        export_textures=self.export_textures,
                        filepath=filepath,
                        export_matrix=export_matrix_np,
                    )
                    preflight_ok = _append_preflight_report(
                        report,
                        validation,
                        obj.name,
                        os.path.basename(filepath),
                    )
                if preflight_ok:
                    export_3df(
                        filepath,
                        obj,
                        export_matrix_np,
                        export_textures=self.export_textures,
                        flip_u=self.flip_u,
                        flip_v=self.flip_v,
                        flip_handedness=self.flip_handedness
                    )
                    destination = os.path.basename(filepath)
                    exported_files.append(destination)
                    report.info(
                        "Export",
                        "Exported successfully.",
                        source=obj.name,
                        destination=destination,
                    )
                else:
                    failed_files.append(obj.name)
            except Exception as exc:
                message = f"Failed to export {obj.name} to {os.path.basename(filepath)}: {exc}"
                error(f"[Export .3DF] {message}")
                report.error(
                    "Export",
                    str(exc),
                    source=obj.name,
                    destination=os.path.basename(filepath),
                    suggested_action="Review the complete report and export settings.",
                )
                failed_files.append(obj.name)

        completed = _report_batch_summary(
            self,
            ".3DF export",
            attempted_count,
            exported_files,
            failed_files,
            report=report,
        )
        _finalize_operation_report(self, report)
        return {'FINISHED'} if completed else {'CANCELLED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='Z', axis_up='Y')
class CARNIVORES_OT_export_car(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "carnivores.export_car"
    bl_label = "Export .CAR Model"
    bl_description = "Export active mesh object as Carnivores .car model file"
    bl_options = {'PRESET'}
    
    filename_ext = ".car"
    filter_glob: bpy.props.StringProperty(default="*.car", options={'HIDDEN'}, maxlen=255)
    
    scale: bpy.props.FloatProperty(
        name="Export Scale",
        description="Scale factor applied to exported coordinates. The standard Carnivores counterpart is Import Scale 0.01.",
        default=100.0,
        min=1.0,
        max=1000.0,
    )
    
    model_name: bpy.props.StringProperty(
        name="Model Name Override",
        description="Internal model name (max 32 chars). Defaults to filename if empty. Tip: Suffix with 'msc: #' for special behavior.",
        default="",
        maxlen=32
    )
    
    export_textures: bpy.props.BoolProperty(
        name="Export Textures",
        description="Export texture(s) if a suitable image is found",
        default=True,
    )
    
    flip_u: bpy.props.BoolProperty(
        name="Flip U",
        description="Flip U coordinate integers",
        default=False,
    )
    
    flip_v: bpy.props.BoolProperty(
        name="Flip V",
        description="Flip V coordinate integers",
        default=False,
    )
    
    flip_handedness: bpy.props.BoolProperty(
        name='Use Carnivores Coordinate Conversion',
        description='Apply the default Carnivores Blender-to-file coordinate conversion. Disable only for a deliberately custom coordinate workflow.',
        default=True
    )
    preflight_validation: bpy.props.BoolProperty(
        name="Preflight Validation",
        description="Run non-destructive model, animation, sound, and C2 MEE checks before CAR export.",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        if _active_mesh(context):
            return True
        return _poll_message(cls, "Select an active mesh object to export as .CAR.")

    def invoke(self, context, event):
        _apply_operation_preferences(self, 'EXPORT')
        return bpy_extras.io_utils.ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = _configure_operator_layout(self.layout)
        _draw_operator_title(layout, ".CAR Export Options", icon='EXPORT')
        _draw_advanced_coordinate_conversion(layout, self)

        content = _operator_panel(
            layout,
            "carnivores_export_car_content",
            "Content",
            icon='MATERIAL',
            header_prop=(self, "export_textures", "Textures"),
        )
        if content:
            content.enabled = self.export_textures
            row = content.row(align=True)
            row.prop(self, "flip_u", text="Flip U")
            row.prop(self, "flip_v", text="Flip V")

        geometry = _operator_panel(
            layout,
            "carnivores_export_car_geometry",
            "Geometry",
            icon='MESH_DATA',
        )
        if geometry:
            geometry.prop(self, "scale", text="Scale")
            _draw_scale_note(geometry, "Export")

        compatibility = _operator_panel(
            layout,
            "carnivores_export_car_compatibility",
            "Compatibility",
            default_closed=True,
            icon='CHECKMARK',
            header_prop=(self, "preflight_validation", "Checks"),
        )
        if compatibility:
            compatibility.prop(self, "model_name", text="Model Name")
            compatibility.label(text="CAR names are limited to 32 characters.", icon='INFO')
            compatibility.label(text="Warnings allow export; errors block unsafe output.", icon='INFO')

    @common.timed("CARNIVORES_OT_export_car.execute", is_operator=True)
    def execute(self, context):
        record_operator_options(
            self,
            [
                "scale",
                "model_name",
                "export_textures",
                "flip_u",
                "flip_v",
                "flip_handedness",
                "preflight_validation",
                "axis_forward",
                "axis_up",
            ],
        )
        handedness_matrix = mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) if self.flip_handedness else mathutils.Matrix.Identity(4)
        export_matrix = (
            bpy_extras.io_utils.axis_conversion(
                from_forward='Y',
                from_up='Z',
                to_forward=self.axis_forward,
                to_up=self.axis_up
            ).to_4x4()
            @ handedness_matrix
            @ mathutils.Matrix.Scale(self.scale, 4) 
        )
        export_matrix_np = np.array(export_matrix)
        
        report = OperationReport(".CAR export", text_name="Carnivores_Export_Report")
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            message = "No active mesh object selected."
            report.error(
                "Input",
                message,
                suggested_action="Select an active mesh object and retry.",
            )
            report.set_outcome(0, 0, 0)
            self.report({'ERROR'}, message)
            _finalize_operation_report(self, report)
            return {'CANCELLED'}

        artifact_cache = {}
        try:
            if self.preflight_validation:
                validation = validate_blender_model(
                    obj,
                    "CAR",
                    export_textures=self.export_textures,
                    check_audio=True,
                    filepath=self.filepath,
                    model_name=self.model_name,
                    export_matrix=export_matrix_np,
                    artifact_cache=artifact_cache,
                )
                if not _append_preflight_report(report, validation, obj.name, os.path.basename(self.filepath)):
                    report.set_outcome(1, 0, 1)
                    self.report({'ERROR'}, "CAR preflight failed; no file was written.")
                    _finalize_operation_report(self, report)
                    return {'CANCELLED'}
            export_car(
                self.filepath,
                obj,
                export_matrix_np,
                export_textures=self.export_textures,
                flip_u=self.flip_u,
                flip_v=self.flip_v,
                flip_handedness=self.flip_handedness,
                model_name_override=self.model_name,
                sound_conversion_cache=artifact_cache.setdefault("sound_conversion", {}),
            )
            destination = os.path.basename(self.filepath)
            report.set_outcome(1, 1, 0)
            report.info(
                "Export",
                "Exported successfully.",
                source=obj.name,
                destination=destination,
            )
            self.report({'INFO'}, f"Exported {destination}")
            _finalize_operation_report(self, report)
            return {'FINISHED'}
        except Exception as exc:
            message = f"Export failed: {exc}"
            report.set_outcome(1, 0, 1)
            report.error(
                "Export",
                str(exc),
                source=obj.name,
                destination=os.path.basename(self.filepath),
                suggested_action="Review the complete report and export settings.",
            )
            self.report({'ERROR'}, message)
            error(f"[Export .CAR] {message}")
            _finalize_operation_report(self, report)
            return {'CANCELLED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='Z', axis_up='Y')
class CARNIVORES_OT_import_car(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = 'carnivores.import_car'
    bl_label = 'Import .CAR Model'
    bl_description = 'Import a Carnivores .car model file'
    bl_options = {'PRESET'}
    filename_ext = '.car'
    filter_glob: bpy.props.StringProperty(
        default='*.car', 
        options={'HIDDEN'}, 
        maxlen=255
    )

    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement)
    directory: bpy.props.StringProperty(subtype='DIR_PATH')

    scale: bpy.props.FloatProperty(
        name='Import Scale',
        description='Scale factor applied to imported coordinates. The standard Carnivores counterpart is Export Scale 100.0.',
        default=0.01,
        min=0.01,
        max=100
    )
    import_textures: bpy.props.BoolProperty(
        name='Import Textures', 
        description='Import textures', 
        default=True
    )
    create_materials: bpy.props.BoolProperty(
        name='Create Materials', 
        description='Create materials for the mesh and the world',
        default=True
    )
    normal_smooth: bpy.props.BoolProperty(
        name='Smooth Faces', 
        description='Whether to smooth out faces or leave them flat (faceted) at import',
        default=True
    )
    validate: bpy.props.BoolProperty(
        name='Compatibility Checks',
        description='Report legacy AltEdit and current C2 MEE compatibility constraints; structural safety checks always run',
        default=True
    )
    flip_handedness: bpy.props.BoolProperty(
        name='Use Carnivores Coordinate Conversion',
        description='Apply the default Carnivores file-to-Blender coordinate conversion. Disable only for a deliberately custom coordinate workflow.',
        default=True
    )
    import_animations: bpy.props.BoolProperty(
        name='Import Animations', 
        description='Import animations as shape keys',
        default=True
    )
    use_absolute_shape_keys: bpy.props.BoolProperty(
        name="Absolute Shape Keys",
        description="Use Absolute Shape Keys (Evaluation Time) for a cleaner Action Editor and easier timing edits",
        default=True,
    )
    use_kps_timing: bpy.props.BoolProperty(
        name="Respect KPS Timing",
        description="Align keyframes to KPS timing (results in sub-frame positions). Disable to snap to integer frames.",
        default=True
    )
    import_sounds: bpy.props.BoolProperty(
        name='Import Sounds',
        description='Import embedded sounds as sound datablocks. If animations are disabled, sounds are imported without linked Actions.',
        default=True
    )
    select_imported: bpy.props.BoolProperty(
        name="Select Imported Objects",
        description="Select imported primary mesh objects and make the last one active after a successful import.",
        default=True,
    )
    frame_imported: bpy.props.BoolProperty(
        name="Frame Imported Objects",
        description="Frame imported objects in the invoking 3D View when the import is run from a compatible viewport.",
        default=False,
    )
    smooth_weights: bpy.props.BoolProperty(
        name="Smooth Weights",
        description="Smooth generated deform groups. CAR stores owner IDs rather than a bone hierarchy, so this affects generated weights only.",
        default=False
    )
    smooth_iterations: bpy.props.IntProperty(
        name="Smoothing Iterations",
        description="Number of smoothing passes. Higher values mean softer joints",
        default=3,
        min=1,
        max=10
    )
    smooth_factor: bpy.props.FloatProperty(
        name="Smoothing Factor",
        description="Intensity of smoothing per pass (0.1 = subtle, 0.9 = aggressive)",
        default=0.5,
        min=0.01,
        max=1.0
    )
    smooth_joints_only: bpy.props.BoolProperty(
        name="Smooth Joints Only",
        description="Only smooth areas where different bone influences meet (preserves limb rigidity)",
        default=True
    )

    def invoke(self, context, event):
        _apply_import_focus_preferences(self)
        return bpy_extras.io_utils.ImportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = _configure_operator_layout(self.layout)
        _draw_operator_title(layout, ".CAR Import Options", icon='IMPORT')
        _draw_advanced_coordinate_conversion(layout, self)

        content = _operator_panel(
            layout,
            "carnivores_import_car_content",
            "Content",
            icon='MATERIAL',
            header_prop=(self, "import_textures", "Textures"),
        )
        if content:
            content.enabled = self.import_textures
            content.prop(self, "create_materials", text="Materials")

        geometry = _operator_panel(
            layout,
            "carnivores_import_car_geometry",
            "Geometry",
            icon='MESH_DATA',
        )
        if geometry:
            geometry.prop(self, "scale", text="Scale")
            _draw_scale_note(geometry, "Import")
            geometry.prop(self, "normal_smooth", text="Smooth Faces")

        animation = _operator_panel(
            layout,
            "carnivores_import_car_animation",
            "Animation",
            icon='ANIM_DATA',
            header_prop=(self, "import_animations", "Animations"),
        )
        if animation:
            if self.import_animations:
                animation.prop(self, "use_absolute_shape_keys", text="Absolute Keys")
                animation.prop(self, "use_kps_timing", text="Respect KPS")
            animation.prop(self, "import_sounds", text="Sounds")
            if not self.import_animations:
                animation.label(text="Sounds import without linked Actions.", icon='INFO')

        rig = _operator_panel(
            layout,
            "carnivores_import_car_deformation",
            "Deformation",
            default_closed=True,
            icon='ARMATURE_DATA',
            header_prop=(self, "smooth_weights", "Smooth Weights"),
        )
        if rig:
            rig.label(text="CAR stores owner IDs; smoothing affects generated weights.", icon='INFO')
            if self.smooth_weights:
                rig.prop(self, "smooth_iterations", text="Iterations")
                rig.prop(self, "smooth_factor", text="Strength")
                rig.prop(self, "smooth_joints_only", text="Joints Only")

        compatibility = _operator_panel(
            layout,
            "carnivores_import_car_compatibility",
            "Compatibility",
            default_closed=True,
            icon='CHECKMARK',
            header_prop=(self, "validate", "Checks"),
        )
        if compatibility:
            compatibility.label(text="Adds legacy and C2 diagnostics.", icon='INFO')

        post_import = _operator_panel(
            layout,
            "carnivores_import_car_after_import",
            "After Import",
            default_closed=True,
            icon='IMPORT',
        )
        if post_import:
            post_import.prop(self, "select_imported", text="Select Objects")
            post_import.prop(self, "frame_imported", text="Frame Objects")

    @common.timed('CARNIVORES_OT_import_car.execute', is_operator=True)
    def execute(self, context):
        record_operator_options(
            self,
            [
                "scale",
                "import_textures",
                "create_materials",
                "normal_smooth",
                "validate",
                "flip_handedness",
                "import_animations",
                "use_absolute_shape_keys",
                "use_kps_timing",
                "import_sounds",
                "select_imported",
                "frame_imported",
                "smooth_weights",
                "smooth_iterations",
                "smooth_factor",
                "smooth_joints_only",
                "axis_forward",
                "axis_up",
            ],
        )
        handedness_matrix = mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) if self.flip_handedness else mathutils.Matrix.Identity(4)
        import_matrix = mathutils.Matrix.Scale(self.scale, 4) @ handedness_matrix @ bpy_extras.io_utils.axis_conversion(
            from_forward=self.axis_forward, from_up=self.axis_up, to_forward='Y', to_up='Z').to_4x4()
        import_matrix_np = np.array(import_matrix)
        report = OperationReport(".CAR import", text_name="Carnivores_Import_Report")
        filepaths = [os.path.join(self.directory, f.name) for f in self.files]
        valid_paths = [fp for fp in filepaths if os.path.isfile(fp)]
        if not valid_paths:
            message = 'No valid .car files selected.'
            report.error(
                "Input",
                message,
                suggested_action="Select one or more existing .car files.",
            )
            report.set_outcome(0, 0, 0)
            self.report({'ERROR'}, message)
            _finalize_operation_report(self, report)
            return {'CANCELLED'}

        imported_files = []
        failed_files = []
        imported_objects = []
        created_collections = []
        created_animation_count = 0
        created_sound_count = 0
        parsed_animation_count = 0
        parsed_sound_count = 0

        for filepath in valid_paths:
            coll = None
            filename = os.path.basename(filepath)
            try:
                mesh_name, _ = io_utils.generate_names(filepath)  # Ignore basename; use model_name below
                coll = io_utils.create_import_collection(os.path.splitext(filename)[0])
                header, model_name, faces, uvs, vertices, bone_names, owner_source, texture, texture_height, warnings, animations, sounds, cross_ref = parse_car(
                    filepath,
                    validate=self.validate,
                    parse_texture=self.import_textures,
                    flip_handedness=self.flip_handedness,
                    import_sounds=self.import_sounds,
                    parse_animations=self.import_animations,
                )
                
                owner_mapping = build_owner_mapping(owner_source)

                debug(f"SOUNDS: {len(sounds)}")
                for s in sounds:
                    debug(f"  -> {s['name']} {s['data'].size} samples")
                debug(f"CROSS_REF (first 10): {cross_ref[:10]}")
                
                verticesTransformedPos = io_utils.apply_import_matrix(vertices['coord'], import_matrix_np)
                # Use bone_names from parser (already handles dummies/offset if needed)
                obj = io_utils.create_mesh_object(mesh_name, verticesTransformedPos, faces['v'], model_name, self.normal_smooth, faces['flags'])
                coll.objects.link(obj)
                obj.carnivores_reconstruct_smooth_weights = self.smooth_weights
                obj.carnivores_reconstruct_smooth_iterations = self.smooth_iterations
                obj.carnivores_reconstruct_smooth_factor = self.smooth_factor
                obj.carnivores_reconstruct_smooth_joints_only = self.smooth_joints_only
                obj.data[OWNER_MAPPING_PROPERTY] = owner_mapping_to_metadata(owner_mapping)

                owner_attr = obj.data.attributes.get("carnivores_owner_index")
                if owner_attr is None:
                    owner_attr = obj.data.attributes.new(name="carnivores_owner_index", type='INT', domain='POINT')
                owner_values = owner_mapping.compact_per_vertex
                if owner_values.size == len(obj.data.vertices):
                    owner_attr.data.foreach_set("value", owner_values)
                else:
                    message = (
                        f"Owner attribute size mismatch for '{obj.name}' "
                        f"(expected {len(obj.data.vertices)}, got {owner_values.size}); "
                        "skipping normalized owner cache."
                    )
                    warn(message)
                    report.warning(
                        "Owner mapping",
                        message,
                        source=filename,
                        destination=coll.name if coll else "",
                        suggested_action="Inspect owner data before reconstructing a rig.",
                    )

                owner_source_attr = obj.data.attributes.get("carnivores_owner_source")
                if owner_source_attr is None:
                    owner_source_attr = obj.data.attributes.new(name="carnivores_owner_source", type='INT', domain='POINT')
                owner_source_values = np.asarray(owner_source, dtype=np.int32)
                if owner_source_values.size == len(obj.data.vertices):
                    owner_source_attr.data.foreach_set("value", owner_source_values)

                io_utils.create_uv_map(obj.data, uvs)
                # Create shape keys
                actions = []
                imported_sounds = []
                file_parsed_animation_count = len(animations)
                file_parsed_sound_count = len(sounds)
                if self.import_animations and animations:
                    anim_utils.create_shape_keys_from_car_animations(obj, animations, import_matrix_np, use_absolute=self.use_absolute_shape_keys)
                    # Automatically create fast actions + NLA strips
                    actions = []
                    try:
                        actions = anim_utils.auto_create_shape_key_actions_from_car(
                            obj, 
                            frame_step=1, 
                            parsed_animations=animations, 
                            use_absolute=self.use_absolute_shape_keys,
                            use_kps_timing=self.use_kps_timing
                        )
                    except Exception as e:
                        message = f"Failed to auto-create animations: {e}"
                        report.warning(
                            "Animation setup",
                            message,
                            source=filename,
                            destination=coll.name if coll else "",
                            suggested_action="Review imported shape keys and create actions manually if needed.",
                        )
                if self.import_sounds and sounds:
                    imported_sounds = anim_utils.import_car_sounds(self, sounds, model_name, context)
                    if actions:
                        anim_utils.associate_sounds_with_animations(
                            self, obj, animations, cross_ref, imported_sounds, actions
                        )
                if self.import_textures and texture is not None:
                    image = io_utils.create_image_texture(texture, texture_height, model_name)
                    if self.create_materials:
                        material = io_utils.create_texture_material(image, model_name)
                        obj.data.materials.append(material)
                # Vertex groups use compact IDs; the structured vertices retain raw CAR owners.
                if len(bone_names) > 0:
                    io_utils.create_vertex_groups_from_bones(obj, bone_names, owner_mapping.compact_per_vertex)
                    if self.smooth_weights:
                        io_utils.smooth_vertex_weights(obj, iterations=self.smooth_iterations, factor=self.smooth_factor, joints_only=self.smooth_joints_only)
                # No hooks/armature for .CAR (owners only; no positions/parents)
                if warnings:
                    for warning in warnings:
                        report.warning(
                            "Parser warning",
                            warning,
                            source=filename,
                            destination=coll.name if coll else "",
                            suggested_action="Review the complete report before export.",
                        )
                imported_files.append(filename)
                imported_objects.append(obj)
                if coll:
                    created_collections.append(coll.name)
                created_animation_count += len(actions)
                created_sound_count += len(imported_sounds)
                parsed_animation_count += file_parsed_animation_count
                parsed_sound_count += file_parsed_sound_count
                report.info(
                    "Import",
                    "Imported successfully.",
                    source=filename,
                    destination=coll.name if coll else "",
                )
            except Exception as exc:
                message = f"Failed to import {filename}: {exc}"
                error(f"[Import .CAR] {message}")
                report.error(
                    "Import",
                    str(exc),
                    source=filename,
                    suggested_action="Check the file and import options, then retry.",
                )
                failed_files.append(filename)
                _remove_failed_import_collection(coll)
                continue

        if self.create_materials and self.import_textures and imported_files:
            io_utils.setup_custom_world_shader()

        completed = _report_batch_summary(
            self,
            ".CAR import",
            len(valid_paths),
            imported_files,
            failed_files,
            report=report,
        )
        if imported_objects:
            _report_import_summary(
                report,
                created_collections,
                [obj.name for obj in imported_objects],
                animations=created_animation_count,
                sounds=created_sound_count,
                parsed_animations=parsed_animation_count,
                parsed_sounds=parsed_sound_count,
            )
            focus = _post_import_focus(
                context,
                imported_objects,
                select_imported=self.select_imported,
                frame_imported=self.frame_imported,
            )
            if self.select_imported:
                report.info("Post-import", f"Selected {focus['selected']} imported primary mesh object(s); the last imported mesh is active.")
            if self.frame_imported:
                if focus["framed"]:
                    report.info("Post-import", "Framed imported objects in the invoking 3D View.")
                elif focus["message"]:
                    report.info("Post-import", focus["message"], suggested_action="Run the import from a 3D View if viewport framing is desired.")
        _finalize_operation_report(self, report)

        return {'FINISHED'} if completed else {'CANCELLED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='Z', axis_up='Y')
class CARNIVORES_OT_export_3dn(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "carnivores.export_3dn"
    bl_label = "Export .3DN Model"
    bl_description = "Export an active mesh as a static .3dn model for Carnivores: Dinosaur Hunter mobile/HD titles"
    bl_options = {'PRESET'}
    
    filename_ext = ".3dn"
    filter_glob: bpy.props.StringProperty(default="*.3dn", options={'HIDDEN'}, maxlen=255)
    
    scale: bpy.props.FloatProperty(
        name="Export Scale",
        description="Scale factor applied to exported coordinates. The standard Carnivores counterpart is Import Scale 0.01.",
        default=100.0,
        min=1.0,
        max=1000.0,
    )
    
    model_name: bpy.props.StringProperty(
        name="Model Name Override",
        description="Internal model name (max 32 chars). Defaults to filename if empty.",
        default="",
        maxlen=32
    )

    has_sprite: bpy.props.BoolProperty(
        name="Has Sprite",
        description="Whether the model has an associated sprite",
        default=False
    )

    sprite_name: bpy.props.StringProperty(
        name="Sprite Name",
        description="Name of the associated sprite (max 32 chars)",
        default="",
        maxlen=32
    )
    
    flip_u: bpy.props.BoolProperty(
        name="Flip U",
        description="Flip U coordinate integers",
        default=False,
    )
    
    flip_v: bpy.props.BoolProperty(
        name="Flip V",
        description="Flip V coordinate integers",
        default=False,
    )
    
    flip_handedness: bpy.props.BoolProperty(
        name='Use Carnivores Coordinate Conversion',
        description='Apply the default Carnivores Blender-to-file coordinate conversion. Disable only for a deliberately custom coordinate workflow.',
        default=True
    )
    preflight_validation: bpy.props.BoolProperty(
        name="Preflight Validation",
        description="Run non-destructive mesh, UV, rig, name, and C2 MEE checks before .3DN export.",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        if _active_mesh(context):
            return True
        return _poll_message(cls, "Select an active mesh object to export as .3DN.")

    def invoke(self, context, event):
        _apply_operation_preferences(self, 'EXPORT')
        return bpy_extras.io_utils.ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = _configure_operator_layout(self.layout)
        _draw_operator_title(layout, ".3DN Export Options", icon='EXPORT')
        _draw_advanced_coordinate_conversion(layout, self)

        content = _operator_panel(
            layout,
            "carnivores_export_3dn_content",
            "Content",
            icon='MATERIAL',
            header_prop=(self, "has_sprite", "Sprite"),
        )
        if content:
            content.prop(self, "model_name", text="Model Name")
            if self.has_sprite:
                content.prop(self, "sprite_name", text="Sprite Name")
            content.label(text="Texture Coordinates", icon='INFO')
            row = content.row(align=True)
            row.prop(self, "flip_u", text="Flip U")
            row.prop(self, "flip_v", text="Flip V")

        geometry = _operator_panel(
            layout,
            "carnivores_export_3dn_geometry",
            "Geometry",
            icon='MESH_DATA',
        )
        if geometry:
            geometry.prop(self, "scale", text="Scale")
            _draw_scale_note(geometry, "Export")

        compatibility = _operator_panel(
            layout,
            "carnivores_export_3dn_compatibility",
            "Compatibility",
            default_closed=True,
            icon='CHECKMARK',
            header_prop=(self, "preflight_validation", "Checks"),
        )
        if compatibility:
            compatibility.label(text="Static mobile/HD model format; animation is separate.", icon='INFO')

    @common.timed("CARNIVORES_OT_export_3dn.execute", is_operator=True)
    def execute(self, context):
        record_operator_options(
            self,
            [
                "scale",
                "model_name",
                "has_sprite",
                "sprite_name",
                "flip_u",
                "flip_v",
                "flip_handedness",
                "preflight_validation",
                "axis_forward",
                "axis_up",
            ],
        )
        handedness_matrix = mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) if self.flip_handedness else mathutils.Matrix.Identity(4)
        export_matrix = (
            bpy_extras.io_utils.axis_conversion(
                from_forward='Y',
                from_up='Z',
                to_forward=self.axis_forward,
                to_up=self.axis_up
            ).to_4x4()
            @ handedness_matrix
            @ mathutils.Matrix.Scale(self.scale, 4) 
        )
        export_matrix_np = np.array(export_matrix)
        
        report = OperationReport(".3DN export", text_name="Carnivores_Export_Report")
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            message = "No active mesh object selected."
            report.error(
                "Input",
                message,
                suggested_action="Select an active mesh object and retry.",
            )
            report.set_outcome(0, 0, 0)
            self.report({'ERROR'}, message)
            _finalize_operation_report(self, report)
            return {'CANCELLED'}

        model_name = self.model_name if self.model_name else os.path.splitext(os.path.basename(self.filepath))[0]

        try:
            if self.preflight_validation:
                validation = validate_blender_model(
                    obj,
                    "3DN",
                    export_textures=False,
                    filepath=self.filepath,
                    model_name=model_name,
                    has_sprite=self.has_sprite,
                    sprite_name=self.sprite_name,
                    export_matrix=export_matrix_np,
                )
                if not _append_preflight_report(report, validation, obj.name, os.path.basename(self.filepath)):
                    report.set_outcome(1, 0, 1)
                    self.report({'ERROR'}, ".3DN preflight failed; no file was written.")
                    _finalize_operation_report(self, report)
                    return {'CANCELLED'}
            export_3dn(
                self.filepath,
                obj,
                export_matrix_np,
                model_name=model_name,
                has_sprite=self.has_sprite,
                sprite_name=self.sprite_name,
                flip_u=self.flip_u,
                flip_v=self.flip_v,
                flip_handedness=self.flip_handedness
            )
            destination = os.path.basename(self.filepath)
            report.set_outcome(1, 1, 0)
            report.info(
                "Export",
                "Exported successfully.",
                source=obj.name,
                destination=destination,
            )
            self.report({'INFO'}, f"Exported {destination}")
            _finalize_operation_report(self, report)
            return {'FINISHED'}
        except Exception as exc:
            message = f"Export failed: {exc}"
            report.set_outcome(1, 0, 1)
            report.error(
                "Export",
                str(exc),
                source=obj.name,
                destination=os.path.basename(self.filepath),
                suggested_action="Review the complete report and export settings.",
            )
            self.report({'ERROR'}, message)
            error(f"[Export .3DN] {message}")
            _finalize_operation_report(self, report)
            return {'CANCELLED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='Z', axis_up='Y')
class CARNIVORES_OT_export_vtl(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "carnivores.export_vtl"
    bl_label = "Export .VTL Animation"
    bl_description = "Export active animation as Carnivores .vtl animation file"
    bl_options = {'PRESET'}
    
    filename_ext = ".vtl"
    filter_glob: bpy.props.StringProperty(default="*.vtl", options={'HIDDEN'}, maxlen=255)
    
    scale: bpy.props.FloatProperty(
        name="Export Scale",
        description="Scale factor applied to exported coordinates. The standard Carnivores counterpart is Import Scale 0.01.",
        default=100.0,
        min=1.0,
        max=1000.0,
    )
    
    flip_handedness: bpy.props.BoolProperty(
        name='Use Carnivores Coordinate Conversion',
        description='Apply the default Carnivores Blender-to-file coordinate conversion. Disable only for a deliberately custom coordinate workflow.',
        default=True
    )
    preflight_validation: bpy.props.BoolProperty(
        name="Preflight Validation",
        description="Run non-destructive animation, timing, coordinate, and C2 MEE checks before .VTL export.",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        if not obj:
            return _poll_message(cls, "Select an active mesh object to export .VTL animation.")

        has_shape_key_animation = bool(
            obj.data.shape_keys and obj.data.shape_keys.animation_data
        )
        parent = getattr(obj, "parent", None)
        has_parent_animation = bool(
            parent and parent.type == 'ARMATURE' and parent.animation_data
        )
        has_object_animation = bool(obj.animation_data)
        if has_shape_key_animation or has_parent_animation or has_object_animation:
            return True
        return _poll_message(cls, "The active mesh has no shape-key, object, or parent-armature animation data.")

    def invoke(self, context, event):
        _apply_operation_preferences(self, 'EXPORT')
        return bpy_extras.io_utils.ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = _configure_operator_layout(self.layout)
        _draw_operator_title(layout, ".VTL Export Options", icon='EXPORT')
        _draw_advanced_coordinate_conversion(layout, self)

        animation = _operator_panel(
            layout,
            "carnivores_export_vtl_animation",
            "Animation",
            icon='ANIM_DATA',
        )
        if animation:
            animation.label(text="Shape keys, object, or armature animation.", icon='INFO')

        geometry = _operator_panel(
            layout,
            "carnivores_export_vtl_geometry",
            "Geometry",
            icon='MESH_DATA',
        )
        if geometry:
            geometry.prop(self, "scale", text="Scale")
            _draw_scale_note(geometry, "Export")

        compatibility = _operator_panel(
            layout,
            "carnivores_export_vtl_compatibility",
            "Compatibility",
            default_closed=True,
            icon='CHECKMARK',
            header_prop=(self, "preflight_validation", "Checks"),
        )
        if compatibility:
            compatibility.label(text="Warnings allow export; errors block unsafe output.", icon='INFO')

    @common.timed("CARNIVORES_OT_export_vtl.execute", is_operator=True)
    def execute(self, context):
        record_operator_options(
            self,
            [
                "scale",
                "flip_handedness",
                "preflight_validation",
                "axis_forward",
                "axis_up",
            ],
        )
        handedness_matrix = mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) if self.flip_handedness else mathutils.Matrix.Identity(4)
        export_matrix = (
            bpy_extras.io_utils.axis_conversion(
                from_forward='Y',
                from_up='Z',
                to_forward=self.axis_forward,
                to_up=self.axis_up
            ).to_4x4()
            @ handedness_matrix
            @ mathutils.Matrix.Scale(self.scale, 4) 
        )
        export_matrix_np = np.array(export_matrix)
        
        report = OperationReport(".VTL export", text_name="Carnivores_Export_Report")
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            message = "No active mesh object selected."
            report.error(
                "Input",
                message,
                suggested_action="Select an active mesh object and retry.",
            )
            report.set_outcome(0, 0, 0)
            self.report({'ERROR'}, message)
            _finalize_operation_report(self, report)
            return {'CANCELLED'}

        try:
            if self.preflight_validation:
                validation = validate_blender_model(
                    obj,
                    "VTL",
                    export_textures=False,
                    check_audio=False,
                    export_matrix=export_matrix_np,
                )
                if not _append_preflight_report(report, validation, obj.name, os.path.basename(self.filepath)):
                    report.set_outcome(1, 0, 1)
                    self.report({'ERROR'}, ".VTL preflight failed; no file was written.")
                    _finalize_operation_report(self, report)
                    return {'CANCELLED'}
            export_vtl(
                self.filepath,
                obj,
                export_matrix_np
            )
            destination = os.path.basename(self.filepath)
            report.set_outcome(1, 1, 0)
            report.info(
                "Export",
                "Exported successfully.",
                source=obj.name,
                destination=destination,
            )
            self.report({'INFO'}, f"Exported {destination}")
            _finalize_operation_report(self, report)
            return {'FINISHED'}
        except Exception as exc:
            message = f"Export failed: {exc}"
            report.set_outcome(1, 0, 1)
            report.error(
                "Export",
                str(exc),
                source=obj.name,
                destination=os.path.basename(self.filepath),
                suggested_action="Review the complete report and export settings.",
            )
            self.report({'ERROR'}, message)
            error(f"[Export .VTL] {message}")
            _finalize_operation_report(self, report)
            return {'CANCELLED'}

class CARNIVORES_OT_modal_message(bpy.types.Operator):
    bl_idname = "carnivores.modal_message"
    bl_label = "Carnivores Operation Report"
    bl_description = "Show a concise operation result and provide access to the complete report."

    message: bpy.props.StringProperty(default="")
    report_text_name: bpy.props.StringProperty(
        name="Report Text",
        description="Text datablock containing the complete operation report",
        default="",
        options={'HIDDEN'},
    )

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        layout = self.layout
        for line in self.message.split('\n'):
            layout.label(text=line)

        if self.report_text_name:
            layout.separator()
            row = layout.row(align=True)
            open_op = row.operator("carnivores.open_report", text="Open Report", icon='TEXT')
            open_op.text_name = self.report_text_name
            copy_op = row.operator("carnivores.copy_report", text="Copy Report", icon='COPYDOWN')
            copy_op.text_name = self.report_text_name
            
    def execute(self, context):
        return {'FINISHED'}
