from . import io, flags, animation

classes = (
    io.CARNIVORES_OT_import_3df,
    io.CARNIVORES_OT_export_3df,
    io.CARNIVORES_OT_export_car,
    io.CARNIVORES_OT_import_car,
    io.CARNIVORES_OT_modal_message,
    
    flags.CARNIVORES_OT_create_3df_flags,
    flags.VIEW3D_PT_3df_face_flags,
    flags.CARNIVORES_OT_select_by_flags,
    flags.CARNIVORES_OT_modify_3df_flag,
    flags.CARNIVORES_OT_clear_flag_selections,
    flags.CARNIVORES_OT_visualize_flags,
    flags.VIEW3D_PT_carnivores_selection,
    
    animation.CARNIVORES_OT_play_linked_sound,
    animation.CARNIVORES_OT_import_sound_for_action,
    animation.CARNIVORES_OT_toggle_nla_sound_playback,
    animation.CARNIVORES_UL_animation_list,
    animation.CARNIVORES_OT_set_kps,
    animation.CARNIVORES_OT_reset_kps,
    animation.CARNIVORES_OT_play_track_preview,
    animation.CARNIVORES_OT_resync_animation,
    animation.CARNIVORES_OT_reconstruct_armature,
    animation.CARNIVORES_OT_debug_rig_info,
    animation.VIEW3D_PT_carnivores_animation,
)