import bpy
import bpy_extras.io_utils
import os
import mathutils
import numpy as np
from ..utils import io as io_utils
from ..utils import animation as anim_utils
from ..utils import common
from ..utils.logger import info, debug, warn, error
from ..parsers.parse_3df import parse_3df
from ..parsers.parse_car import parse_car
from ..parsers.export_3df import export_3df
from ..parsers.export_car import export_car

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
        
        filepaths = [os.path.join(self.directory, f.name) for f in self.files]
        valid_paths = [fp for fp in filepaths if os.path.isfile(fp)]
        if not valid_paths:
            self.report({'ERROR'}, "No valid .3df files selected.")
            return {'CANCELLED'}
        for filepath in valid_paths:

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
                io_utils.create_uv_map(obj.data, uvs)
                if self.import_textures and texture is not None:
                    image = io_utils.create_image_texture(texture, texture_height, object_name)
                    if self.create_materials:
                        material = io_utils.create_texture_material(image, object_name)
                        obj.data.materials.append(material)

                if self.bone_import_type == 'HOOKS':
                    vertex_groups_by_index = io_utils.create_vertex_groups_from_bones(obj, bone_names, vertices['owner'])
                    hook_objects = io_utils.create_hooks(bone_names, bonesTransformedPos, bones['parent'], object_name, obj, coll)
                    io_utils.assign_hook_modifiers(obj, hook_objects, vertex_groups_by_index)

                elif self.bone_import_type == 'ARMATURE':
                    io_utils.create_vertex_groups_from_bones(obj, bone_names, vertices['owner'])
                    armature_obj = io_utils.create_armature(bone_names, bonesTransformedPos, bones['parent'], object_name, coll)
                    io_utils.assign_armature_modifier(obj, armature_obj)

                if warnings:
                    bpy.ops.carnivores.modal_message('INVOKE_DEFAULT', message="\n".join(warnings))

            except Exception as e:
                self.report({'ERROR'}, f"Failed to import {os.path.basename(filepath)} at parsing step: {str(e)}")
                if 'coll' in locals() and coll in bpy.data.collections:
                    bpy.data.collections.remove(coll, do_unlink=True)
                continue
                
        if self.create_materials and self.import_textures:
            io_utils.setup_custom_world_shader()
            
        return {'FINISHED'}

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

    @common.timed('CARNIVORES_OT_import_car.execute', is_operator=True)
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
                mesh_name, _ = io_utils.generate_names(filepath)  # Ignore basename; use model_name below
                coll = io_utils.create_import_collection(os.path.splitext(os.path.basename(filepath))[0])
                header, model_name, faces, uvs, vertices, bone_names, texture, texture_height, warnings, animations, sounds, cross_ref = parse_car(
                    filepath,
                    validate=self.validate,
                    parse_texture=self.import_textures,
                    flip_handedness=self.flip_handedness,
                    import_sounds=self.import_sounds
                )
                
                debug(f"SOUNDS: {len(sounds)}")
                for s in sounds:
                    debug(f"  -> {s['name']} {s['data'].size} samples")
                debug(f"CROSS_REF (first 10): {cross_ref[:10]}")
                
                verticesTransformedPos = io_utils.apply_import_matrix(vertices['coord'], import_matrix_np)
                # Use bone_names from parser (already handles dummies/offset if needed)
                obj = io_utils.create_mesh_object(mesh_name, verticesTransformedPos, faces['v'], model_name, self.normal_smooth, faces['flags'])
                coll.objects.link(obj)
                io_utils.create_uv_map(obj.data, uvs)
                # Create shape keys
                if self.import_animations and animations:
                    anim_utils.create_shape_keys_from_car_animations(obj, animations, import_matrix_np)
                    # Automatically create fast actions + NLA strips
                    actions = []
                    try:
                        actions = anim_utils.auto_create_shape_key_actions_from_car(obj, frame_step=1, parsed_animations=animations)
                    except Exception as e:
                        self.report({'WARNING'}, f"Failed to auto-create animations: {e}")
                if self.import_sounds and sounds:
                    imported_sounds = anim_utils.import_car_sounds(self, sounds, model_name, context)
                    anim_utils.associate_sounds_with_animations(self, obj, animations, cross_ref, imported_sounds, actions)
                if self.import_textures and texture is not None:
                    image = io_utils.create_image_texture(texture, texture_height, model_name)
                    if self.create_materials:
                        material = io_utils.create_texture_material(image, model_name)
                        obj.data.materials.append(material)
                # Vertex groups from owners (dummy bones if needed)
                if len(bone_names) > 0:
                    io_utils.create_vertex_groups_from_bones(obj, bone_names, vertices['owner'])
                # No hooks/armature for .CAR (owners only; no positions/parents)
                if warnings:
                    bpy.ops.carnivores.modal_message('INVOKE_DEFAULT', message='\n'.join(warnings))
            except Exception as e:
                self.report({'ERROR'}, f"Failed to import {os.path.basename(filepath)} at parsing step: {str(e)}")
                if 'coll' in locals() and bpy.data.collections.get(coll.name) is not None:
                    continue
        if self.create_materials and self.import_textures:
            io_utils.setup_custom_world_shader()
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