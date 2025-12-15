import bpy
import bmesh
import numpy as np
from ..utils import flags as flag_utils
from ..core.constants import FACE_FLAG_OPTIONS

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
        counts, total = flag_utils.count_flag_hits(obj)
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
                selected_indices = flag_utils.get_selected_face_indices(obj)
                if selected_indices.size == 0:
                    self.report({'WARNING'}, 'No faces selected.')
                    return {'CANCELLED'}
                changed = flag_utils.bulk_modify_flag(mesh, selected_indices, self.flag_bit, self.action.lower())
                mesh.update()
                action_name = {'SET': 'Set', 'CLEAR': 'Cleared', 'TOGGLE': 'Toggled'}[self.action]
                self.report({'INFO'}, f"{action_name} flag 0x{self.flag_bit:04X} on {changed} faces.")
                return {'FINISHED'}
        finally:
            if was_edit:
                bpy.ops.object.mode_set(mode='EDIT')
                context.view_layer.update()

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
