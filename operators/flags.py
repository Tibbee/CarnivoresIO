import bpy
import bmesh
import numpy as np
from ..utils import flags as flag_utils
from ..core.constants import FACE_FLAG_OPTIONS


# Runtime-only viewport state.  Nothing here is serialized into the model.
_VIEWPORT_FLAG_RESTORE = {}


def _poll_message(cls, message):
    """Set a Blender operator poll explanation when available."""
    try:
        cls.poll_message_set(message)
    except (AttributeError, TypeError, RuntimeError):
        pass
    return False


def _active_mesh(context):
    obj = getattr(context, "active_object", None)
    return obj if obj and obj.type == 'MESH' else None


class CARNIVORES_OT_create_3df_flags(bpy.types.Operator):
    """Create a face-domain integer attribute named '3df_flags' (initialized to 0)"""
    bl_idname = "carnivores.create_3df_flags"
    bl_label = "Create 3df_flags Attribute"
    bl_description = "Create the face-domain INT attribute used to store serialized C2 surface flags, initialized to zero."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        if not obj:
            return _poll_message(cls, "Select a mesh object before creating face flags.")
        attr = obj.data.attributes.get("3df_flags")
        if attr is not None:
            return _poll_message(cls, "The active mesh already has a '3df_flags' attribute.")
        return True

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

def _set_active_flag_colors(mesh):
    """Make the generated color attribute the active viewport color source."""
    colors = getattr(mesh, "color_attributes", None)
    if colors is None:
        return False
    index = colors.find("FlagColors")
    if index < 0:
        return False
    colors.active_color_index = index
    return True


def _show_flag_colors(context, mesh):
    """Configure only the invoking 3D View for vertex-color display."""
    previous_color_index = getattr(mesh.color_attributes, "active_color_index", 0)
    _set_active_flag_colors(mesh)
    area = getattr(context, "area", None)
    region = next((item for item in area.regions if item.type == 'WINDOW'), None) if area and area.type == 'VIEW_3D' else None
    if region is None:
        return False, "FlagColors was refreshed, but the invoking area is not a compatible 3D View. Use Solid shading with Vertex colors to see it."

    key = area.as_pointer()
    shading = area.spaces.active.shading
    if key not in _VIEWPORT_FLAG_RESTORE:
        _VIEWPORT_FLAG_RESTORE[key] = {
            "color_type": shading.color_type,
            "mesh": mesh,
            "active_color_index": previous_color_index,
        }
    try:
        enum_ids = {item.identifier for item in shading.bl_rna.properties["color_type"].enum_items}
        if "VERTEX" not in enum_ids:
            return False, "FlagColors was refreshed, but this Blender version has no vertex-color viewport mode."
        shading.color_type = "VERTEX"
        return True, ""
    except (AttributeError, RuntimeError, TypeError) as exc:
        return False, f"FlagColors was refreshed, but viewport display could not be configured: {exc}"


def _hide_flag_colors(context, mesh):
    """Restore the invoking view and active color attribute when possible."""
    area = getattr(context, "area", None)
    key = area.as_pointer() if area and area.type == 'VIEW_3D' else None
    state = _VIEWPORT_FLAG_RESTORE.pop(key, None) if key is not None else None
    if state is None:
        return False, "No Carnivores viewport display state was recorded; the FlagColors attribute remains available."
    try:
        area.spaces.active.shading.color_type = state["color_type"]
        old_mesh = state.get("mesh")
        if old_mesh and old_mesh.name in bpy.data.meshes and hasattr(old_mesh, "color_attributes"):
            old_mesh.color_attributes.active_color_index = min(
                state.get("active_color_index", 0),
                max(0, len(old_mesh.color_attributes) - 1),
            )
        return True, ""
    except (ReferenceError, AttributeError, RuntimeError, TypeError) as exc:
        return False, f"Viewport display was updated, but the previous state could not be fully restored: {exc}"


def _remove_flag_colors(context, mesh):
    _hide_flag_colors(context, mesh)
    attr = mesh.attributes.get("FlagColors")
    if attr is None:
        return False, "No generated FlagColors attribute exists."
    if attr.domain != 'CORNER' or attr.data_type not in {'BYTE_COLOR', 'FLOAT_COLOR'}:
        return False, "FlagColors exists but is not a generated corner color attribute; it was left unchanged."
    try:
        mesh.attributes.remove(attr)
        mesh.update()
        return True, ""
    except (RuntimeError, ReferenceError) as exc:
        return False, f"Could not remove FlagColors: {exc}"


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
        
        visualization = layout.box()
        visualization.label(text="Visualization", icon="COLOR")
        row = visualization.row(align=True)
        operator = row.operator("carnivores.visualize_flags", text="Show", icon="HIDE_OFF")
        operator.display_action = 'SHOW'
        operator = row.operator("carnivores.visualize_flags", text="Refresh", icon="FILE_REFRESH")
        operator.display_action = 'REFRESH'
        operator = row.operator("carnivores.visualize_flags", text="Hide", icon="HIDE_ON")
        operator.display_action = 'HIDE'
        row = visualization.row(align=True)
        operator = row.operator("carnivores.visualize_flags", text="Remove Colors", icon="TRASH")
        operator.display_action = 'REMOVE'
        visualization.label(text="Generated FlagColors never replaces serialized 3df_flags.")
        visualization.label(text="Overlapping flags blend in listed flag order for deterministic colors.")
        legend = visualization.column(align=True)
        legend.label(text="Legend (viewport colors):")
        tint_by_bit = dict(flag_utils.FLAG_TINTS)
        for bit, label, _ in FACE_FLAG_OPTIONS:
            tint = tint_by_bit.get(bit, (1.0, 1.0, 1.0, 1.0))
            rgb = ", ".join(str(int(round(channel * 255.0))) for channel in tint[:3])
            legend.label(text=f"{label} 0x{bit:04X}: RGB {rgb}")
        layout.separator()
        
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
            if count == 0:
                state = "None"
            elif count == total:
                state = "All"
            else:
                state = "Mixed"
            text = f"{label}: {state} ({count}/{total})"
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
        clear_row = layout.row(align=True)
        clear_row.operator('carnivores.clear_selected_3df_flags', text='Clear Selected Faces', icon='X')
        clear_row.operator('carnivores.clear_all_3df_flags', text='Clear All Faces', icon='X')

class CARNIVORES_OT_visualize_flags(bpy.types.Operator):
    """Generate and manage the non-serialized FlagColors viewport aid."""
    bl_idname = "carnivores.visualize_flags"
    bl_label = "Visualize Flags"
    bl_description = "Show, refresh, hide, or remove the generated FlagColors viewport aid without changing serialized 3df_flags."
    bl_options = {'REGISTER', 'UNDO'}

    display_action: bpy.props.EnumProperty(
        name="Display Action",
        description="Choose how to manage the generated face-flag color visualization.",
        items=[
            ('SHOW', "Show", "Refresh FlagColors and use it as the active vertex-color source in the invoking 3D View."),
            ('REFRESH', "Refresh", "Rebuild FlagColors from the serialized face flags without changing viewport settings."),
            ('HIDE', "Hide", "Restore the invoking 3D View's previous color display setting."),
            ('REMOVE', "Remove", "Remove the generated FlagColors attribute; serialized 3df_flags are preserved."),
        ],
        default='SHOW',
    )

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        if not obj:
            return _poll_message(cls, "Select a mesh with 3DF flags to visualize them.")
        attr = obj.data.attributes.get("3df_flags")
        if not attr or attr.domain != 'FACE' or attr.data_type != 'INT':
            return _poll_message(cls, "The active mesh needs a valid FACE-domain INT '3df_flags' attribute.")
        return True

    def invoke(self, context, event):
        if self.display_action == 'REMOVE':
            return context.window_manager.invoke_confirm(self, event)
        return self.execute(context)

    def execute(self, context):
        obj = context.active_object
        if not obj or obj.type != 'MESH':
            self.report({'ERROR'}, "Active object must be a mesh.")
            return {'CANCELLED'}

        if self.display_action == 'REMOVE':
            success, message = _remove_flag_colors(context, obj.data)
            if success:
                self.report({'INFO'}, "Removed generated 'FlagColors'; serialized 3df_flags were preserved.")
                return {'FINISHED'}
            self.report({'WARNING'}, message)
            return {'CANCELLED'}

        if self.display_action in {'SHOW', 'REFRESH'}:
            try:
                flag_utils.update_flag_colors(obj.data)
            except (RuntimeError, ReferenceError) as exc:
                self.report({'ERROR'}, f"Could not update 'FlagColors': {exc}")
                return {'CANCELLED'}

        if self.display_action == 'SHOW':
            shown, message = _show_flag_colors(context, obj.data)
            if shown:
                self.report({'INFO'}, "Showing 'FlagColors' in the invoking 3D View. Use Hide to restore its previous color display.")
            else:
                self.report({'INFO'}, message)
        elif self.display_action == 'HIDE':
            restored, message = _hide_flag_colors(context, obj.data)
            self.report({'INFO' if restored else 'WARNING'}, "Flag visualization hidden." if restored else message)
        else:
            self.report({'INFO'}, "Refreshed 'FlagColors'; serialized 3df_flags were preserved.")
        return {'FINISHED'}


def _clear_flag_values(obj, selected_only=False):
    mesh = obj.data
    attr = mesh.attributes.get('3df_flags')
    if not attr or attr.domain != 'FACE' or attr.data_type != 'INT' or len(attr.data) != len(mesh.polygons):
        raise RuntimeError("Mesh needs a valid FACE-domain INT '3df_flags' attribute.")

    selected_indices = None
    if selected_only:
        selected_indices = flag_utils.get_selected_face_indices(obj)
        if selected_indices.size == 0:
            return 0, 0

    previous_mode = obj.mode
    was_edit = previous_mode == 'EDIT'
    if was_edit:
        bpy.ops.object.mode_set(mode='OBJECT')
        context_view_layer = bpy.context.view_layer
        context_view_layer.update()
        attr = mesh.attributes.get('3df_flags')

    try:
        values = np.empty(len(mesh.polygons), dtype=np.int32)
        attr.data.foreach_get('value', values)
        before = values.copy() if selected_indices is None else values[selected_indices].copy()
        if selected_indices is None:
            values[:] = 0
        else:
            values[selected_indices] = 0
        attr.data.foreach_set('value', values)
        mesh.update()
        changed = int(np.count_nonzero(before))
        return changed, int(len(mesh.polygons) if selected_indices is None else selected_indices.size)
    finally:
        if was_edit:
            bpy.ops.object.mode_set(mode='EDIT')
            bpy.context.view_layer.update()


class CARNIVORES_OT_clear_selected_3df_flags(bpy.types.Operator):
    """Clear serialized flags only on currently selected faces."""
    bl_idname = "carnivores.clear_selected_3df_flags"
    bl_label = "Clear Flags on Selected Faces"
    bl_description = "Clear every serialized 3DF flag on selected faces only; Object Mode uses selected mesh faces."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        if not obj:
            return _poll_message(cls, "Select a mesh with 3DF flags to clear selected faces.")
        attr = obj.data.attributes.get('3df_flags')
        if not attr or attr.domain != 'FACE' or attr.data_type != 'INT':
            return _poll_message(cls, "The active mesh needs a valid FACE-domain INT '3df_flags' attribute.")
        return True

    def execute(self, context):
        try:
            changed, affected = _clear_flag_values(context.active_object, selected_only=True)
            if affected == 0:
                self.report({'INFO'}, "No faces are selected; no flags were changed.")
                return {'CANCELLED'}
            flag_utils.update_flag_colors(context.active_object.data)
            self.report({'INFO'}, f"Cleared flags on {changed} selected face(s).")
            return {'FINISHED'}
        except (RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CARNIVORES_OT_clear_all_3df_flags(bpy.types.Operator):
    """Clear serialized flags on every face after explicit confirmation."""
    bl_idname = "carnivores.clear_all_3df_flags"
    bl_label = "Clear Flags on All Faces"
    bl_description = "Clear every serialized 3DF flag on every face; this is destructive to authored flag values and asks for confirmation."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        if not obj:
            return _poll_message(cls, "Select a mesh with 3DF flags to clear all faces.")
        attr = obj.data.attributes.get('3df_flags')
        if not attr or attr.domain != 'FACE' or attr.data_type != 'INT':
            return _poll_message(cls, "The active mesh needs a valid FACE-domain INT '3df_flags' attribute.")
        return True

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            changed, affected = _clear_flag_values(context.active_object, selected_only=False)
            flag_utils.update_flag_colors(context.active_object.data)
            self.report({'INFO'}, f"Cleared flags on {affected} face(s); {changed} face(s) changed.")
            return {'FINISHED'}
        except (RuntimeError, ReferenceError) as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}


class CARNIVORES_OT_select_by_flags(bpy.types.Operator):
    """Select/Deselect/Invert faces on the active mesh by 3DF flag mask"""
    bl_idname = "carnivores.select_by_flags"
    bl_label = "Select Faces by 3DF Flags"
    bl_description = "Find faces by the selected C2 surface flags, then select, deselect, or invert the matching faces."
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        if not obj:
            return _poll_message(cls, "Select a mesh with 3DF flags to select faces.")
        attr = obj.data.attributes.get("3df_flags")
        if not attr or attr.domain != 'FACE' or attr.data_type != 'INT':
            return _poll_message(cls, "The active mesh needs a valid FACE-domain INT '3df_flags' attribute.")
        scene = getattr(context, "scene", None)
        if scene is not None:
            has_selected_flags = any(
                getattr(scene, f"cf_flag_{i}", False)
                for i, _ in enumerate(FACE_FLAG_OPTIONS)
            )
            if not has_selected_flags:
                return _poll_message(cls, "Select at least one face flag in the Selection Tools panel.")
        return True

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
    bl_description = 'Set, clear, or toggle the C2 surface flag shown on this row; Clear All uses selected Edit Mode faces or the whole mesh in Object Mode.'
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _active_mesh(context)
        if not obj:
            return _poll_message(cls, "Select a mesh with 3DF flags to modify them.")
        attr = obj.data.attributes.get("3df_flags")
        if not attr or attr.domain != 'FACE' or attr.data_type != 'INT':
            return _poll_message(cls, "The active mesh needs a valid FACE-domain INT '3df_flags' attribute.")
        return True
    
    action: bpy.props.EnumProperty(
        name='Action',
        items=[
            ('SET', 'Set', 'Set this C2 surface flag on the selected faces.'),
            ('CLEAR', 'Clear', 'Clear this C2 surface flag from the selected faces.'),
            ('TOGGLE', 'Toggle', 'Invert this C2 surface flag on the selected faces.'),
            ('CLEAR_ALL', 'Clear All', 'Clear every C2 surface flag on selected Edit Mode faces or the whole mesh in Object Mode.'),
        ],
        description='Operation to perform on the C2 face flag.',
        default='SET'
    )
    flag_bit: bpy.props.IntProperty(
        name='C2 Flag Bit',
        description='Serialized C2 surface-flag bit. The Face Flags panel assigns this automatically.',
        default=0,
    )

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

        # Face-domain attribute data is synchronized to the Mesh in Object
        # Mode. For Edit Mode, preserve the selected-face scope while using the
        # same reliable Mesh attribute path as the other flag operations.
        if self.action == 'CLEAR_ALL' and was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.update()
            try:
                # Refresh the RNA attribute wrapper after leaving Edit Mode;
                # the pre-edit wrapper may still report zero data elements.
                attr = mesh.attributes.get('3df_flags')
                selected_indices = flag_utils.get_selected_face_indices(obj)
                vals = np.empty(face_count, dtype=np.int32)
                attr.data.foreach_get('value', vals)
                before_selected = vals[selected_indices].copy()
                vals[selected_indices] = 0
                attr.data.foreach_set('value', vals)
                mesh.update()
                flag_utils.update_flag_colors(mesh)
                changed = int(np.count_nonzero(before_selected != 0))
                self.report({'INFO'}, f"Cleared flags on {changed} selected faces.")
                return {'FINISHED'}
            finally:
                bpy.ops.object.mode_set(mode='EDIT')
                context.view_layer.update()

        if was_edit:
            bpy.ops.object.mode_set(mode='OBJECT')
            context.view_layer.update()
            # Refresh the RNA attribute wrapper after leaving Edit Mode.
            attr = mesh.attributes.get('3df_flags')

        try:
            if self.action == 'CLEAR_ALL':
                vals = np.zeros(face_count, dtype=np.int32)
                attr.data.foreach_set('value', vals)
                mesh.update()
                self.report({'INFO'}, f"Cleared all flags on {face_count} faces.")

                # Auto-Update Colors
                flag_utils.update_flag_colors(mesh)
                return {'FINISHED'}

            selected_indices = flag_utils.get_selected_face_indices(obj)
            if selected_indices.size == 0:
                self.report({'WARNING'}, 'No faces selected.')
                return {'CANCELLED'}
            changed = flag_utils.bulk_modify_flag(mesh, selected_indices, self.flag_bit, self.action.lower())
            mesh.update()

            # Auto-Update Colors
            flag_utils.update_flag_colors(mesh)

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

        # Check if any flags are selected; show a compact mask and match preview.
        mask = sum(int(bit) for i, (bit, _, _) in enumerate(FACE_FLAG_OPTIONS) if getattr(scene, f"cf_flag_{i}", False))
        selected_labels = [label for i, (_, label, _) in enumerate(FACE_FLAG_OPTIONS) if getattr(scene, f"cf_flag_{i}", False)]
        if mask == 0:
            box.label(text="No flags selected (mask=0)", icon='ERROR')
        else:
            box.label(text=f"Mask 0x{mask:04X}: {', '.join(selected_labels)}", icon='FILTER')

        # Mode and Action in a single row.
        row = box.row(align=True)
        row.prop(scene, "cf_select_mode", text="", icon='FILTER')
        row.prop(scene, "cf_select_action", text="", icon='RESTRICT_SELECT_ON')
        apply_row = row.row(align=True)
        apply_row.enabled = mask != 0
        apply_row.operator("carnivores.select_by_flags", text="Apply", icon='CHECKMARK')

        if mask:
            matched, scoped = flag_utils.count_matching_faces(
                context.active_object,
                mask,
                getattr(scene, "cf_select_mode", "ANY"),
            )
            scope = "selected faces" if context.active_object and context.active_object.mode == 'EDIT' else "all faces"
            box.label(text=f"Preview: {matched}/{scoped} matching in {scope}.", icon='VIEWZOOM')

        layout.separator()
        col = layout.column(align=True)
        col.label(text="Match mode tooltips explain Any/All/None; selection scope follows Object/Edit Mode.", icon='INFO')

class CARNIVORES_OT_clear_flag_selections(bpy.types.Operator):
    """Clear all flag selections in the Selection Tools panel"""
    bl_idname = "carnivores.clear_flag_selections"
    bl_label = "Clear Flag Selections"
    bl_description = "Clear the temporary flag mask used by Select Faces by 3DF Flags; this does not modify mesh face flags."
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        scene = context.scene
        for i, (bit, label, _) in enumerate(FACE_FLAG_OPTIONS):
            prop_name = f"cf_flag_{i}"
            if hasattr(scene, prop_name):
                setattr(scene, prop_name, False)
        self.report({'INFO'}, "Cleared all flag selections.")
        return {'FINISHED'}