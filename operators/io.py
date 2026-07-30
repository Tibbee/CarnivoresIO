import bpy
import bpy_extras.io_utils
import os
import mathutils
import numpy as np
from ..utils import io as io_utils
from ..utils import animation as anim_utils
from ..utils import common
from ..utils.logger import info, debug, warn, error
from ..utils.reporting import OperationReport, write_report_text
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
        name="Scale",
        description="Scale factor for the imported model",
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
    bone_import_type: bpy.props.EnumProperty(
        name="Bone Import Type",
        description="Choose how bones are imported",
        items=[
            ('NONE', "None", "Do not import bones"),
            ('ARMATURE', "Armature", "Import as armature"),
            ('HOOKS', "Hooks", "Import as hooks"),
        ],
        default='HOOKS'
    )
    validate: bpy.props.BoolProperty(
        name="Compatibility Checks",
        description="Report legacy AltEdit and current C2 MEE compatibility constraints; structural safety checks always run",
        default=True
    )
    flip_handedness: bpy.props.BoolProperty(
        name='Flip Handedness',
        description='Negate X-axis to match game\'s left-handed coordinate system (fixes mirroring)',
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
    
    def draw(self, context):
        layout = self.layout

        layout.label(text="Import Options")
        layout.prop(self, "scale")
        layout.prop(self, "import_textures")
        row = layout.row()
        row.enabled = self.import_textures  # Disable based on the checkbox
        row.prop(self, "create_materials")
        layout.prop(self, "normal_smooth")
        layout.prop(self, "validate")
        layout.prop(self, 'flip_handedness')
        
        layout.separator() 
        
        layout.label(text="Bone Import")
        layout.prop(self, "bone_import_type")
        if self.bone_import_type != 'NONE':
            layout.prop(self, "smooth_weights")
            if self.smooth_weights:
                layout.prop(self, "smooth_iterations")
                layout.prop(self, "smooth_factor")
                layout.prop(self, "smooth_joints_only")
        
        layout.separator() 
        
        box = layout.box()
        box.label(text="Axis Conversion")
        box.prop(self, "axis_forward")
        box.prop(self, "axis_up")
        
        layout.separator() 
        
    @common.timed("CARNIVORES_OT_import_3df.execute", is_operator=True)
    def execute(self, context):
        
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
        name="Scale",
        description="Scale factor to apply on export (applies to vertex coordinates)",
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
        name='Flip Handedness',
        description='Negate X-axis to match game\'s left-handed coordinate system (fixes mirroring)',
        default=True  # Start disabled to match your current diff
    )

    @classmethod
    def poll(cls, context):
        if _active_mesh(context):
            return True
        return _poll_message(cls, "Select an active mesh object to export as .3DF.")

    def draw(self, context):
        layout = self.layout
        layout.label(text="Export Options")
        layout.prop(self, "use_multi_export")
        layout.label(text="Exports active object to the specified file" if not self.use_multi_export else "Exports selected objects to separate files with filename as prefix")
        layout.prop(self, "scale")
        layout.prop(self, "export_textures")
        row = layout.row()
        row.enabled = self.export_textures
        row.prop(self, "flip_u")
        row.prop(self, "flip_v")
        layout.prop(self, 'flip_handedness')
        layout.separator()
        box = layout.box()
        box.label(text="Axis Conversion")
        box.prop(self, "axis_forward")
        box.prop(self, "axis_up")

    @common.timed("CARNIVORES_OT_export_3df.execute", is_operator=True)
    def execute(self, context):
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
        name="Scale",
        description="Scale factor to apply on export (applies to vertex coordinates)",
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
        name='Flip Handedness',
        description='Negate X-axis to match game\'s left-handed coordinate system',
        default=True
    )

    @classmethod
    def poll(cls, context):
        if _active_mesh(context):
            return True
        return _poll_message(cls, "Select an active mesh object to export as .CAR.")

    def draw(self, context):
        layout = self.layout
        layout.label(text="Export Options")
        layout.prop(self, "scale")
        layout.prop(self, "model_name")
        layout.prop(self, "export_textures")
        row = layout.row()
        row.enabled = self.export_textures
        row.prop(self, "flip_u")
        row.prop(self, "flip_v")
        layout.prop(self, 'flip_handedness')
        layout.separator()
        box = layout.box()
        box.label(text="Axis Conversion")
        box.prop(self, "axis_forward")
        box.prop(self, "axis_up")

    @common.timed("CARNIVORES_OT_export_car.execute", is_operator=True)
    def execute(self, context):
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

        try:
            export_car(
                self.filepath,
                obj,
                export_matrix_np,
                export_textures=self.export_textures,
                flip_u=self.flip_u,
                flip_v=self.flip_v,
                flip_handedness=self.flip_handedness,
                model_name_override=self.model_name
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
        name='Scale', 
        description='Scale factor for the imported model', 
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
        name='Flip Handedness', 
        description="Negate X-axis to match game\'s left-handed coordinate system (fixes mirroring)", 
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
        default=False
    )
    use_kps_timing: bpy.props.BoolProperty(
        name="Respect KPS Timing",
        description="Align keyframes to KPS timing (results in sub-frame positions). Disable to snap to integer frames.",
        default=True
    )
    import_sounds: bpy.props.BoolProperty(
        name='Import Sounds',
        description='Import embedded sounds as sound datablocks',
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

    def draw(self, context):
        layout = self.layout
        
        # General Settings
        box = layout.box()
        box.label(text="General Settings", icon='PREFERENCES')
        box.prop(self, 'scale')
        box.prop(self, 'import_textures')
        row = box.row()
        row.enabled = self.import_textures
        row.prop(self, 'create_materials')
        box.prop(self, 'normal_smooth')
        box.prop(self, 'validate')
        box.prop(self, 'flip_handedness')
        
        # Animation Settings
        box = layout.box()
        box.prop(self, 'import_animations', icon='ANIM')
        if self.import_animations:
            sub = box.box()
            sub.prop(self, 'use_absolute_shape_keys')
            sub.prop(self, 'use_kps_timing')
        box.prop(self, 'import_sounds')
        
        # Bone & Smoothing Settings
        box = layout.box()
        box.label(text="Mesh & Bone Smoothing", icon='MOD_SMOOTH')
        box.prop(self, "smooth_weights")
        if self.smooth_weights:
            sub = box.box()
            sub.prop(self, "smooth_iterations")
            sub.prop(self, "smooth_factor")
            sub.prop(self, "smooth_joints_only")
            
        # Advanced
        box = layout.box()
        box.label(text="Advanced Axis Settings", icon='TRIA_DOWN')
        box.prop(self, 'axis_forward')
        box.prop(self, 'axis_up')
        layout.separator()

    @common.timed('CARNIVORES_OT_import_car.execute', is_operator=True)
    def execute(self, context):
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
                    import_sounds=self.import_sounds
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
                    anim_utils.associate_sounds_with_animations(self, obj, animations, cross_ref, imported_sounds, actions)
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
        _finalize_operation_report(self, report)

        return {'FINISHED'} if completed else {'CANCELLED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='Z', axis_up='Y')
class CARNIVORES_OT_export_3dn(bpy.types.Operator, bpy_extras.io_utils.ExportHelper):
    bl_idname = "carnivores.export_3dn"
    bl_label = "Export .3DN Model"
    bl_description = "Export active mesh object as Carnivores .3dn model file"
    bl_options = {'PRESET'}
    
    filename_ext = ".3dn"
    filter_glob: bpy.props.StringProperty(default="*.3dn", options={'HIDDEN'}, maxlen=255)
    
    scale: bpy.props.FloatProperty(
        name="Scale",
        description="Scale factor to apply on export (applies to vertex coordinates)",
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
        name='Flip Handedness',
        description='Negate X-axis to match game\'s left-handed coordinate system',
        default=True
    )

    @classmethod
    def poll(cls, context):
        if _active_mesh(context):
            return True
        return _poll_message(cls, "Select an active mesh object to export as .3DN.")

    def draw(self, context):
        layout = self.layout
        layout.label(text="Export Options")
        layout.prop(self, "scale")
        layout.prop(self, "model_name")
        layout.prop(self, "has_sprite")
        if self.has_sprite:
            layout.prop(self, "sprite_name")
        
        layout.separator()
        layout.label(text="UV Options")
        row = layout.row()
        row.prop(self, "flip_u")
        row.prop(self, "flip_v")
        layout.prop(self, 'flip_handedness')
        layout.separator()
        box = layout.box()
        box.label(text="Axis Conversion")
        box.prop(self, "axis_forward")
        box.prop(self, "axis_up")

    @common.timed("CARNIVORES_OT_export_3dn.execute", is_operator=True)
    def execute(self, context):
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
        name="Scale",
        description="Scale factor to apply on export (applies to vertex coordinates)",
        default=100.0,
        min=1.0,
        max=1000.0,
    )
    
    flip_handedness: bpy.props.BoolProperty(
        name='Flip Handedness',
        description='Negate X-axis to match game\'s left-handed coordinate system',
        default=True
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

    def draw(self, context):
        layout = self.layout
        layout.label(text="Export Options")
        layout.prop(self, "scale")
        layout.prop(self, 'flip_handedness')
        layout.separator()
        box = layout.box()
        box.label(text="Axis Conversion")
        box.prop(self, "axis_forward")
        box.prop(self, "axis_up")

    @common.timed("CARNIVORES_OT_export_vtl.execute", is_operator=True)
    def execute(self, context):
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
