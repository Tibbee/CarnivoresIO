"""Non-destructive Blender-side model health and export preflight checks.

The parser validator protects binary input.  This module performs the
corresponding scene/datablock checks before an export or from the Model Health
panel.  It only reads Blender data; repairs remain separate, explicit
operators.
"""

import os
import json
from collections import Counter

import bmesh
import bpy
import numpy as np

from ..core.constants import FACE_FLAG_OPTIONS, TEXTURE_WIDTH
from .common import timed
from .reporting import OperationReport


FORMAT_ITEMS = (
    ("AUTO", "Auto", "Choose .CAR when animation data is present, otherwise .3DF."),
    ("3DF", ".3DF", "Validate a static Carnivores model export."),
    ("CAR", ".CAR", "Validate an animated Carnivores model export."),
    ("3DN", ".3DN", "Validate a Dinosaur Hunter Mobile/HD static model export."),
    ("VTL", ".VTL", "Validate a standalone Carnivores animation export."),
)

C2_MEE_OBJECT_LIMIT = 1024
C2_MEE_ANIMATION_LIMIT = 64
C2_MEE_SOUND_LIMIT = 64
C2_MEE_TEXTURE_HEIGHT = 256
CAR_VTL_COORDINATE_MIN = -32768.0 / 16.0
CAR_VTL_COORDINATE_MAX = 32767.0 / 16.0
SERIALIZED_NAME_BYTES = 32
SERIALIZED_BONE_BYTES = 32


def _format_key(value):
    value = str(value or "AUTO").upper().lstrip(".")
    return value if value in {item[0] for item in FORMAT_ITEMS} else "AUTO"


def _active_animation_data(obj):
    if not obj or obj.type != 'MESH':
        return None
    shape_keys = getattr(obj.data, "shape_keys", None)
    if shape_keys and shape_keys.animation_data:
        return shape_keys.animation_data
    parent = getattr(obj, "parent", None)
    if parent and parent.type == 'ARMATURE' and parent.animation_data:
        return parent.animation_data
    if obj.animation_data:
        return obj.animation_data
    return None


def _animation_records(obj):
    """Mirror the exporter's animation source selection without changing state."""
    anim_data = _active_animation_data(obj)
    if not anim_data:
        return []

    records = []
    if anim_data.nla_tracks:
        for track in reversed(anim_data.nla_tracks):
            for strip in track.strips:
                if not strip.action:
                    continue
                records.append({
                    "name": strip.name,
                    "action": strip.action,
                    "start": float(strip.frame_start),
                    "end": float(strip.frame_end),
                })
    elif anim_data.action:
        action = anim_data.action
        try:
            start, end = action.frame_range
        except (AttributeError, TypeError, ValueError):
            start, end = 1.0, 1.0
        records.append({
            "name": action.name.replace("_Action", ""),
            "action": action,
            "start": float(start),
            "end": float(end),
        })
    return records


def _vtl_animation_record(obj):
    """Mirror VTL's selected-track/strip choice without changing selection."""
    anim_data = _active_animation_data(obj)
    if not anim_data:
        return None
    if anim_data.use_nla and anim_data.nla_tracks:
        target_track = next((track for track in anim_data.nla_tracks if track.select or track.active), None)
        target_track = target_track or anim_data.nla_tracks[0]
        if not target_track.strips:
            return None
        strip = next((item for item in target_track.strips if item.select or item.active), None)
        strip = strip or target_track.strips[0]
        if not strip.action:
            return None
        return {
            "name": strip.name,
            "action": strip.action,
            "start": float(strip.frame_start),
            "end": float(strip.frame_end),
        }
    if anim_data.action:
        action = anim_data.action
        start, end = _frame_range(action)
        return {
            "name": action.name.replace("_Action", ""),
            "action": action,
            "start": float(start),
            "end": float(end),
        }
    return None


def resolve_target_format(obj, target_format="AUTO"):
    target = _format_key(target_format)
    if target != "AUTO":
        return target
    return "CAR" if _animation_records(obj) else "3DF"


def _add(report, severity, category, message, *, suggested_action=""):
    report.add(
        severity,
        category,
        message,
        suggested_action=suggested_action,
    )


def _read_coordinates(mesh):
    coordinates = np.empty((len(mesh.vertices), 3), dtype=np.float64)
    if coordinates.size:
        mesh.vertices.foreach_get("co", coordinates.ravel())
    return coordinates


def _check_mesh(report, obj, lightweight=False):
    mesh = obj.data
    vertex_count = len(mesh.vertices)
    face_count = len(mesh.polygons)
    loop_count = len(mesh.loops)

    if vertex_count == 0:
        _add(report, "ERROR", "Mesh", "Mesh contains no vertices.", suggested_action="Add or select a mesh with geometry.")
    if face_count == 0:
        _add(report, "ERROR", "Mesh", "Mesh contains no faces.", suggested_action="Add faces before exporting a model.")

    try:
        coordinates = _read_coordinates(mesh)
    except Exception as exc:
        _add(report, "ERROR", "Mesh", f"Could not read vertex coordinates: {exc}")
        coordinates = np.empty((0, 3), dtype=np.float64)

    if coordinates.size and not np.isfinite(coordinates).all():
        count = int(np.count_nonzero(~np.isfinite(coordinates)))
        _add(
            report,
            "ERROR",
            "Mesh",
            f"Mesh coordinates contain {count} NaN or infinite component(s).",
            suggested_action="Replace non-finite vertex coordinates before exporting.",
        )
    elif vertex_count:
        _add(report, "PASS", "Mesh", f"Read {vertex_count} finite mesh vertices.")

    invalid_faces = 0
    non_triangles = 0
    for polygon in mesh.polygons:
        if polygon.loop_total < 3:
            invalid_faces += 1
            continue
        if polygon.loop_total != 3:
            non_triangles += 1
        start = polygon.loop_start
        end = start + polygon.loop_total
        if start < 0 or end > loop_count:
            invalid_faces += 1
            continue
        for loop in mesh.loops[start:end]:
            if loop.vertex_index < 0 or loop.vertex_index >= vertex_count:
                invalid_faces += 1
                break

    visible_modifiers = [modifier for modifier in obj.modifiers if modifier.show_viewport]
    if visible_modifiers:
        names = ", ".join(modifier.name for modifier in visible_modifiers[:4])
        if len(visible_modifiers) > 4:
            names += f", +{len(visible_modifiers) - 4} more"
        _add(
            report,
            "WARNING",
            "Geometry",
            f"Visible modifier(s) present ({names}); model export reads the base mesh and only triangulates a temporary copy, so modifiers are not applied to static geometry.",
            suggested_action="Apply required modifiers explicitly or export a prepared mesh copy.",
        )

    if invalid_faces:
        _add(
            report,
            "ERROR",
            "Geometry",
            f"{invalid_faces} face(s) have invalid loop structure or fewer than three vertices.",
            suggested_action="Repair the mesh topology before exporting.",
        )
    elif face_count:
        if non_triangles:
            _add(
                report,
                "INFO",
                "Geometry",
                f"{non_triangles} polygon(s) are not triangles; the exporter will triangulate a temporary copy.",
            )
        if not lightweight and non_triangles:
            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                bmesh.ops.triangulate(
                    bm,
                    faces=list(bm.faces),
                    quad_method='BEAUTY',
                    ngon_method='BEAUTY',
                )
            except Exception as exc:
                _add(
                    report,
                    "ERROR",
                    "Geometry",
                    f"Mesh could not be triangulated without modifying the source: {exc}",
                    suggested_action="Inspect non-manifold or malformed faces before exporting.",
                )
            finally:
                bm.free()
        if not any(entry.category == "Geometry" and entry.severity == "ERROR" for entry in report.entries):
            _add(report, "PASS", "Geometry", f"Validated {face_count} face(s) for export triangulation.")

    return coordinates


def _check_uvs(report, obj, target, export_textures, lightweight=False):
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if target in {"3DF", "CAR"} and export_textures and uv_layer is None:
        _add(
            report,
            "ERROR",
            "Texture",
            f"{target} texture export is enabled but the mesh has no active UV layer.",
            suggested_action="Create and assign an active UV map or disable texture export.",
        )
    elif target == "3DN" and uv_layer is None:
        _add(
            report,
            "WARNING",
            "Texture",
            ".3DN has no active UV layer; exported pixel coordinates will be zero.",
            suggested_action="Create an active UV map if the external .3DN texture should display correctly.",
        )
    elif uv_layer is not None:
        _add(report, "PASS", "Texture", f"Active UV layer '{uv_layer.name}' is available.")

    if target not in {"3DF", "CAR", "3DN"}:
        return None
    if not export_textures:
        _add(report, "INFO", "Texture", "Texture checks are disabled for this validation target.")
        return None

    from .io import find_texture_image

    try:
        image, height = find_texture_image(obj)
    except Exception as exc:
        _add(report, "ERROR", "Texture", f"Could not resolve a material image: {exc}")
        return None

    if image is None:
        if target in {"3DF", "CAR"}:
            _add(
                report,
                "ERROR",
                "Texture",
                f"{target} texture export is enabled but no image was found in the material nodes.",
                suggested_action="Connect an image texture to a material or disable texture export.",
            )
        else:
            _add(report, "WARNING", "Texture", "No material image was found; .3DN UV height defaults to 256 pixels.")
        return None

    try:
        width, image_height = (int(image.size[0]), int(image.size[1]))
    except Exception as exc:
        _add(report, "ERROR", "Texture", f"Could not read image dimensions: {exc}")
        return None

    if width != TEXTURE_WIDTH:
        _add(
            report,
            "ERROR",
            "Texture",
            f"Image '{image.name}' is {width}px wide; Carnivores textures must be {TEXTURE_WIDTH}px wide.",
            suggested_action="Use a 256-pixel-wide image or disable texture export.",
        )
    if image_height <= 0:
        _add(report, "ERROR", "Texture", f"Image '{image.name}' has no pixel rows.")

    if not lightweight:
        try:
            pixel_count = len(image.pixels)
            expected = max(0, width * image_height * 4)
            if pixel_count != expected:
                _add(
                    report,
                    "ERROR",
                    "Texture",
                    f"Image '{image.name}' exposes {pixel_count} pixel components; expected {expected}.",
                    suggested_action="Load or pack the image data before exporting.",
                )
            elif hasattr(image, "has_data") and not image.has_data:
                _add(report, "ERROR", "Texture", f"Image '{image.name}' has no loaded pixel data.")
            else:
                encoded_bytes = width * image_height * 2
                row_bytes = TEXTURE_WIDTH * 2
                if encoded_bytes % row_bytes:
                    _add(report, "ERROR", "Texture", f"Image '{image.name}' would produce a non-row-aligned ARGB1555 payload.")
                else:
                    _add(
                        report,
                        "PASS",
                        "Texture",
                        f"Resolved image '{image.name}' at {width}x{image_height}; ARGB1555 payload is {encoded_bytes} row-aligned bytes.",
                    )
        except Exception as exc:
            _add(report, "ERROR", "Texture", f"Image '{image.name}' pixel data is unavailable: {exc}")

    if image_height != C2_MEE_TEXTURE_HEIGHT:
        _add(
            report,
            "WARNING",
            "C2 MEE compatibility",
            f"Texture is {image_height} rows high. The C2 MEE OpenGL loader uses a {TEXTURE_WIDTH}x{C2_MEE_TEXTURE_HEIGHT} buffer; the software loader and add-on support complete variable-height rows.",
        )
    else:
        _add(report, "PASS", "C2 MEE compatibility", "Texture height matches the current C2 MEE OpenGL buffer.")

    return image


def _check_flags(report, obj):
    mesh = obj.data
    attr = mesh.attributes.get("3df_flags")
    face_count = len(mesh.polygons)
    if attr is None:
        _add(
            report,
            "INFO",
            "Face flags",
            "Mesh has no '3df_flags' attribute; the exporter will write zero flags for every face.",
            suggested_action="Use Create 3df_flags Attribute if the model needs per-face flags.",
        )
        return
    if attr.domain != 'FACE' or attr.data_type != 'INT':
        _add(
            report,
            "ERROR",
            "Face flags",
            f"'3df_flags' must be a FACE-domain INT attribute, not {attr.domain}/{attr.data_type}.",
            suggested_action="Create a valid '3df_flags' attribute and preserve the existing data before retrying.",
        )
        return
    if len(attr.data) != face_count:
        _add(
            report,
            "ERROR",
            "Face flags",
            f"'3df_flags' contains {len(attr.data)} values for {face_count} faces.",
            suggested_action="Recreate the attribute with Create 3df_flags Attribute.",
        )
        return

    values = np.empty(face_count, dtype=np.int64)
    if face_count:
        attr.data.foreach_get("value", values)
    out_of_range = (values < 0) | (values > 0xFFFF)
    if np.any(out_of_range):
        _add(
            report,
            "ERROR",
            "Face flags",
            f"{int(np.count_nonzero(out_of_range))} face flag value(s) do not fit the serialized 16-bit field.",
            suggested_action="Review the affected values; unknown or out-of-range bits are not removed automatically.",
        )
        return

    known_mask = sum(int(bit) for bit, _, _ in FACE_FLAG_OPTIONS)
    unknown = values & (~known_mask & 0xFFFF)
    if np.any(unknown):
        first = int(unknown[unknown != 0][0])
        _add(
            report,
            "WARNING",
            "Face flags",
            f"{int(np.count_nonzero(unknown))} face(s) use unknown flag bits; first mask is 0x{first:04X}. Bits will be preserved.",
            suggested_action="Confirm unknown bits against the target engine before relying on them.",
        )
    else:
        _add(report, "PASS", "Face flags", f"Validated {face_count} serialized 16-bit face flag value(s).")


def _clean_bone_name(name):
    if name and len(name) >= 4 and name[-4] == '.' and name[-3:].isdigit():
        return name[:-4]
    return name


def _check_serialized_name(report, category, label, value, required=False):
    value = str(value or "")
    if not value:
        severity = "ERROR" if required else "INFO"
        _add(
            report,
            severity,
            category,
            f"{label} is empty." + (" It is required for this export." if required else ""),
            suggested_action=f"Set a {label.lower()} before exporting." if required else "",
        )
        return False
    try:
        encoded = value.encode('ascii')
    except UnicodeEncodeError:
        _add(
            report,
            "ERROR",
            category,
            f"{label} contains non-ASCII characters; the format stores ASCII names.",
            suggested_action=f"Rename the {label.lower()} using ASCII characters.",
        )
        return False
    if len(encoded) > SERIALIZED_NAME_BYTES:
        _add(
            report,
            "ERROR",
            category,
            f"{label} is {len(encoded)} bytes; the format stores at most {SERIALIZED_NAME_BYTES} bytes.",
            suggested_action=f"Shorten the {label.lower()} before exporting.",
        )
        return False
    return True


def _check_skeleton(report, obj, target):
    if target not in {"3DF", "CAR", "3DN"}:
        return

    armature = obj.parent if getattr(obj, "parent", None) and obj.parent.type == 'ARMATURE' else None
    hook_modifiers = []
    if obj.parent is None:
        hook_modifiers = [
            modifier for modifier in obj.modifiers
            if modifier.type == 'HOOK' and modifier.object and modifier.vertex_group
        ]

    if armature:
        bones = list(armature.data.bones)
        if not bones:
            _add(report, "ERROR", "Rig / owners", "The parent armature contains no bones.", suggested_action="Add bones or remove the invalid armature parent.")
            return
        matching_names = [bone.name for bone in bones]
        names = list(matching_names)
        try:
            name_entries = json.loads(armature.get("carnivores_reconstruct_bone_name_map", "[]"))
            if (
                isinstance(name_entries, list)
                and len(name_entries) == len(bones)
                and all(entry.get("blender_name") == bone.name for entry, bone in zip(name_entries, bones))
            ):
                names = [str(entry.get("export_name", bone.name)) for entry, bone in zip(name_entries, bones)]
        except (TypeError, ValueError, AttributeError):
            pass
        parent_indices = []
        index_by_name = {bone.name: index for index, bone in enumerate(bones)}
        for bone in bones:
            parent_indices.append(index_by_name.get(bone.parent.name, -1) if bone.parent else -1)
            if not np.isfinite(np.asarray(bone.head_local, dtype=np.float64)).all():
                _add(report, "ERROR", "Rig / owners", f"Bone '{bone.name}' has non-finite coordinates.")
        source = f"armature '{armature.name}'"
    elif hook_modifiers:
        hook_objects = [modifier.object for modifier in hook_modifiers]
        names = [hook.name for hook in hook_objects]
        matching_names = list(names)
        index_by_name = {name: index for index, name in enumerate(names)}
        parent_indices = [index_by_name.get(hook.parent.name, -1) if hook.parent else -1 for hook in hook_objects]
        source = f"{len(hook_objects)} hook object(s)"
    else:
        names = ["Default"]
        matching_names = list(names)
        parent_indices = [-1]
        source = "implicit Default owner"
        if obj.vertex_groups:
            _add(
                report,
                "WARNING",
                "Rig / owners",
                f"{len(obj.vertex_groups)} vertex group(s) exist but no armature or hooks are connected; export will ignore them and assign all vertices to the implicit Default owner.",
                suggested_action="Parent the mesh to an armature, use hooks, or remove the unused groups before exporting.",
            )
        else:
            _add(report, "INFO", "Rig / owners", "No armature or hook source was found; export will assign all vertices to the implicit Default owner.")

    if len(names) > 32768:
        _add(report, "ERROR", "Rig / owners", f"{len(names)} exported bones/owners exceed the signed 16-bit index range.")
    else:
        _add(report, "PASS", "Rig / owners", f"Resolved {len(names)} exported owner record(s) from {source}.")
        if len(names) > C2_MEE_OBJECT_LIMIT:
            _add(
                report,
                "WARNING",
                "C2 MEE compatibility",
                f"{len(names)} exported owner/object records exceed the current C2 MEE array size of {C2_MEE_OBJECT_LIMIT}; loading may be unsafe.",
            )

    cleaned_names = []
    for index, name in enumerate(names):
        cleaned = _clean_bone_name(str(name))
        cleaned_names.append(cleaned)
        if not _check_serialized_name(report, "Rig / owners", f"Bone #{index} name", cleaned, required=True):
            continue
        if len(cleaned.encode('ascii')) > SERIALIZED_BONE_BYTES:
            _add(report, "ERROR", "Rig / owners", f"Bone #{index} name exceeds {SERIALIZED_BONE_BYTES} bytes after cleanup.")
    name_counts = Counter(name for name in cleaned_names if name)
    duplicate_names = sorted(name for name, count in name_counts.items() if count > 1)
    if duplicate_names:
        _add(
            report,
            "WARNING",
            "Rig / owners",
            f"Cleaned exported bone names are duplicated: {duplicate_names}.",
            suggested_action="Rename bones or remove numeric suffix collisions before exporting.",
        )

    for index, parent in enumerate(parent_indices):
        if parent < -1 or parent >= len(names):
            _add(report, "ERROR", "Rig / owners", f"Bone #{index} has invalid parent index {parent}.")
            continue
        seen = set()
        node = index
        while node != -1:
            if node in seen:
                _add(report, "ERROR", "Rig / owners", f"Owner hierarchy contains a cycle involving bone #{node}.")
                break
            seen.add(node)
            node = parent_indices[node]

    if armature or hook_modifiers:
        known_names = {
            _clean_bone_name(name): index
            for index, name in enumerate(names + matching_names)
        }
        unmatched = 0
        for vertex in obj.data.vertices:
            mapped = False
            for group in vertex.groups:
                if group.group >= len(obj.vertex_groups):
                    continue
                group_name = _clean_bone_name(obj.vertex_groups[group.group].name)
                if group_name in known_names:
                    mapped = True
                    break
            if not mapped:
                unmatched += 1
        if unmatched:
            _add(
                report,
                "WARNING",
                "Rig / owners",
                f"{unmatched} vertex/vertices have no matching exported owner group and will fall back to owner 0.",
                suggested_action="Assign matching vertex groups or review the generated owner mapping.",
            )
        else:
            _add(report, "PASS", "Rig / owners", "Every vertex has at least one matching exported owner group.")


def _frame_range(action):
    try:
        from .animation import get_action_frame_range
        return get_action_frame_range(action)
    except Exception:
        try:
            start, end = action.frame_range
            return int(start), int(end)
        except Exception:
            return 1, 1


def _check_animations(report, obj, target, check_audio=True, audio_cache=None):
    if target == "VTL":
        vtl_record = _vtl_animation_record(obj)
        records = [vtl_record] if vtl_record else []
    else:
        records = _animation_records(obj)
    if not records:
        if target == "VTL":
            _add(
                report,
                "ERROR",
                "Animation",
                ".VTL export requires an Action or NLA strip with animation data.",
                suggested_action="Create or select an animation Action/NLA strip before exporting .VTL.",
            )
        else:
            _add(report, "INFO", "Animation", "No animation source was found; the CAR export will contain zero animations.")
        return

    if target == "CAR" and len(records) > C2_MEE_ANIMATION_LIMIT:
        _add(
            report,
            "WARNING",
            "C2 MEE compatibility",
            f"{len(records)} animations exceed the current C2 MEE fixed limit of {C2_MEE_ANIMATION_LIMIT}; loading may be unsafe.",
        )

    scene = getattr(bpy.context, "scene", None)
    scene_fps = int(getattr(getattr(scene, "render", None), "fps", 0) or 0)
    sounds = {}
    valid_records = 0
    for index, record in enumerate(records):
        action = record["action"]
        name = record["name"]
        if not _check_serialized_name(report, "Animation", f"Animation #{index} name", name, required=True):
            continue

        start = record["start"]
        end = record["end"]
        if not np.isfinite([start, end]).all() or end < start:
            _add(report, "ERROR", "Animation", f"Animation '{name}' has an invalid frame range {start}..{end}.")
        frame_count = max(1, int(round(end - start)) + 1) if np.isfinite([start, end]).all() else 0
        if frame_count <= 0 or frame_count > 0xFFFFFFFF:
            _add(report, "ERROR", "Animation", f"Animation '{name}' cannot produce a representable frame count.")
        elif action is not None:
            action_start, action_end = _frame_range(action)
            try:
                from .animation import iter_action_fcurves
                has_keyframes = any(len(fc.keyframe_points) > 0 for fc in iter_action_fcurves(action))
            except Exception:
                has_keyframes = action_start != action_end
            if not has_keyframes:
                _add(report, "WARNING", "Animation", f"Animation '{name}' has no keyframes and will export as a static frame.")
        raw_kps = action.get("carnivores_kps", scene_fps) if action else scene_fps
        try:
            kps_float = float(raw_kps)
            kps = int(raw_kps)
        except (TypeError, ValueError, OverflowError):
            kps_float = 0.0
            kps = 0
        if not np.isfinite(kps_float) or kps_float != kps or kps <= 0 or kps > 0xFFFFFFFF:
            _add(
                report,
                "ERROR",
                "Animation",
                f"Animation '{name}' has invalid KPS {raw_kps}; CAR/VTL requires a positive 32-bit integer.",
                suggested_action="Set a positive integer KPS override or use the scene FPS.",
            )
        else:
            valid_records += 1

        if target == "CAR" and action:
            from .animation import resolve_action_sound
            sound = resolve_action_sound(action)
            legacy_name = action.get("carnivores_sound")
            if legacy_name and sound is None:
                _add(report, "WARNING", "Sound", f"Animation '{name}' references missing sound '{legacy_name}'.")
            if sound:
                sounds[sound.name] = sound
                _check_serialized_name(report, "Sound", f"Sound '{sound.name}' name", sound.name, required=True)

    if valid_records:
        _add(report, "PASS", "Animation", f"Validated {valid_records} animation timing record(s).")

    if target == "CAR":
        if len(sounds) > C2_MEE_SOUND_LIMIT:
            _add(
                report,
                "WARNING",
                "C2 MEE compatibility",
                f"{len(sounds)} linked sounds exceed the current C2 MEE fixed limit of {C2_MEE_SOUND_LIMIT}; loading may be unsafe.",
            )
        if sounds and check_audio:
            from ..parsers.export_car import convert_sound_to_22khz_mono
            for sound in sounds.values():
                payload, length = convert_sound_to_22khz_mono(
                    sound, conversion_cache=audio_cache
                )
                if not payload or length <= 0:
                    _add(
                        report,
                        "ERROR",
                        "Sound",
                        f"Linked sound '{sound.name}' cannot be converted to non-empty 22050Hz mono PCM16.",
                        suggested_action="Replace, reload, or unlink the sound before exporting CAR.",
                    )
                else:
                    _add(report, "PASS", "Sound", f"Linked sound '{sound.name}' converts to {length} PCM bytes.")


def _check_names_and_format(report, obj, target, filepath="", model_name="", has_sprite=False, sprite_name=""):
    if target == "CAR":
        effective_name = model_name or (os.path.splitext(os.path.basename(filepath))[0] if filepath else obj.name)
        _check_serialized_name(report, "Model name", "CAR model name", effective_name, required=True)
    elif target == "3DN":
        effective_name = model_name or (os.path.splitext(os.path.basename(filepath))[0] if filepath else obj.name)
        _check_serialized_name(report, "Model name", ".3DN model name", effective_name, required=True)
        if has_sprite:
            _check_serialized_name(report, "Sprite", ".3DN sprite name", sprite_name, required=True)


def _check_quantization(report, coordinates, target, export_matrix, has_animation=False):
    if export_matrix is None or target not in {"CAR", "VTL"} or not coordinates.size:
        return
    if target == "CAR" and not has_animation:
        return
    try:
        matrix = np.asarray(export_matrix, dtype=np.float64)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError("export matrix must be a finite 4x4 matrix")
        homogeneous = np.column_stack((coordinates, np.ones(len(coordinates))))
        transformed = homogeneous @ matrix.T
        transformed = transformed[:, :3]
    except Exception as exc:
        _add(report, "ERROR", "Coordinates", f"Could not evaluate the export coordinate transform: {exc}")
        return

    outside = (transformed < CAR_VTL_COORDINATE_MIN) | (transformed > CAR_VTL_COORDINATE_MAX)
    if np.any(outside):
        _add(
            report,
            "ERROR",
            "Coordinates",
            f"{int(np.count_nonzero(np.any(outside, axis=1)))} base vertex/vertices exceed the signed 16-bit animation range after export scaling; animation export would clip them.",
            suggested_action="Reduce Export Scale or adjust the model before exporting CAR/VTL.",
        )
    else:
        _add(report, "PASS", "Coordinates", "Base coordinates fit the signed 16-bit CAR/VTL animation range.")


@timed('validation.preflight')
def validate_blender_model(
    obj,
    target_format="AUTO",
    *,
    export_textures=True,
    check_audio=True,
    lightweight=False,
    filepath="",
    model_name="",
    has_sprite=False,
    sprite_name="",
    export_matrix=None,
    artifact_cache=None,
):
    """Return an :class:`OperationReport` for a Blender model preflight."""
    target = resolve_target_format(obj, target_format)
    report = OperationReport(f"Model validation (.{target})", text_name="Carnivores_Validation_Report")

    if not obj or obj.type != 'MESH' or not getattr(obj, "data", None):
        _add(
            report,
            "ERROR",
            "Input",
            "The active object is not a mesh with data.",
            suggested_action="Select a mesh object before validating or exporting.",
        )
        report.set_outcome(1, 0, 1)
        return report

    coordinates = _check_mesh(report, obj, lightweight=lightweight)
    if target in {"3DF", "CAR", "3DN"}:
        _check_flags(report, obj)
        _check_skeleton(report, obj, target)
    if target in {"3DF", "CAR", "3DN"}:
        _check_uvs(report, obj, target, export_textures, lightweight=lightweight)
    animation_records = _animation_records(obj) if target == "CAR" else []
    if target in {"CAR", "VTL"}:
        audio_cache = None
        if artifact_cache is not None:
            audio_cache = artifact_cache.setdefault("sound_conversion", {})
        _check_animations(
            report,
            obj,
            target,
            check_audio=check_audio and not lightweight,
            audio_cache=audio_cache,
        )
    if target in {"CAR", "3DN"}:
        _check_names_and_format(report, obj, target, filepath, model_name, has_sprite, sprite_name)
    _check_quantization(report, coordinates, target, export_matrix, has_animation=bool(animation_records))

    error_count = report.counts()["ERROR"]
    report.set_outcome(1, 0 if error_count else 1, 1 if error_count else 0)
    return report


def model_health_summary(obj, target_format="AUTO"):
    """Return compact, redraw-safe facts for the Model Health panel."""
    if not obj or obj.type != 'MESH' or not getattr(obj, "data", None):
        return {
            "status": "NO OBJECT",
            "vertices": 0,
            "faces": 0,
            "flags": "Unavailable",
            "texture": "Unavailable",
            "animation": "Unavailable",
        }

    mesh = obj.data
    attr = mesh.attributes.get("3df_flags")
    if attr is None:
        flags = "Missing"
    elif attr.domain != 'FACE' or attr.data_type != 'INT' or len(attr.data) != len(mesh.polygons):
        flags = "Invalid"
    else:
        flags = "Ready"

    uv = "Ready" if mesh.uv_layers.active else "Missing"
    animation = "Present" if _animation_records(obj) else "None"
    # Panel draw methods run on every UI redraw. Full validation is deliberately
    # reserved for the explicit Validate Model operator and export preflight.
    return {
        "status": "RUN VALIDATION",
        "vertices": len(mesh.vertices),
        "faces": len(mesh.polygons),
        "flags": flags,
        "texture": uv,
        "animation": animation,
    }
