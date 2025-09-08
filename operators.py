import bpy
import bpy_extras.io_utils
import os
import mathutils
import bmesh
import numpy as np

from .parsers.parse_3df import parse_3df
from .parsers.export_3df import export_3df
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
        
        import_matrix = (
            mathutils.Matrix.Scale(self.scale, 4) @
            mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) @
            bpy_extras.io_utils.axis_conversion(
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
                header, faces, uvs, vertices, bones, bone_names, texture, texture_height, warnings = parse_3df(filepath, self.validate, self.import_textures)
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
        layout.separator()
        box = layout.box()
        box.label(text="Axis Conversion")
        box.prop(self, "axis_forward")
        box.prop(self, "axis_up")

    @utils.timed("CARNIVORES_OT_export_3df.execute", is_operator=True)
    def execute(self, context):
        export_matrix = (
            bpy_extras.io_utils.axis_conversion(
                from_forward='Y',
                from_up='Z',
                to_forward=self.axis_forward,
                to_up=self.axis_up
            ).to_4x4() @ mathutils.Matrix.Scale(-1, 4, (1, 0, 0)) @ mathutils.Matrix.Scale(self.scale, 4)
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
                        flip_v=self.flip_v
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
                    flip_v=self.flip_v
                )
                exported_files.append(os.path.basename(filepath))
            except Exception as e:
                self.report({'ERROR'}, f"Failed to export {obj.name} to {os.path.basename(filepath)}: {e}")
        if exported_files:
            self.report({'INFO'}, f"Exported {len(exported_files)} file(s): {', '.join(exported_files)}")
        else:
            self.report({'ERROR'}, "No files were exported due to errors.")
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
            layout.label(text="Select a mesh object", icon='ERROR')
            return

        mesh = obj.data
        if "3df_flags" not in mesh.attributes:
            layout.label(text="No '3df_flags' attribute on mesh", icon='INFO')
            row = layout.row(align=True)
            row.operator("carnivores.create_3df_flags", icon='ADD', text="Create '3df_flags'")
            layout.label(text="Creates a face-domain INT attribute set to 0")
            return

        counts, total = utils.count_flag_hits(obj)
        if total == 0:
            layout.label(text="Mesh has no faces", icon='INFO')
            return

        # Header to clarify mode
        mode_text = "All faces" if obj.mode != 'EDIT' else "Selected faces"
        layout.label(text=f"Face Flags ({mode_text}: {total})", icon='FACESEL')

        # Single-column list layout
        col = layout.column(align=True)
        label_fraction = 0.65
        for bit, label in FACE_FLAG_OPTIONS:
            count = counts.get(bit, 0)
            icon = 'CHECKBOX_HLT' if count > 0 else 'CHECKBOX_DEHLT'
            # Highlight non-zero counts with bold text
            text = f"{label} ({count}/{total})" if count == 0 else f"{label} ({count}/{total})"
            split = col.split(factor=label_fraction)
            left = split.column()
            right = split.column()
            left_row = left.row(align=True)
            left_row.label(text=text, icon=icon)
            btn_row = right.row(align=True)
            btn_row.operator("carnivores.set_3df_flag", text="", icon='CHECKMARK').flag_bit = bit
            btn_row.operator("carnivores.clear_3df_flag", text="", icon='X').flag_bit = bit
            btn_row.operator("carnivores.toggle_3df_flag", text="", icon='ARROW_LEFTRIGHT').flag_bit = bit

        # Clear all flags button at the bottom
        layout.separator()
        layout.operator("carnivores.clear_all_3df_flags", text="Clear All Flags", icon='X')
            
class CARNIVORES_OT_set_3df_flag(bpy.types.Operator):
    """Set a bit flag on selected faces (vectorized, fast)"""
    bl_idname = "carnivores.set_3df_flag"
    bl_label = "Set 3DF Flag"
    bl_options = {'REGISTER', 'UNDO'}

    flag_bit: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a mesh object.")
            return {'CANCELLED'}

        mesh = obj.data
        attr = mesh.attributes.get("3df_flags")
        if not attr:
            self.report({'ERROR'}, "'3df_flags' attribute missing. Create it first.")
            return {'CANCELLED'}
        if attr.domain != 'FACE':
            self.report({'ERROR'}, "'3df_flags' attribute is not FACE-domain.")
            return {'CANCELLED'}

        # Store current mode and switch to OBJECT mode for attribute access
        prev_mode = obj.mode
        was_edit = (prev_mode == 'EDIT')
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.update()

        try:
            # Get selected face indices
            selected_indices = utils.get_selected_face_indices(obj)
            if selected_indices.size == 0:
                self.report({'WARNING'}, "No faces selected.")
                return {'CANCELLED'}

            # Set the flag bit using bulk_modify_flag
            changed = utils.bulk_modify_flag(mesh, selected_indices, self.flag_bit, 'set')
            mesh.update()

            self.report({'INFO'}, f"Set flag 0x{self.flag_bit:04X} on {changed} faces.")
            return {'FINISHED'}

        finally:
            # Restore original mode
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')
                context.view_layer.update()
        
class CARNIVORES_OT_clear_3df_flag(bpy.types.Operator):
    """Clear a bit flag on selected faces (vectorized, fast)"""
    bl_idname = "carnivores.clear_3df_flag"
    bl_label = "Clear 3DF Flag"
    bl_options = {'REGISTER', 'UNDO'}

    flag_bit: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a mesh object.")
            return {'CANCELLED'}

        mesh = obj.data
        attr = mesh.attributes.get("3df_flags")
        if not attr:
            self.report({'ERROR'}, "'3df_flags' attribute missing. Create it first.")
            return {'CANCELLED'}
        if attr.domain != 'FACE':
            self.report({'ERROR'}, "'3df_flags' attribute is not FACE-domain.")
            return {'CANCELLED'}

        # Store current mode and switch to OBJECT mode for attribute access
        prev_mode = obj.mode
        was_edit = (prev_mode == 'EDIT')
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.update()

        try:
            # Get selected face indices
            selected_indices = utils.get_selected_face_indices(obj)
            if selected_indices.size == 0:
                self.report({'WARNING'}, "No faces selected.")
                return {'CANCELLED'}

            # Clear the flag bit using bulk_modify_flag
            changed = utils.bulk_modify_flag(mesh, selected_indices, self.flag_bit, 'clear')
            mesh.update()

            self.report({'INFO'}, f"Cleared flag 0x{self.flag_bit:04X} on {changed} faces.")
            return {'FINISHED'}

        finally:
            # Restore original mode
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')
                context.view_layer.update()
        
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
        for i, (bit, label) in enumerate(FACE_FLAG_OPTIONS):
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
            for i, (bit, label) in enumerate(FACE_FLAG_OPTIONS):
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
        mask = sum(int(bit) for i, (bit, _) in enumerate(FACE_FLAG_OPTIONS) if getattr(scene, f"cf_flag_{i}", False))
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
        
class CARNIVORES_OT_toggle_3df_flag(bpy.types.Operator):
    """Toggle a bit flag on selected faces (vectorized, fast)"""
    bl_idname = "carnivores.toggle_3df_flag"
    bl_label = "Toggle 3DF Flag"
    bl_options = {'REGISTER', 'UNDO'}

    flag_bit: bpy.props.IntProperty()

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a mesh object.")
            return {'CANCELLED'}

        mesh = obj.data
        attr = mesh.attributes.get("3df_flags")
        if not attr:
            self.report({'ERROR'}, "'3df_flags' attribute missing. Create it first.")
            return {'CANCELLED'}
        if attr.domain != 'FACE':
            self.report({'ERROR'}, "'3df_flags' attribute is not FACE-domain.")
            return {'CANCELLED'}

        prev_mode = obj.mode
        was_edit = (prev_mode == 'EDIT')
        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.update()

        try:
            selected_indices = utils.get_selected_face_indices(obj)
            if selected_indices.size == 0:
                self.report({'WARNING'}, "No faces selected.")
                return {'CANCELLED'}

            changed = utils.bulk_modify_flag(mesh, selected_indices, self.flag_bit, 'toggle')
            mesh.update()

            self.report({'INFO'}, f"Toggled flag 0x{self.flag_bit:04X} on {changed} faces.")
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
        for i, (bit, label) in enumerate(FACE_FLAG_OPTIONS):
            prop_name = f"cf_flag_{i}"
            if hasattr(scene, prop_name):
                setattr(scene, prop_name, False)
        self.report({'INFO'}, "Cleared all flag selections.")
        return {'FINISHED'}

class CARNIVORES_OT_clear_all_3df_flags(bpy.types.Operator):
    """Clear all 3DF flags on all faces of the active mesh"""
    bl_idname = "carnivores.clear_all_3df_flags"
    bl_label = "Clear All 3DF Flags"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Please select a mesh object.")
            return {'CANCELLED'}

        mesh = obj.data
        attr = mesh.attributes.get("3df_flags")
        if not attr:
            self.report({'ERROR'}, "'3df_flags' attribute missing. Create it first.")
            return {'CANCELLED'}
        if attr.domain != 'FACE':
            self.report({'ERROR'}, "'3df_flags' attribute is not FACE-domain.")
            return {'CANCELLED'}

        face_count = len(mesh.polygons)
        if face_count == 0:
            self.report({'INFO'}, "Mesh has no faces to clear.")
            return {'FINISHED'}

        was_edit = (obj.mode == 'EDIT')
        if was_edit:
            # Use BMesh in Edit mode for safe attribute modification
            bm = bmesh.from_edit_mesh(mesh)
            bm.faces.ensure_lookup_table()
            layer = bm.faces.layers.int.get("3df_flags")
            if not layer:
                self.report({'ERROR'}, "'3df_flags' layer missing in BMesh.")
                return {'CANCELLED'}

            # Set all face flags to 0
            for f in bm.faces:
                f[layer] = 0

            bmesh.update_edit_mesh(mesh)
            self.report({'INFO'}, f"Cleared all flags on {face_count} faces.")
            return {'FINISHED'}
        else:
            # Object mode: use vectorized approach
            vals = np.zeros(face_count, dtype=np.int32)
            attr.data.foreach_set("value", vals)
            mesh.update()
            self.report({'INFO'}, f"Cleared all flags on {face_count} faces.")
            return {'FINISHED'}