bl_info = {
    "name": "CarnivoresIO",
    "author": "StriderTibe",
    "version": (2, 3, 1),
    "blender": (4, 0, 0),
    "location": "File > Import-Export",
    "description": "Import/Export Carnivores .3DF and .CAR models with animations",
    "category": "Import-Export",
}

import bpy
from .operators import classes as operator_classes
from .operators import animation as anim_ops
from .utils import animation as anim_utils
from .operators.animation import set_kps_mode, get_kps_mode
from .utils.logger import info

def _volume_property_changed():
    """RNA property update callback — re-applies volume to live handles."""
    anim_ops.update_audio_volumes()


def _nla_sound_enabled_changed(self, context):
    """Stop managed preview handles as soon as the authoritative toggle is disabled."""
    anim_ops.set_nla_sound_enabled(bool(getattr(self, "carnivores_nla_sound_enabled", True)))

class CarnivoresPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    debug_mode: bpy.props.BoolProperty(
        name="Debug Mode",
        description="Enable verbose logging in the console",
        default=False,
    )
    performance_mode: bpy.props.BoolProperty(
        name="Performance Instrumentation",
        description="Collect structured import/export stage timings in Carnivores_Performance_Report",
        default=False,
    )
    auto_select_imported: bpy.props.BoolProperty(
        name="Select Imported Objects",
        description="Select imported primary mesh objects and make the last imported mesh active after import.",
        default=True,
    )
    auto_frame_imported: bpy.props.BoolProperty(
        name="Frame Imported Objects",
        description="Frame imported objects when the import is invoked from a compatible 3D View.",
        default=False,
    )
    default_import_scale: bpy.props.FloatProperty(
        name="Default Import Scale",
        description="Default scale shown in import dialogs; existing operator and scripted defaults remain unchanged until a user changes this preference.",
        default=0.01,
        min=0.01,
        max=100.0,
    )
    default_export_scale: bpy.props.FloatProperty(
        name="Default Export Scale",
        description="Default scale shown in export dialogs; existing operator and scripted defaults remain unchanged until a user changes this preference.",
        default=100.0,
        min=1.0,
        max=1000.0,
    )
    default_flip_handedness: bpy.props.BoolProperty(
        name="Default Coordinate Conversion",
        description="Use the standard Carnivores file/Blender coordinate conversion in newly opened dialogs.",
        default=True,
    )
    default_bone_import_type: bpy.props.EnumProperty(
        name="Default 3DF Bone Import",
        description="Default deformation representation for newly opened .3DF import dialogs.",
        items=[
            ('NONE', "None", "Import no deformation objects."),
            ('ARMATURE', "Armature", "Create one editable armature."),
            ('HOOKS', "Hooks", "Create lightweight hook controls."),
        ],
        default='HOOKS',
    )
    show_advanced_options: bpy.props.BoolProperty(
        name="Show Advanced Options",
        description="Show coordinate-axis controls in import and export dialogs.",
        default=True,
    )
    documentation_url: bpy.props.StringProperty(
        name="Documentation URL",
        default="https://github.com/Tibbee/CarnivoresIO/blob/main/README.md",
    )
    issue_tracker_url: bpy.props.StringProperty(
        name="Issue Tracker URL",
        default="https://github.com/Tibbee/CarnivoresIO/issues",
    )

    def draw(self, context):
        layout = self.layout
        diagnostics = layout.box()
        diagnostics.label(text="Diagnostics", icon='INFO')
        diagnostics.prop(self, "debug_mode")
        diagnostics.prop(self, "performance_mode")
        import_behavior = layout.box()
        import_behavior.label(text="Import Behavior", icon='IMPORT')
        import_behavior.prop(self, "auto_select_imported")
        import_behavior.prop(self, "auto_frame_imported")
        import_behavior.label(text="These defaults apply when opening an import dialog; scripted operators keep their own properties.")

        defaults = layout.box()
        defaults.label(text="Operation Defaults", icon='PREFERENCES')
        defaults.prop(self, "default_import_scale")
        defaults.prop(self, "default_export_scale")
        defaults.prop(self, "default_flip_handedness")
        defaults.prop(self, "default_bone_import_type")
        defaults.prop(self, "show_advanced_options")

        links = layout.box()
        links.label(text="Help", icon='HELP')
        row = links.row(align=True)
        documentation = row.operator("wm.url_open", text="Documentation", icon='URL')
        documentation.url = self.documentation_url
        issues = row.operator("wm.url_open", text="Report Issue", icon='URL')
        issues.url = self.issue_tracker_url
        layout.operator("carnivores.restore_preferences", text="Restore Defaults", icon='FILE_REFRESH')

class CARNIVORES_MT_import(bpy.types.Menu):
    bl_idname = "CARNIVORES_MT_import"
    bl_label = "Carnivores Engine (.3df, .car)"

    def draw(self, context):
        layout = self.layout
        layout.operator("carnivores.import_3df", text="Static Model (.3df)")
        layout.operator("carnivores.import_car", text="Animated Model (.car)")

class CARNIVORES_MT_export(bpy.types.Menu):
    bl_idname = "CARNIVORES_MT_export"
    bl_label = "Carnivores Engine (.3df, .car, .3dn)"

    def draw(self, context):
        layout = self.layout
        layout.operator("carnivores.export_3df", text="Static Model (.3df)")
        layout.operator("carnivores.export_car", text="Animated Model (.car)")
        layout.operator("carnivores.export_3dn", text="Dinosaur Hunter Mobile/HD (.3dn)")
        layout.operator("carnivores.export_vtl", text="Animation (.vtl)")

classes = (
    CarnivoresPreferences,
    CARNIVORES_MT_import,
    CARNIVORES_MT_export,
)

def register():
    # Register Classes
    for cls in classes:
        bpy.utils.register_class(cls)
        
    for cls in operator_classes:
        bpy.utils.register_class(cls)

    # Register Menus
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)
    
    # Register Properties
    bpy.types.Scene.cf_flag_section = bpy.props.BoolProperty(
        name="Flag Selection",
        description="Show the C2 face flags used by the Select Faces by 3DF Flags tool.",
        default=False,
    )
    bpy.types.Scene.cf_show_flag_legend = bpy.props.BoolProperty(
        name="Show Color Legend",
        description="Show the generated face-flag color names in the 3DF Face Flags panel.",
        default=False,
    )
    bpy.types.Scene.cf_select_mode = bpy.props.EnumProperty(
        items=[
            ('ANY', "Has Any", "Match faces with at least one selected flag (OR)."),
            ('ALL', "Has All", "Match faces with every selected flag (AND)."),
            ('NONE', "Has None", "Match faces with none of the selected flags (NOT)."),
        ],
        name="Flag Match Mode",
        description="Choose how the selected C2 face flags are combined when finding faces.",
        default='ANY',
    )
    bpy.types.Scene.cf_select_action = bpy.props.EnumProperty(
        items=[
            ('SELECT', "Select", "Select only faces that match the flag mask and deselect the rest."),
            ('DESELECT', "Deselect", "Deselect faces that match the flag mask."),
            ('INVERT', "Invert", "Invert selection only on faces that match the flag mask."),
        ],
        name="Selection Action",
        description="Choose what to do with faces matched by the selected C2 face flags.",
        default='SELECT',
    )
    from .utils.validation import FORMAT_ITEMS
    bpy.types.Scene.carnivores_validation_format = bpy.props.EnumProperty(
        name="Preflight Target Format",
        description="Format-aware requirements used by the Carnivores Model health panel.",
        items=FORMAT_ITEMS,
        default='AUTO',
    )
    bpy.types.Scene.carnivores_validation_export_textures = bpy.props.BoolProperty(
        name="Export Textures",
        description="Include texture and active-UV requirements in model preflight checks.",
        default=True,
    )
    bpy.types.Scene.carnivores_validation_check_audio = bpy.props.BoolProperty(
        name="Check Audio Conversion",
        description="Verify linked CAR sounds can be converted to 22050Hz mono PCM16.",
        default=True,
    )
    # Register flags
    from .core.constants import FACE_FLAG_OPTIONS
    for i, (bit, label, description) in enumerate(FACE_FLAG_OPTIONS):
        setattr(
            bpy.types.Scene,
            f"cf_flag_{i}",
            bpy.props.BoolProperty(
                name=label,
                description=f"C2 face flag 0x{bit:04X}: {description}",
                default=False,
            ),
        )

    bpy.types.Object.carnivores_anim_source = bpy.props.EnumProperty(
        name="Animation Source",
        description="Choose where to read NLA tracks from",
        items=[
            ('AUTO', "Auto (Detect)", "Try Shape Keys first, then Object Animation"),
            ('SHAPE_KEYS', "Shape Keys", "Use Shape Key Animation Data (Typical for .CAR)"),
            ('OBJECT', "Object/Armature", "Use Object Level Animation Data")
        ],
        default='AUTO'
    )
    
    bpy.types.Object.carnivores_active_nla_index = bpy.props.IntProperty(
        name="Active NLA Track Index",
        description="NLA track whose sound, KPS, preview, and timing controls are shown.",
        default=0,
    )
    bpy.types.Object.carnivores_reconstruct_algorithm = bpy.props.EnumProperty(
        name="Reconstruction Algorithm",
        description="Choose the stable legacy autorig or the experimental topology-first proposal",
        items=[
            ('LEGACY', "Legacy", "Use centroid clustering and scored MST reconstruction"),
            ('TOPOLOGY', "Topology (Experimental)", "Use owner boundaries and deterministic topology analysis"),
        ],
        default='LEGACY',
    )
    bpy.types.Object.carnivores_reconstruct_component_policy = bpy.props.EnumProperty(
        name="Disconnected Components",
        description="Choose how topology reconstruction handles owner regions without boundary connections",
        items=[
            ('MULTI_ROOT', "Multiple Roots", "Preserve every component as a separate root"),
            ('ATTACH_NEAREST', "Attach Nearest", "Connect components using low-confidence nearest-region edges"),
            ('SKIP', "Skip Detached", "Rig only the component with the most owned vertices"),
            ('HOOKS', "Reserve for Hooks", "Skip detached groups and report them for a future hook workflow"),
        ],
        default='MULTI_ROOT',
    )
    bpy.types.Object.carnivores_reconstruct_root_override = bpy.props.IntProperty(
        name="Root Override Index",
        description="Manually specify the bone index to use as root (set to -1 for auto)",
        default=-1,
        min=-1,
    )
    bpy.types.Object.carnivores_reconstruct_side_axis = bpy.props.EnumProperty(
        name="Side Axis",
        description="Axis used to classify bilateral owner regions in topology proposals",
        items=[
            ('X', "X", "Use Blender X as the bilateral side axis."),
            ('Y', "Y", "Use Blender Y as the bilateral side axis."),
            ('Z', "Z", "Use Blender Z as the bilateral side axis."),
        ],
        default='X',
    )
    bpy.types.Object.carnivores_reconstruct_side_inverted = bpy.props.BoolProperty(
        name="Invert Side Axis",
        description="Invert positive and negative side classification for topology proposals",
        default=False,
    )
    bpy.types.Object.carnivores_rig_proposal_edges = bpy.props.CollectionProperty(
        type=anim_ops.CARNIVORES_PG_rig_proposal_edge,
        name="Rig Proposal Edges",
    )
    bpy.types.Object.carnivores_rig_proposal_edge_index = bpy.props.IntProperty(
        name="Rig Proposal Edge",
        default=0,
        min=0,
    )
    bpy.types.Object.carnivores_reconstruct_root_choice = bpy.props.EnumProperty(
        name="Reconstruction Root",
        description="Choose Automatic or an owner index with its generated name and vertex count.",
        items=anim_ops.reconstruction_root_items,
        get=anim_ops.get_reconstruction_root_choice,
        set=anim_ops.set_reconstruction_root_choice,
    )
    bpy.types.Object.carnivores_reconstruct_semantic_naming = bpy.props.BoolProperty(
        name="Semantic L/R Suffixes",
        description="Automatically append _L or _R to generic bone names and vertex groups based on symmetry plane alignment",
        default=True,
    )
    bpy.types.Object.carnivores_reconstruct_legacy_filter_clusters = bpy.props.BoolProperty(
        name="Filter Detached Centroid Clusters",
        description="Legacy compatibility option that discards every centroid cluster except the one with the most groups; may remove valid limb chains",
        default=False,
    )
    bpy.types.Object.carnivores_reconstruct_rig_policy = bpy.props.EnumProperty(
        name="Existing Rig Policy",
        description="How reconstruction handles an armature that was previously generated for this mesh",
        items=[
            ('CREATE_NEW', "Create New", "Always create a fresh armature, keeping any previous generated rig"),
            ('REPLACE_GENERATED', "Replace Generated", "Remove the previously generated armature (marked by carnivores_rig_algorithm) before creating a new one"),
            ('UPDATE_GENERATED', "Update Generated", "Reuse the existing generated armature object and replace its bones in place"),
            ('CANCEL_IF_RIGGED', "Cancel if Rigged", "Refuse reconstruction when the mesh already has a generated armature"),
        ],
        default='CREATE_NEW',
    )
    bpy.types.Object.carnivores_reconstruct_smooth_weights = bpy.props.BoolProperty(
        name="Smooth Weights",
        description="Generate smoothed deform weights from imported owners; Legacy also uses them for centroid inference while Topology keeps canonical boundaries for structure",
        default=False,
    )
    bpy.types.Object.carnivores_reconstruct_smooth_iterations = bpy.props.IntProperty(
        name="Smoothing Iterations",
        description="Number of generated deform-weight smoothing passes",
        default=3,
        min=1,
        max=10,
    )
    bpy.types.Object.carnivores_reconstruct_smooth_factor = bpy.props.FloatProperty(
        name="Smoothing Factor",
        description="Intensity of smoothing per pass",
        default=0.5,
        min=0.01,
        max=1.0,
    )
    bpy.types.Object.carnivores_reconstruct_smooth_joints_only = bpy.props.BoolProperty(
        name="Smooth Joints Only",
        description="Only smooth vertices near owner-group boundaries",
        default=True,
    )
    
    # Register KPS Mode property
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
    
    bpy.types.Action.carnivores_sound_ptr = bpy.props.PointerProperty(
        type=bpy.types.Sound,
        name="Linked Sound",
        description="Sound effect associated with this animation"
    )
    
    bpy.types.Scene.carnivores_nla_sound_enabled = bpy.props.BoolProperty(
        name="Enable NLA Sound",
        description="Play linked sounds for previews, NLA tweak mode, and the selected Carnivores track during timeline playback; disabling stops managed playback immediately.",
        default=True,
        update=_nla_sound_enabled_changed,
    )

    bpy.types.Action.carnivores_sound_volume = bpy.props.FloatProperty(
        name="Sound Volume",
        description="Volume multiplier for this animation's preview sound",
        default=1.0,
        min=0.0,
        max=2.0,
        update=lambda self, ctx: _volume_property_changed()
    )

    bpy.types.Scene.carnivores_nla_sound_volume = bpy.props.FloatProperty(
        name="NLA Sound Volume",
        description="Master volume for Carnivores NLA sound playback",
        default=1.0,
        min=0.0,
        max=2.0,
        update=lambda self, ctx: _volume_property_changed()
    )
    
    # Register Handlers
    anim_ops.register_audio_handlers()
        
    from .utils.preset_deployment import deploy_presets
    deploy_presets()
        
    info("CarnivoresIO: Registered")

def unregister():
    info("CarnivoresIO: Unregistering...")
    
    # Cleanup Audio handlers and resources
    anim_ops.unregister_audio_handlers()
        
    # Clean up temp files from packed-sound playback
    anim_utils.cleanup_temp_sound_files()

    # Unregister Menus
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)

    # Unregister Classes
    for cls in reversed(operator_classes):
        bpy.utils.unregister_class(cls)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
        
    # Unregister Properties
    del bpy.types.Scene.cf_flag_section
    del bpy.types.Scene.cf_show_flag_legend
    del bpy.types.Scene.cf_select_mode
    del bpy.types.Scene.cf_select_action
    del bpy.types.Scene.carnivores_validation_format
    del bpy.types.Scene.carnivores_validation_export_textures
    del bpy.types.Scene.carnivores_validation_check_audio
    
    from .core.constants import FACE_FLAG_OPTIONS
    for i in range(len(FACE_FLAG_OPTIONS)):
        delattr(bpy.types.Scene, f"cf_flag_{i}")
        
    del bpy.types.Object.carnivores_anim_source
    del bpy.types.Object.carnivores_active_nla_index
    del bpy.types.Object.carnivores_reconstruct_algorithm
    del bpy.types.Object.carnivores_reconstruct_component_policy
    del bpy.types.Object.carnivores_reconstruct_root_choice
    del bpy.types.Object.carnivores_reconstruct_root_override
    del bpy.types.Object.carnivores_reconstruct_side_axis
    del bpy.types.Object.carnivores_reconstruct_side_inverted
    del bpy.types.Object.carnivores_rig_proposal_edges
    del bpy.types.Object.carnivores_rig_proposal_edge_index
    del bpy.types.Object.carnivores_reconstruct_semantic_naming
    del bpy.types.Object.carnivores_reconstruct_legacy_filter_clusters
    del bpy.types.Object.carnivores_reconstruct_rig_policy
    del bpy.types.Object.carnivores_reconstruct_smooth_weights
    del bpy.types.Object.carnivores_reconstruct_smooth_iterations
    del bpy.types.Object.carnivores_reconstruct_smooth_factor
    del bpy.types.Object.carnivores_reconstruct_smooth_joints_only
    del bpy.types.Action.carnivores_kps_mode
    del bpy.types.Action.carnivores_sound_ptr
    del bpy.types.Scene.carnivores_nla_sound_enabled
    del bpy.types.Action.carnivores_sound_volume
    del bpy.types.Scene.carnivores_nla_sound_volume
    
    info("CarnivoresIO: Unregistered")

def menu_func_import(self, context):
    self.layout.menu(CARNIVORES_MT_import.bl_idname, text="Carnivores Engine (.3df, .car)")

def menu_func_export(self, context):
    self.layout.menu(CARNIVORES_MT_export.bl_idname, text="Carnivores Engine (.3df, .car, .3dn)")

if __name__ == "__main__":
    register()