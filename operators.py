import aud # Import the aud module
import bpy
import bpy_extras.io_utils
import os
import mathutils
import bmesh
import numpy as np

from .parsers.parse_3df import parse_3df
from .parsers.parse_car import parse_car
from .parsers.export_3df import export_3df
from .parsers.export_car import export_car
from .core.constants import FACE_FLAG_OPTIONS

from . import utils

@bpy_extras.io_utils.orientation_helper(axis_forward='-Z', axis_up='Y')
class CARNIVORES_OT_import_3df(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    bl_idname = "carnivores.import_3df"
    bl_label = "Import .3DF Model"
    bl_description = "Import a Carnivores .3df model file"
    bl_options = {'PRESET'}
    
    filename_ext = ".3df"
    # File selector filter that window_manager.fileselect_add looks for from ImportHelper
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
        name="Run Validations",
        description="Enable file validity and error checking and if possible automatic repairs",
        default=False
    )
    flip_handedness: bpy.props.BoolProperty(
        name='Flip Handedness',
        description='Negate X-axis to match game\'s left-handed coordinate system (fixes mirroring)',
        default=True
    )
    
    def draw (self, context):
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
        
        layout.separator() 
        
        box = layout.box()
        box.label(text="Axis Conversion")
        box.prop(self, "axis_forward")
        box.prop(self, "axis_up")
        
        layout.separator() 
        
    @utils.timed("CARNIVORES_OT_import_3df.execute", is_operator=True)
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
        
        filepaths = [os.path.join(self.directory, f.name) for f in self.files]
        valid_paths = [fp for fp in filepaths if os.path.isfile(fp)]
        if not valid_paths:
            self.report({'ERROR'}, "No valid .3df files selected.")
            return {'CANCELLED'}
        for filepath in valid_paths:

            try:
                # Your existing parsing and importing logic here
                mesh_name, object_name = utils.generate_names(filepath)
                coll = utils.create_import_collection(object_name)
                header, faces, uvs, vertices, bones, bone_names, texture, texture_height, warnings = parse_3df(filepath, self.validate, self.import_textures, flip_handedness=self.flip_handedness)
                verticesTransformedPos = utils.apply_import_matrix(vertices['coord'], import_matrix_np)
                bonesTransformedPos = utils.apply_import_matrix(bones['pos'], import_matrix_np)

                obj = utils.create_mesh_object(
                    mesh_name,
                    verticesTransformedPos,
                    faces['v'],
                    object_name,
                    self.normal_smooth,
                    faces['flags']
                )

                coll.objects.link(obj)
                utils.create_uv_map(obj.data, uvs)
                if self.import_textures and texture is not None:
                    image = utils.create_image_texture(texture, texture_height, object_name)
                    if self.create_materials:
                        material = utils.create_texture_material(image, object_name)
                        obj.data.materials.append(material)

                if self.bone_import_type == 'HOOKS':
                    vertex_groups_by_index = utils.create_vertex_groups_from_bones(obj, bone_names, vertices['owner'])
                    hook_objects = utils.create_hooks(bone_names, bonesTransformedPos, bones['parent'], object_name, obj, coll)
                    utils.assign_hook_modifiers(obj, hook_objects, vertex_groups_by_index)

                elif self.bone_import_type == 'ARMATURE':
                    utils.create_vertex_groups_from_bones(obj, bone_names, vertices['owner'])
                    armature_obj = utils.create_armature(bone_names, bonesTransformedPos, bones['parent'], object_name, coll)
                    utils.assign_armature_modifier(obj, armature_obj)

                if warnings:
                    bpy.ops.carnivores.modal_message('INVOKE_DEFAULT', message="\n".join(warnings))

            except Exception as e:
                self.report({'ERROR'}, f"Failed to import {os.path.basename(filepath)} at parsing step: {str(e)}")
                if 'coll' in locals() and coll in bpy.data.collections:
                    bpy.data.collections.remove(coll, do_unlink=True)
                continue
                
        if self.create_materials and self.import_textures:
            utils.setup_custom_world_shader()
            
        return {'FINISHED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='-Z', axis_up='Y')
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

    @utils.timed("CARNIVORES_OT_export_3df.execute", is_operator=True)
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
        exported_files = []
        if self.use_multi_export:
            mesh_objects = [obj for obj in context.selected_objects if obj.type == 'MESH']
            if not mesh_objects:
                self.report({'ERROR'}, "No mesh objects selected for export.")
                return {'CANCELLED'}
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
                    exported_files.append(os.path.basename(filepath))
                except Exception as e:
                    self.report({'ERROR'}, f"Failed to export {obj.name} to {os.path.basename(filepath)}: {e}")
        else:
            obj = context.active_object
            if not obj or obj.type != 'MESH':
                self.report({'ERROR'}, "No active mesh object selected for single-file export.")
                return {'CANCELLED'}
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
                exported_files.append(os.path.basename(filepath))
            except Exception as e:
                self.report({'ERROR'}, f"Failed to export {obj.name} to {os.path.basename(filepath)}: {e}")
        if exported_files:
            self.report({'INFO'}, f"Exported {len(exported_files)} file(s): {', '.join(exported_files)}")
        else:
            self.report({'ERROR'}, "No files were exported due to errors.")
        return {'FINISHED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='-Z', axis_up='Y')
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

    @utils.timed("CARNIVORES_OT_export_car.execute", is_operator=True)
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
        
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "No active mesh object selected.")
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
            self.report({'INFO'}, f"Exported {os.path.basename(self.filepath)}")
            return {'FINISHED'}
        except Exception as e:
            self.report({'ERROR'}, f"Export failed: {e}")
            return {'CANCELLED'}

@bpy_extras.io_utils.orientation_helper(axis_forward='-Z', axis_up='Y')
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
        name='Run Validations', 
        description='Enable file validity and error checking and if possible automatic repairs',
        default=False
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
    import_sounds: bpy.props.BoolProperty(
        name='Import Sounds',
        description='Import embedded sounds as sound datablocks',
        default=True
    )

    def draw(self, context):
        layout = self.layout
        layout.label(text='Import Options')
        layout.prop(self, 'scale')
        layout.prop(self, 'import_textures')
        row = layout.row()
        row.enabled = self.import_textures
        row.prop(self, 'create_materials')
        layout.prop(self, 'normal_smooth')
        layout.prop(self, 'validate')
        layout.prop(self, 'flip_handedness')
        layout.prop(self, 'import_animations')
        layout.prop(self, 'import_sounds')
        layout.separator()
        box = layout.box()
        box.label(text='Axis Conversion')
        box.prop(self, 'axis_forward')
        box.prop(self, 'axis_up')
        layout.separator()

    @utils.timed('CARNIVORES_OT_import_car.execute', is_operator=True)
    def execute(self, context):
        handedness_matrix = mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) if self.flip_handedness else mathutils.Matrix.Identity(4)
        import_matrix = mathutils.Matrix.Scale(self.scale, 4) @ handedness_matrix @ bpy_extras.io_utils.axis_conversion(
            from_forward=self.axis_forward, from_up=self.axis_up, to_forward='Y', to_up='Z').to_4x4()
        import_matrix_np = np.array(import_matrix)
        filepaths = [os.path.join(self.directory, f.name) for f in self.files]
        valid_paths = [fp for fp in filepaths if os.path.isfile(fp)]
        if not valid_paths:
            self.report({'ERROR'}, 'No valid .car files selected.')
            return {'CANCELLED'}
        for filepath in valid_paths:
            try:
                mesh_name, _ = utils.generate_names(filepath)  # Ignore basename; use model_name below
                coll = utils.create_import_collection(os.path.splitext(os.path.basename(filepath))[0])
                header, model_name, faces, uvs, vertices, bone_names, texture, texture_height, warnings, animations, sounds, cross_ref = parse_car(
                    filepath,
                    validate=self.validate,
                    parse_texture=self.import_textures,
                    flip_handedness=self.flip_handedness,
                    import_sounds=self.import_sounds
                )
                print("SOUNDS:", len(sounds))
                for s in sounds:
                    print("  ->", s['name'], s['data'].size, "samples")
                print("CROSS_REF (first 10):", cross_ref[:10])
                # print(animations)  # Should show list of dicts
                verticesTransformedPos = utils.apply_import_matrix(vertices['coord'], import_matrix_np)
                # Use bone_names from parser (already handles dummies/offset if needed)
                obj = utils.create_mesh_object(mesh_name, verticesTransformedPos, faces['v'], model_name, self.normal_smooth, faces['flags'])
                coll.objects.link(obj)
                utils.create_uv_map(obj.data, uvs)
                # Create shape keys
                if self.import_animations and animations:
                    utils.create_shape_keys_from_car_animations(obj, animations, import_matrix_np)
                    # Automatically create fast actions + NLA strips
                    actions = []
                    try:
                        actions = utils.auto_create_shape_key_actions_from_car(obj, frame_step=1, parsed_animations=animations)
                    except Exception as e:
                        self.report({'WARNING'}, f"Failed to auto-create animations: {e}")
                if self.import_sounds and sounds:
                    imported_sounds = utils.import_car_sounds(self, sounds, model_name, context)
                    utils.associate_sounds_with_animations(self, obj, animations, cross_ref, imported_sounds, actions)
                if self.import_textures and texture is not None:
                    image = utils.create_image_texture(texture, texture_height, model_name)
                    if self.create_materials:
                        material = utils.create_texture_material(image, model_name)
                        obj.data.materials.append(material)
                # Vertex groups from owners (dummy bones if needed)
                if len(bone_names) > 0:
                    utils.create_vertex_groups_from_bones(obj, bone_names, vertices['owner'])
                # No hooks/armature for .CAR (owners only; no positions/parents)
                if warnings:
                    bpy.ops.carnivores.modal_message('INVOKE_DEFAULT', message='\n'.join(warnings))
            except Exception as e:
                self.report({'ERROR'}, f"Failed to import {os.path.basename(filepath)} at parsing step: {str(e)}")
                if 'coll' in locals() and bpy.data.collections.get(coll.name) is not None:
                    continue
        if self.create_materials and self.import_textures:
            utils.setup_custom_world_shader()
        return {'FINISHED'}

class CARNIVORES_OT_create_3df_flags(bpy.types.Operator):
    """Create a face-domain integer attribute named '3df_flags' (initialized to 0)"""
    bl_idname = "carnivores.create_3df_flags"
    bl_label = "Create 3df_flags Attribute"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a mesh object.")
            return {'CANCELLED'}

        mesh = obj.data
        attr = mesh.attributes.get("3df_flags")
        if attr:
            if attr.domain != 'FACE':
                self.report({'ERROR'}, "'3df_flags' attribute exists but is not FACE-domain.")
                return {'CANCELLED'}
            self.report({'INFO'}, "'3df_flags' attribute already exists.")
            return {'CANCELLED'}

        prev_mode = obj.mode
        if prev_mode != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')

        attr = mesh.attributes.new(name="3df_flags", type='INT', domain='FACE')
        face_count = len(mesh.polygons)
        if face_count > 0:
            zeros = [0] * face_count
            attr.data.foreach_set("value", zeros)

        if prev_mode != 'OBJECT':
            try:
                bpy.ops.object.mode_set(mode=prev_mode)
            except Exception as e:
                self.report({'WARNING'}, f"Failed to restore mode '{prev_mode}': {e}")
                return {'FINISHED'}

        self.report({'INFO'}, "'3df_flags' attribute created.")
        return {'FINISHED'}
            
class CARNIVORES_OT_modal_message(bpy.types.Operator):
    bl_idname = "carnivores.modal_message"
    bl_label = "Import Warnings"

    message: bpy.props.StringProperty(default="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        layout = self.layout
        for line in self.message.split('\n'):
            layout.label(text=line)
            
    def execute(self, context):
        return {'FINISHED'}

class VIEW3D_PT_3df_face_flags(bpy.types.Panel):
    bl_label = "3DF Face Flags"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Carnivores'

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            layout.label(text='Select a mesh object', icon='ERROR')
            return
        mesh = obj.data
        if '3df_flags' not in mesh.attributes:
            layout.label(text="No '3df_flags' attribute on mesh", icon='INFO')
            row = layout.row(align=True)
            row.operator('carnivores.create_3df_flags', icon='ADD', text="Create '3df_flags'")
            layout.label(text='Creates a face-domain INT attribute set to 0')
            return
        counts, total = utils.count_flag_hits(obj)
        if total == 0:
            layout.label(text='Mesh has no faces', icon='INFO')
            return
        mode_text = 'All faces' if obj.mode != 'EDIT' else 'Selected faces'
        layout.label(text=f"Face Flags ({mode_text}: {total})", icon='FACESEL')
        col = layout.column(align=True)
        label_fraction = .65
        for (bit, label, _) in FACE_FLAG_OPTIONS:
            count = counts.get(bit, 0)
            icon = 'CHECKBOX_HLT' if count > 0 else 'CHECKBOX_DEHLT'
            text = f"{label} ({count}/{total})"
            split = col.split(factor=label_fraction)
            left = split.column()
            right = split.column()
            left_row = left.row(align=True)
            left_row.label(text=text, icon=icon)
            btn_row = right.row(align=True)
            op = btn_row.operator('carnivores.modify_3df_flag', text='', icon='CHECKMARK')
            op.action = 'SET'
            op.flag_bit = bit
            op = btn_row.operator('carnivores.modify_3df_flag', text='', icon='X')
            op.action = 'CLEAR'
            op.flag_bit = bit
            op = btn_row.operator('carnivores.modify_3df_flag', text='', icon='ARROW_LEFTRIGHT')
            op.action = 'TOGGLE'
            op.flag_bit = bit
        layout.separator()
        layout.operator('carnivores.modify_3df_flag', text='Clear All Flags', icon='X').action = 'CLEAR_ALL'
            
class CARNIVORES_OT_select_by_flags(bpy.types.Operator):
    """Select/Deselect/Invert faces on the active mesh by 3DF flag mask"""
    bl_idname = "carnivores.select_by_flags"
    bl_label = "Select Faces by 3DF Flags"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        obj = context.active_object

        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh.")
            return {'CANCELLED'}

        # Build mask from Scene properties
        mask = 0
        for i, (bit, label, _) in enumerate(FACE_FLAG_OPTIONS):
            if getattr(scene, f"cf_flag_{i}", False):
                mask |= int(bit)

        if mask == 0:
            self.report({'ERROR'}, "No flags selected in the UI.")
            return {'CANCELLED'}

        mode = getattr(scene, "cf_select_mode", "ANY")
        action = getattr(scene, "cf_select_action", "SELECT")

        mesh = obj.data

        attr = mesh.attributes.get("3df_flags")
        if not attr:
            self.report({'ERROR'}, "Mesh has no '3df_flags' attribute. Create it first.")
            return {'CANCELLED'}

        if getattr(attr, "domain", None) != 'FACE':
            self.report({'ERROR'}, "'3df_flags' attribute is not a FACE-domain attribute.")
            return {'CANCELLED'}

        face_count = len(mesh.polygons)
        if face_count == 0:
            self.report({'INFO'}, "Mesh has no faces.")
            return {'CANCELLED'}

        was_edit = (obj.mode == 'EDIT')

        if was_edit:
            # Use BMesh for Edit mode to handle full deselection
            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.ensure_lookup_table()
            layer = bm.faces.layers.int.get("3df_flags")
            if not layer:
                self.report({'ERROR'}, "'3df_flags' layer missing in BMesh.")
                return {'CANCELLED'}

            # Get flags and current selections (vectorized)
            vals = np.array([f[layer] for f in bm.faces], dtype=np.int32)
            sel_flags = np.array([f.select for f in bm.faces], dtype=np.int8)

            # Compute matches
            if mode == 'ANY':
                matches = (vals & mask) != 0
            elif mode == 'ALL':
                matches = (vals & mask) == mask
            elif mode == 'NONE':
                matches = (vals & mask) == 0
            else:
                self.report({'ERROR'}, f"Unknown mode: {mode}")
                return {'CANCELLED'}

            # Compute new selections
            new_sel = sel_flags.copy()
            if action == 'SELECT':
                new_sel[matches] = 1
            elif action == 'DESELECT':
                new_sel[matches] = 0
            elif action == 'INVERT':
                new_sel[matches] = 1 - new_sel[matches]
            else:
                self.report({'ERROR'}, f"Unknown action: {action}")
                return {'CANCELLED'}

            # Apply changes and fully deselect if needed
            for i, f in enumerate(bm.faces):
                if new_sel[i] != sel_flags[i]:  # Only update changed faces
                    f.select = bool(new_sel[i])
                    if not f.select:  # Fully deselect: clear edges and verts
                        for e in f.edges:
                            e.select = False
                        for v in f.verts:
                            v.select = False

            bmesh.update_edit_mesh(mesh)
        else:
            # Object mode: unchanged, uses polygons directly
            vals = np.empty(face_count, dtype=np.int32)
            attr.data.foreach_get("value", vals)

            if mode == 'ANY':
                matches = (vals & mask) != 0
            elif mode == 'ALL':
                matches = (vals & mask) == mask
            elif mode == 'NONE':
                matches = (vals & mask) == 0
            else:
                self.report({'ERROR'}, f"Unknown mode: {mode}")
                return {'CANCELLED'}

            sel_flags = np.empty(face_count, dtype=np.int8)
            mesh.polygons.foreach_get("select", sel_flags)

            if action == 'SELECT':
                sel_flags[matches] = 1
            elif action == 'DESELECT':
                sel_flags[matches] = 0
            elif action == 'INVERT':
                sel_flags[matches] = 1 - sel_flags[matches]
            else:
                self.report({'ERROR'}, f"Unknown action: {action}")
                return {'CANCELLED'}

            mesh.polygons.foreach_set("select", sel_flags)
            mesh.update()

        matched_count = int(np.count_nonzero(matches))
        self.report({'INFO'}, f"{action.title()}ed {matched_count} faces (mask 0x{mask:04X}).")
        return {'FINISHED'}

class CARNIVORES_OT_play_linked_sound(bpy.types.Operator):
    """Plays the sound linked to the active object's active animation by adding it to the sequencer"""
    bl_idname = "carnivores.play_linked_sound"
    bl_label = "Play Linked Sound"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj:
            self.report({'ERROR'}, "No active object selected.")
            return {'CANCELLED'}

        if not obj.animation_data or not obj.animation_data.action:
            self.report({'ERROR'}, "Active object has no active animation action.")
            return {'CANCELLED'}

        action = obj.animation_data.action
        if 'carnivores_sound' not in action:
            self.report({'ERROR'}, f"Animation '{action.name}' has no linked sound (missing 'carnivores_sound' property).")
            return {'CANCELLED'}

        sound_name = action['carnivores_sound']
        linked_sound = bpy.data.sounds.get(sound_name)

        if not linked_sound:
            self.report({'ERROR'}, f"Linked sound '{sound_name}' not found in Blender data.")
            return {'CANCELLED'}

        # Ensure sequence editor exists
        if not context.scene.sequence_editor:
            context.scene.sequence_editor_create()

        # Add sound strip to sequencer
        # We'll place it on channel 1 and start it at the current frame
        # The name of the strip will be the sound's name
        try:
            print(f"DEBUG: Sound data block exists. Sound name: {linked_sound.name}")
            print(f"DEBUG: Attempting to play new sound '{linked_sound.name}' for {obj.name}.")

            # Check if a strip with the same name already exists to avoid duplicates
            existing_strip = context.scene.sequence_editor.sequences.get(linked_sound.name)
            if existing_strip:
                self.report({'INFO'}, f"Sound '{linked_sound.name}' already in sequencer. Skipping addition.")
                return {'FINISHED'}

            # Create a new sound strip and link the existing sound data block
            sound_strip = context.scene.sequence_editor.sequences.new(
                name=linked_sound.name,
                type='SOUND',
                channel=1,
                frame_start=context.scene.frame_current
            )
            sound_strip.sound = linked_sound  # Link the actual sound datablock

            self.report({'INFO'}, f"Added sound '{linked_sound.name}' to sequencer at frame {context.scene.frame_current}.")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to add sound to sequencer: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

# Global dictionary to track playing sounds for each object
_playing_sounds = {}
_temp_sound_files = set()
_aud_device = None # Global aud device
_is_real_playback = False # Our reliable flag for actual playback state

def get_aud_device():
    global _aud_device
    if _aud_device is None:
        _aud_device = aud.Device()
    return _aud_device

def playback_started_handler(scene):
    """This handler is called by Blender right before animation playback starts."""
    global _is_real_playback
    _is_real_playback = True
    print("DEBUG: Playback STARTED. _is_real_playback = True")

def playback_stopped_handler(scene):
    """This handler is called by Blender right after animation playback stops."""
    global _is_real_playback, _playing_sounds
    _is_real_playback = False
    print("DEBUG: Playback STOPPED. _is_real_playback = False")
    
    # If there are any lingering sounds, stop them now.
    if _playing_sounds:
        print("DEBUG: Playback stopped, stopping all managed sounds.")
        for handle, _ in _playing_sounds.values():
            handle.stop()
        _playing_sounds.clear()

def carnivores_nla_sound_handler(scene):
    global _playing_sounds, _is_real_playback
    
    # This handler should ONLY run when our flag indicates real playback is happening.
    if not _is_real_playback:
        return

    if not scene.carnivores_nla_sound_enabled:
        return

    device = get_aud_device()
    objects_with_active_sounds = {} # {obj: linked_sound_name}

    for obj in scene.objects:
        anim_data_container = None
        if obj.animation_data:
            anim_data_container = obj.animation_data
        elif obj.data and obj.data.shape_keys and obj.data.shape_keys.animation_data:
            anim_data_container = obj.data.shape_keys.animation_data
        
        if anim_data_container and anim_data_container.nla_tracks:
            current_action = None
            if scene.is_nla_tweakmode:
                active_action = anim_data_container.action if anim_data_container else None
                if active_action:
                    for track in anim_data_container.nla_tracks:
                        for strip in track.strips:
                            if strip.action == active_action:
                                if strip.frame_start <= scene.frame_current < strip.frame_end:
                                    current_action = active_action
                                break
                        if current_action:
                            break

            # Use the pointer property first, fallback to legacy string property if needed (optional)
            if current_action:
                sound_name = None
                if getattr(current_action, 'carnivores_sound_ptr', None):
                     sound_name = current_action.carnivores_sound_ptr.name
                elif 'carnivores_sound' in current_action:
                     sound_name = current_action['carnivores_sound']
                
                if sound_name:
                    objects_with_active_sounds[obj] = sound_name

    # Stop sounds that should no longer be playing
    for obj_playing in list(_playing_sounds.keys()):
        current_handle, current_sound_name = _playing_sounds[obj_playing]
        if obj_playing not in objects_with_active_sounds or objects_with_active_sounds[obj_playing] != current_sound_name:
            current_handle.stop()
            del _playing_sounds[obj_playing]

    # Start new sounds
    for obj_active, linked_sound_name in objects_with_active_sounds.items():
        if obj_active in _playing_sounds:
            continue

        linked_sound_data_block = bpy.data.sounds.get(linked_sound_name)
        if not linked_sound_data_block:
            continue

        try:
            sound_factory = linked_sound_data_block.factory
            
            # Fallback: If Blender failed to create a factory (common with some external files),
            # try loading it directly via aud using the absolute path.
            if not sound_factory:
                abs_path = bpy.path.abspath(linked_sound_data_block.filepath)
                if os.path.exists(abs_path):
                    try:
                        sound_factory = aud.Sound.file(abs_path)
                        print(f"DEBUG: Loaded sound factory from file fallback: {abs_path}")
                    except Exception as e:
                        print(f"NLA Sound Warning: Fallback load failed for '{linked_sound_name}': {e}")

            if sound_factory:
                # Play the sound without looping (looping handled by re-triggering or future features)
                handle = device.play(sound_factory)
                _playing_sounds[obj_active] = (handle, linked_sound_name)
            else:
                # Factory creation failed (broken file or invalid path)
                print(f"NLA Sound Warning: Could not load audio factory for '{linked_sound_name}'")

        except Exception as e:
            print(f"NLA Sound Error: Could not play sound '{linked_sound_name}' for {obj_active.name}: {e}")
            if obj_active in _playing_sounds:
                del _playing_sounds[obj_active]

class CARNIVORES_OT_import_sound_for_action(bpy.types.Operator, bpy_extras.io_utils.ImportHelper):
    """Import a sound file and link it to the specified Action"""
    bl_idname = "carnivores.import_sound_for_action"
    bl_label = "Import Sound"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: bpy.props.StringProperty(
        default="*.wav;*.mp3;*.ogg;*.flac",
        options={'HIDDEN'},
    )

    action_name: bpy.props.StringProperty(name="Action Name")

    def execute(self, context):
        if not self.action_name:
            self.report({'ERROR'}, "No action specified.")
            return {'CANCELLED'}
        
        action = bpy.data.actions.get(self.action_name)
        if not action:
            self.report({'ERROR'}, f"Action '{self.action_name}' not found.")
            return {'CANCELLED'}

        filepath = self.filepath
        if not os.path.isfile(filepath):
            self.report({'ERROR'}, "File not found.")
            return {'CANCELLED'}

        try:
            sound = bpy.data.sounds.load(filepath)
            # sound.pack() # Disabled packing to ensure immediate playback reliability
            action.carnivores_sound_ptr = sound
            self.report({'INFO'}, f"Imported '{sound.name}' and linked to '{action.name}'")
        except Exception as e:
            self.report({'ERROR'}, f"Failed to load sound: {e}")
            return {'CANCELLED'}

        return {'FINISHED'}

class CARNIVORES_OT_toggle_nla_sound_playback(bpy.types.Operator):
    bl_idname = "carnivores.toggle_nla_sound_playback"
    bl_label = "Toggle NLA Sound Playback"
    bl_description = "Toggles automatic sound playback based on active NLA strips"
    bl_options = {'REGISTER'}

    def execute(self, context):
        scene = context.scene
        scene.carnivores_nla_sound_enabled = not scene.carnivores_nla_sound_enabled

        if scene.carnivores_nla_sound_enabled:
            self.report({'INFO'}, "NLA Sound Playback Enabled.")
        else:
            # Stop all aud handles when disabling
            global _playing_sounds
            for obj_id, (handle, sound_name) in list(_playing_sounds.items()): # Use list() to iterate over a copy
                handle.stop()
                del _playing_sounds[obj_id]
            print("DEBUG: All playing sounds stopped and cleared.")
            self.report({'INFO'}, "NLA Sound Playback Disabled.")

        return {'FINISHED'}

class VIEW3D_PT_carnivores_selection(bpy.types.Panel):
    bl_label = "Selection Tools"
    bl_idname = "VIEW3D_PT_carnivores_selection"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Carnivores'

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        box = layout.box()
        box.label(text="Select Faces by 3DF Flags", icon='RESTRICT_SELECT_OFF')

        # Collapsible flag selection
        col = box.column(align=True)
        col.prop(scene, "cf_flag_section", text="Flag Selection", icon='TRIA_DOWN' if scene.cf_flag_section else 'TRIA_RIGHT')
        if scene.cf_flag_section:
            flag_col = col.column(align=True)
            for i, (bit, label, _) in enumerate(FACE_FLAG_OPTIONS):
                prop_name = f"cf_flag_{i}"
                if hasattr(scene, prop_name):
                    flag_col.prop(scene, prop_name, text=f"{label} (0x{bit:04X})",
                                  toggle=True,
                                  icon='CHECKBOX_HLT' if getattr(scene, prop_name) else 'CHECKBOX_DEHLT')
                else:
                    row = flag_col.row()
                    row.enabled = False
                    row.label(text=f"{label} (0x{bit:04X})")

            # Clear all flags button
            col.operator("carnivores.clear_flag_selections", text="Clear All Flags", icon='X')

        # Check if any flags are selected; show warning if not
        mask = sum(int(bit) for i, (bit, _, _) in enumerate(FACE_FLAG_OPTIONS) if getattr(scene, f"cf_flag_{i}", False))
        if mask == 0:
            box.label(text="No flags selected (mask=0)", icon='ERROR')

        # Mode and Action in a single row
        row = box.row(align=True)
        row.prop(scene, "cf_select_mode", text="", icon='FILTER')
        row.prop(scene, "cf_select_action", text="", icon='RESTRICT_SELECT_ON')
        row.operator("carnivores.select_by_flags", text="Apply", icon='CHECKMARK')

        # Improved notes as bullets
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Mode Explanations:", icon='INFO')
        col.label(text="- Has Any (OR): Matches if face has at least one selected flag")
        col.label(text="- Has All (AND): Matches if face has every selected flag")
        col.label(text="- Has None (NOT): Matches if face has no selected flags")
        col.label(text="Action: Apply Select/Deselect/Invert to matched faces")
        

def get_kps_mode(self):
    return 1 if "carnivores_kps" in self else 0

def set_kps_mode(self, value):
    if value == 1: # OVERRIDE
        if "carnivores_kps" not in self:
             self["carnivores_kps"] = int(bpy.context.scene.render.fps)
    else: # AUTO
        if "carnivores_kps" in self:
            del self["carnivores_kps"]

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


class CARNIVORES_UL_animation_list(bpy.types.UIList):
    """UIList for displaying NLA tracks in the Carnivores Animation Panel"""
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        track = item
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            row = layout.row(align=True)
            # Mute Toggle (Eye Icon logic reversed: Mute=True -> Eye Closed)
            icon = 'HIDE_OFF' if not track.mute else 'HIDE_ON'
            row.prop(track, "mute", text="", icon=icon, emboss=False)
            row.prop(track, "name", text="", emboss=False)
        elif self.layout_type == 'GRID':
            layout.alignment = 'CENTER'
            layout.label(text="", icon='NLA')

    def filter_items(self, context, data, propname):
        tracks = getattr(data, propname)
        if not tracks:
            return [], []
        
        # Default flags (all visible)
        flt_flags = [self.bitflag_filter_item] * len(tracks)
        
        # Reverse order: Visual index 0 -> Data index N-1
        flt_neworder = list(range(len(tracks) - 1, -1, -1))
        
        return flt_flags, flt_neworder

class CARNIVORES_OT_set_kps(bpy.types.Operator):
    """Set a custom Keys Per Second (KPS) override for this animation"""
    bl_idname = "carnivores.set_kps"
    bl_label = "Set KPS"
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty()
    default_value: bpy.props.IntProperty(default=30)

    def execute(self, context):
        action = bpy.data.actions.get(self.action_name)
        if action:
            action["carnivores_kps"] = self.default_value
            return {'FINISHED'}
        return {'CANCELLED'}

class CARNIVORES_OT_resync_animation(bpy.types.Operator):
    """Re-calculate keyframes for this animation based on current KPS and Scene FPS"""
    bl_idname = "carnivores.resync_animation"
    bl_label = "Re-Sync Timing"
    bl_options = {'REGISTER', 'UNDO'}

    action_name: bpy.props.StringProperty()

    def get_anim_data(self, obj):
        if obj.type == 'MESH' and obj.data and obj.data.shape_keys:
            return obj.data.shape_keys.animation_data
        return None

    def execute(self, context):
        obj = context.active_object
        action = bpy.data.actions.get(self.action_name)
        if not action:
            self.report({'ERROR'}, f"Action '{self.action_name}' not found.")
            return {'CANCELLED'}
        
        # Extract base name (remove _Action suffix)
        # This relies on the naming convention used by the importer
        if action.name.endswith("_Action"):
            anim_base_name = action.name[:-7]
        else:
            anim_base_name = action.name
        
        # Get KPS
        # Respect the Mode selector
        # With new get/set property, we can just check if key exists or trust the property
        if "carnivores_kps" in action:
            kps = action["carnivores_kps"]
        else:
            kps = context.scene.render.fps
        
        utils.keyframe_shape_key_animation_as_action(
            obj, 
            anim_base_name, 
            frame_start=1, 
            kps=kps, 
            scene_fps=context.scene.render.fps
        )
        
        strip_updated = self.update_nla_strip(obj, action)
        
        # If the action is in the NLA, clear it from the active slot to prevent
        # "Double Dipping" (Active + NLA = 200% intensity = Explosion)
        anim_data = self.get_anim_data(obj)
        if strip_updated and anim_data and anim_data.action == action:
            try:
                anim_data.action = None
            except AttributeError:
                self.report({'WARNING'}, f"Could not clear active action for '{action.name}' (likely NLA controlled).")
            
        self.report({'INFO'}, f"Resynced '{action.name}' at {kps} KPS.")
        return {'FINISHED'}
        
    def update_nla_strip(self, obj, action):
        # Find strip using this action and update its length
        anim_data = self.get_anim_data(obj)
        if anim_data and anim_data.nla_tracks:
            for track in anim_data.nla_tracks:
                for strip in track.strips:
                    if strip.action == action:
                        start, end = utils.get_action_frame_range(action)
                        # Reset internal action range
                        strip.action_frame_start = start
                        strip.action_frame_end = end
                        # Update strip duration on timeline
                        # Keep the start frame, adjust the end frame
                        strip.frame_end = strip.frame_start + (end - start)
                        # Ensure sync length is on
                        strip.use_sync_length = True
                        return True
        return False

class VIEW3D_PT_carnivores_animation(bpy.types.Panel):
    bl_label = "Carnivores Animation"
    bl_idname = "VIEW3D_PT_carnivores_animation"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Carnivores'

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        obj = context.active_object

        # --- Global Sound Settings ---
        row = layout.row()
        row.prop(scene, "carnivores_nla_sound_enabled", text="Enable NLA Sound", toggle=True)
        row.operator(CARNIVORES_OT_toggle_nla_sound_playback.bl_idname, text="", icon='PLAY_SOUND' if not scene.carnivores_nla_sound_enabled else 'PAUSE')
        layout.separator()

        if not obj:
            layout.label(text="Select an object", icon='INFO')
            return

        # Determine Animation Data Source
        anim_data = None
        source_label = ""
        if obj.data and getattr(obj.data, 'shape_keys', None) and obj.data.shape_keys.animation_data:
            anim_data = obj.data.shape_keys.animation_data
            source_label = "Shape Keys"
        elif obj.animation_data:
            anim_data = obj.animation_data
            source_label = "Object"

        if not anim_data:
            layout.label(text="No animation data found.", icon='INFO')
            if obj.type == 'MESH' and obj.data.shape_keys:
                 layout.operator("ops.shape_key_add_anim_data?", text="Init Anim Data") # Placeholder
            return

        layout.label(text=f"Source: {source_label}", icon='ANIM_DATA')

        # --- NLA Track List ---
        layout.label(text="NLA Tracks (Export Order):")
        row = layout.row()
        row.template_list(
            "CARNIVORES_UL_animation_list", "", 
            anim_data, "nla_tracks", 
            obj, "carnivores_active_nla_index", 
            rows=5
        )

        # --- Active Track Details ---
        idx = obj.carnivores_active_nla_index
        # Since the list is reversed, we need to handle the index carefully.
        # template_list with a custom order usually updates the index to match the VISUAL index.
        # But we need to access the DATA index.
        # The filter_items 'new_order' maps: VisualIndex -> DataIndex
        # So: data_index = new_order[visual_index]
        # BUT: template_list's active_index property logic is a bit opaque with reordering.
        # Usually, the index stored in the property matches the collection index (Data Index) unless
        # 'use_filter_sort_lock' is involved? No, checking docs/experience:
        # If reordering is active, the index property usually refers to the index in the FILTERED list (Visual Index).
        # Wait, actually: "The active_propname ... points to an integer property ... which holds the index of the active item in the collection."
        # If we reorder, does it point to the collection index or list index?
        # Generally it points to the Collection Index (Data Index).
        # Let's assume it points to the Data Index (Track 0, Track 1...).
        # BUT our visual list is reversed. If I click the top item (Visual 0), does it set index to (Data N-1)?
        # Yes, usually.
        
        # However, to display the *correct* details for the selected item, we just use the index stored in the property.
        if 0 <= idx < len(anim_data.nla_tracks):
            active_track = anim_data.nla_tracks[idx]
            
            if active_track and active_track.strips:
                # For simplicity, assume 1 strip per track for .car workflow, or take the first one
                strip = active_track.strips[0] 
                action = strip.action
                
                if action:
                    box = layout.box()
                    
                    # Header / Action Name
                    row = box.row(align=True)
                    row.prop(action, "name", text="", icon='ACTION')
                    
                    # Sound
                    row = box.row(align=True)
                    row.prop(action, "carnivores_sound_ptr", text="Sound")
                    op = row.operator("carnivores.import_sound_for_action", text="", icon='FILE_FOLDER')
                    op.action_name = action.name
                    
                    # KPS
                    row = box.row(align=True)
                    row.prop(action, "carnivores_kps_mode", text="") # Use the new EnumProperty

                    # Check existence directly for UI state
                    if "carnivores_kps" in action:
                        row.prop(action, '["carnivores_kps"]', text="KPS")
                    else: # AUTO mode
                        row.label(text=f"KPS: {scene.render.fps} (Scene FPS)")
                    
                    # Resync Button
                    row = box.row()
                    op = row.operator("carnivores.resync_animation", text="Re-Sync Timing", icon='FILE_REFRESH')
                    op.action_name = action.name
            elif active_track:
                layout.label(text="Empty Track (No Strips)", icon='INFO')

class CARNIVORES_OT_modify_3df_flag(bpy.types.Operator):                                                                                                                                                                                   
    bl_idname = 'carnivores.modify_3df_flag'
                                                                                                                                                                    
    bl_label = 'Modify 3DF Flag'
                                                                                                                                                                    
    bl_options = {'REGISTER', 'UNDO'}
    
    action: bpy.props.EnumProperty(
        name='Action',
        items=[
            ('SET', 'Set', 'Set the specified flag on selected faces'),
            ('CLEAR', 'Clear', 'Clear the specified flag on selected faces'),
            ('TOGGLE', 'Toggle', 'Toggle the specified flag on selected faces'),
            ('CLEAR_ALL', 'Clear All', 'Clear all flags on selected or all faces')
        ],
        default='SET'
    )
    flag_bit: bpy.props.IntProperty(name='Flag Bit', default=0)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, 'Please select a mesh object.')
            return {'CANCELLED'}
        
        mesh = obj.data
        attr = mesh.attributes.get('3df_flags')
        if not attr:
            self.report({'ERROR'}, "'3df_flags' attribute missing. Create it first.")
            return {'CANCELLED'}
        if attr.domain != 'FACE':
            self.report({'ERROR'}, "'3df_flags' attribute is not FACE-domain.")
            return {'CANCELLED'}
        
        face_count = len(mesh.polygons)
        if face_count == 0 and self.action != 'CLEAR_ALL':
            self.report({'INFO'}, 'Mesh has no faces to modify.')
            return {'CANCELLED'}
        
        prev_mode = obj.mode
        was_edit = prev_mode == 'EDIT'
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.update()
        
        try:
            if self.action == 'CLEAR_ALL':
                if was_edit:
                    bm = bmesh.from_edit_mesh(mesh)
                    bm.faces.ensure_lookup_table()
                    layer = bm.faces.layers.int.get('3df_flags')
                    if not layer:
                        self.report({'ERROR'}, "'3df_flags' layer missing in BMesh.")
                        return {'CANCELLED'}
                    changed = 0
                    for f in bm.faces:
                        if f.select or not was_edit:
                            f[layer] = 0
                            changed += 1
                    bmesh.update_edit_mesh(mesh)
                    self.report({'INFO'}, f"Cleared all flags on {changed} faces.")
                else:
                    vals = np.zeros(face_count, dtype=np.int32)
                    attr.data.foreach_set('value', vals)
                    mesh.update()
                    self.report({'INFO'}, f"Cleared all flags on {face_count} faces.")
                return {'FINISHED'}
            else:
                selected_indices = utils.get_selected_face_indices(obj)
                if selected_indices.size == 0:
                    self.report({'WARNING'}, 'No faces selected.')
                    return {'CANCELLED'}
                changed = utils.bulk_modify_flag(mesh, selected_indices, self.flag_bit, self.action.lower())
                mesh.update()
                action_name = {'SET': 'Set', 'CLEAR': 'Cleared', 'TOGGLE': 'Toggled'}[self.action]
                self.report({'INFO'}, f"{action_name} flag 0x{self.flag_bit:04X} on {changed} faces.")
                return {'FINISHED'}
        finally:
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')
                context.view_layer.update()

class CARNIVORES_OT_clear_flag_selections(bpy.types.Operator):
    """Clear all flag selections in the Selection Tools panel"""
    bl_idname = "carnivores.clear_flag_selections"
    bl_label = "Clear Flag Selections"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        for i, (bit, label, _) in enumerate(FACE_FLAG_OPTIONS):
            prop_name = f"cf_flag_{i}"
            if hasattr(scene, prop_name):
                setattr(scene, prop_name, False)
        self.report({'INFO'}, "Cleared all flag selections.")
        return {'FINISHED'}