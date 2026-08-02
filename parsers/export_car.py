import bpy
import numpy as np
import aud
import io
import os
import struct
import re
import time
import wave

from ..core.core import CAR_HEADER_DTYPE, VERTEX_DTYPE, FACE_DTYPE
from ..core.constants import TEXTURE_WIDTH
from .. import utils
from .export_3df import gather_mesh_data
from ..utils.logger import info, debug, warn, error
from ..utils.animation import resolve_action_sound, sound_datablock_to_factory
from ..utils.performance import current_session

def _extract_pcm16_mono_22050_wav(raw_bytes):
    """Return an unchanged compatible WAV payload, or None for fallback conversion."""
    if not raw_bytes:
        return None
    try:
        with wave.open(io.BytesIO(bytes(raw_bytes)), 'rb') as wav_file:
            if (
                wav_file.getcomptype() != 'NONE'
                or wav_file.getnchannels() != 1
                or wav_file.getsampwidth() != 2
                or wav_file.getframerate() != 22050
                or wav_file.getnframes() <= 0
            ):
                return None
            expected_size = wav_file.getnframes() * 2
            payload = wav_file.readframes(wav_file.getnframes())
            if len(payload) != expected_size or len(payload) % 2:
                return None
            return payload
    except (EOFError, OSError, TypeError, ValueError, wave.Error):
        return None


def _compatible_wav_payload(sound_datablock):
    """Resolve exact CAR-compatible PCM without invoking Audaspace decoding."""
    packed_file = getattr(sound_datablock, "packed_file", None)
    if packed_file:
        payload = _extract_pcm16_mono_22050_wav(getattr(packed_file, "data", None))
        if payload is not None:
            return payload

    filepath = bpy.path.abspath(getattr(sound_datablock, "filepath", ""))
    if filepath and os.path.isfile(filepath):
        try:
            with open(filepath, "rb") as source_file:
                payload = _extract_pcm16_mono_22050_wav(source_file.read())
            if payload is not None:
                return payload
        except OSError:
            pass
    return None


# Helper for sound conversion
def _convert_sound_to_22khz_mono(sound_datablock):
    """
    Converts a Blender Sound datablock to raw 16-bit signed, 22050Hz, Mono PCM data.
    Returns (bytes_data, length_in_bytes) or (None, 0) on failure.
    """
    if not sound_datablock:
        return None, 0

    try:
        compatible_payload = _compatible_wav_payload(sound_datablock)
        if compatible_payload is not None:
            return compatible_payload, len(compatible_payload)

        factory = sound_datablock_to_factory(sound_datablock)
        if not factory:
            warn(f"Could not load factory for sound {sound_datablock.name}")
            return None, 0

        # Fast path: skip resample/rechannel ONLY when the datablock's cached
        # RNA header values are definitively known to be 22050 Hz mono. The RNA
        # values are populated at load time by Blender's aud integration.
        # Accessing factory.specs can force a decode, so we never probe it here
        # (that would double the cost) — unknown values fall through to the
        # full conversion, which is the original behavior.
        rate = getattr(sound_datablock, "samplerate", 0) or 0
        channels_enum = getattr(sound_datablock, "channels", "")
        is_known_22050_mono = (
            rate == 22050 and channels_enum == "MONO"
        )

        if not is_known_22050_mono:
            factory = factory.resample(22050)
            factory = factory.rechannel(1)

        data = factory.data()

        if data is None:
            warn(f"Sound '{sound_datablock.name}' produced no sample data")
            return None, 0

        if data.size == 0:
            warn(f"Sound '{sound_datablock.name}' has zero samples")
            return None, 0

        if not data.dtype == np.float32:
            if np.issubdtype(data.dtype, np.floating):
                data = data.astype(np.float32)
            elif np.issubdtype(data.dtype, np.integer):
                info_val = np.iinfo(data.dtype)
                data = data.astype(np.float32) / (info_val.max + 1)
            else:
                error(f"Sound '{sound_datablock.name}' has unsupported sample dtype: {data.dtype}")
                return None, 0

        if not np.isfinite(data).all():
            error(f"Sound '{sound_datablock.name}' contains non-finite samples")
            return None, 0

        data = np.clip(np.round(data * 32767), -32768, 32767)
        data = np.ascontiguousarray(data, dtype=np.int16)
        if data.ndim > 1:
            data = data.ravel()

        payload = data.tobytes()
        length = len(payload)
        if length != data.nbytes:
            error(f"Sound '{sound_datablock.name}' byte length mismatch: payload={length}, nbytes={data.nbytes}")
            return None, 0

        if length % 2 != 0:
            error(f"Sound '{sound_datablock.name}' payload has odd byte length {length}")
            return None, 0

        if length > 0xFFFFFFFF:
            error(f"Sound '{sound_datablock.name}' payload exceeds 32-bit length field ({length} bytes)")
            return None, 0

        return payload, length

    except Exception as e:
        error(f"Error converting sound {sound_datablock.name}: {e}")
        return None, 0


@utils.timed('export_car.sound_conversion')
def convert_sound_to_22khz_mono(sound_datablock, conversion_cache=None):
    """Convert a sound once per operation when a conversion cache is supplied."""
    if conversion_cache is None or not sound_datablock:
        return _convert_sound_to_22khz_mono(sound_datablock)

    try:
        cache_key = int(sound_datablock.as_pointer())
    except Exception:
        cache_key = id(sound_datablock)
    if cache_key not in conversion_cache:
        conversion_cache[cache_key] = _convert_sound_to_22khz_mono(sound_datablock)
    return conversion_cache[cache_key]


def _animation_sample_count(start, end, frame_step):
    """Return the round-half-up sample count while preserving fractional ranges."""
    if frame_step <= 0:
        raise ValueError("Animation frame step must be positive.")
    return max(1, int(((float(end) - float(start)) / frame_step) + 0.5) + 1)


def _linear_fcurve_samples(fcurve, times):
    """Evaluate a simple linear F-Curve in NumPy, returning None when unsafe."""
    if fcurve is None or len(fcurve.keyframe_points) == 0 or len(fcurve.modifiers):
        return None
    points = list(fcurve.keyframe_points)
    if any(point.interpolation != 'LINEAR' for point in points[:-1]):
        return None
    coordinates = np.asarray([point.co[:] for point in points], dtype=np.float64)
    x_values = coordinates[:, 0]
    if len(x_values) > 1 and np.any(np.diff(x_values) <= 0.0):
        return None
    times = np.asarray(times, dtype=np.float64)
    tolerance = 1e-6
    if np.any(times < x_values[0] - tolerance) or np.any(times > x_values[-1] + tolerance):
        return None
    return np.interp(times, x_values, coordinates[:, 1]).astype(np.float32)


@utils.timed('export_car.animations')
def gather_car_animations(obj, export_matrix, vertex_count):
    """
    Collects animation data by baking the object's deformation.
    Strategy:
    1. If NLA tracks exist, iterate through each strip (Reversed order):
       - Solo the track (mute others).
       - Bake the frame range.
    2. If no NLA tracks, fallback to baking the active Action.
    """
    animations = []
    context = bpy.context
    scene = context.scene
    
    # --- 1. Find Animation Source ---
    anim_data = None
    source_label = ""
    
    # Priority: Shape Keys -> Parent Armature -> Object
    if obj.data.shape_keys and obj.data.shape_keys.animation_data:
        anim_data = obj.data.shape_keys.animation_data
        source_label = "Shape Keys"
    elif obj.parent and obj.parent.type == 'ARMATURE' and obj.parent.animation_data:
        anim_data = obj.parent.animation_data
        source_label = f"Parent Armature ({obj.parent.name})"
    elif obj.animation_data:
        anim_data = obj.animation_data
        source_label = f"Object ({obj.name})"
        
    if not anim_data:
        warn("No animation data found on Object, ShapeKeys, or Parent Armature.")
        return []
        
    debug(f"Found animation source: {source_label}")

    # Direct shape-key sampling does not need dependency-graph or NLA state
    # mutation. Other deformation paths retain the evaluated-mesh bake.
    can_use_fast_path = (
        obj.data.shape_keys is not None
        and all(
            mod.type not in {'ARMATURE', 'HOOK', 'CLOTH', 'SOFT_BODY'}
            for mod in obj.modifiers
            if mod.show_viewport
        )
    )
    used_evaluated_bake = False

    # --- STATE MANAGEMENT ---
    original_frame = scene.frame_current
    original_action = anim_data.action
    original_use_nla = anim_data.use_nla
    
    # Store original mute states if NLA exists
    original_mute_states = {}
    if anim_data.nla_tracks and not can_use_fast_path:
        for track in anim_data.nla_tracks:
            original_mute_states[track] = track.mute

    # Shape Key Pinning Handling
    original_show_only_shape_key = False
    if not can_use_fast_path and obj.type == 'MESH' and obj.show_only_shape_key:
        original_show_only_shape_key = True
        obj.show_only_shape_key = False # Disable pinning to allow animation
        debug("Temporarily disabled Shape Key Pinning for bake.")

    # Evaluated baking temporarily isolates supported deformers. The direct
    # shape-key path reads coordinates and actions without touching modifiers.
    mod_states = {}
    if not can_use_fast_path:
        for mod in obj.modifiers:
            mod_states[mod.name] = mod.show_viewport
            if mod.type in {'ARMATURE', 'HOOK'}:
                mod.show_viewport = True
            else:
                mod.show_viewport = False

    # --- Define Bake Helper ---
    def bake_range(name, start, end, kps, sound_ptr):
        nonlocal used_evaluated_bake
        used_evaluated_bake = True
        debug(f"Baking '{name}' ({start}-{end}) KPS:{kps}")
        # Calculate time step (Blender Frames per Game Frame)
        # e.g. 60 FPS / 20 KPS = 3.0 step
        scene_fps = scene.render.fps
        if kps <= 0: kps = 1 # Safety
        frame_step = scene_fps / kps
        
        num_samples = _animation_sample_count(start, end, frame_step)
        
        debug(f"         Step: {frame_step:.4f}, Samples: {num_samples}")
        frames_data = np.empty((num_samples, vertex_count, 3), dtype=np.int16)

        start_time = time.perf_counter()
        # Shared dependency graph and matrix across frames.
        depsgraph = context.evaluated_depsgraph_get()
        full_matrix_cache = None

        for i in range(num_samples):
            current_frame = start + (i * frame_step)
            
            # Update scene/depsgraph to current frame
            scene.frame_set(int(current_frame), subframe=(current_frame % 1.0))
            
            # Evaluate mesh (Deformed by Armature/Action/NLA)
            eval_obj = obj.evaluated_get(depsgraph)
            
            # Use to_mesh() to get the deformed geometry with modifiers applied
            mesh = eval_obj.to_mesh()
            
            try:
                count = len(mesh.vertices)
                if count != vertex_count:
                    error(f"Frame {current_frame:.2f} of '{name}' has {count} vertices, expected {vertex_count} (Base). Skipping animation.")
                    return None # Signal error
                
                # Bulk get coords
                verts_co_flat = np.empty(count * 3, dtype=np.float32)
                mesh.vertices.foreach_get("co", verts_co_flat)
                verts_co = verts_co_flat.reshape(count, 3)
                
                # Transform into Armature-local space if armature exists
                # OPTIMIZATION: Assume mesh_to_arm is static during bake for now.
                if i == 0:
                    full_matrix_cache = export_matrix
                    if obj.parent and obj.parent.type == 'ARMATURE':
                        mesh_to_arm = eval_obj.parent.matrix_world.inverted() @ eval_obj.matrix_world
                        full_matrix_cache = export_matrix @ np.array(mesh_to_arm)

                transformed_co = utils.apply_import_matrix(verts_co, full_matrix_cache)
                
                # Quantize to fixed point 16.0
                frames_data[i] = np.clip(
                    np.round(transformed_co * 16.0), -32768, 32767
                ).astype(np.int16)
            
            finally:
                eval_obj.to_mesh_clear()
        
        elapsed = time.perf_counter() - start_time
        debug(f"[Timing] bake_range '{name}' took {elapsed:.6f} seconds")
        benchmark = current_session()
        if benchmark:
            benchmark.record_duration(
                "evaluated_mesh_bake",
                elapsed,
                animation=name,
                samples=num_samples,
                vertices=vertex_count,
            )

        # Static Check
        if len(frames_data) > 1 and np.all(frames_data[1:] == frames_data[0]):
            warn(f"Animation '{name}' appears to be static.")

        return frames_data

    def bake_range_fast(name, start, end, kps, anim_source_data, trans_basis, trans_delta, key_blocks_names):
        """
        Fast path for Shape Key animations. Bypasses Blender's mesh evaluation.
        """
        debug(f"Fast Baking '{name}' ({start}-{end}) KPS:{kps}")
        
        # 1. Identify F-Curves
        # Map: Key Index (1-based because 0 is basis) -> FCurve
        start_setup = time.perf_counter()
        fcurve_map = {}
        eval_time_fc = None
        target_action = anim_source_data
        num_keys_total = len(trans_delta) + 1
        
        sk_data = obj.data.shape_keys
        use_relative = sk_data.use_relative

        # Use a helper to get fcurves from action (handles 5.0+)
        def get_fcurves(act):
            if not act: return []
            if hasattr(act, "slots") and bpy.app.version >= (5, 0, 0):
                import bpy_extras.anim_utils
                fcs = []
                for slot in act.slots:
                    bag = bpy_extras.anim_utils.action_get_channelbag_for_slot(act, slot)
                    if bag: fcs.extend(bag.fcurves)
                return fcs
            return getattr(act, "fcurves", [])

        all_fcurves = get_fcurves(target_action)
        for fc in all_fcurves:
            if use_relative:
                # Path format: key_blocks["Name"].value
                match = re.match(r'key_blocks\["(.+)"\]\.value', fc.data_path)
                if match:
                    kb_name = match.group(1)
                    if kb_name in key_blocks_names:
                        idx = key_blocks_names.get(kb_name) # 0-based
                        if idx > 0: # Skip basis
                            fcurve_map[idx - 1] = fc
            else:
                # Path format: eval_time
                if fc.data_path == 'eval_time':
                    eval_time_fc = fc
                    break

        setup_elapsed = time.perf_counter() - start_setup
        benchmark = current_session()
        if benchmark:
            benchmark.record_duration(
                "shape_key_fcurve_setup",
                setup_elapsed,
                animation=name,
                fcurves=len(all_fcurves),
            )

        # 2. Sample
        start_sampling = time.perf_counter()
        scene_fps = scene.render.fps
        if kps <= 0: kps = 1
        frame_step = scene_fps / kps
        num_samples = _animation_sample_count(start, end, frame_step)
        times = start + np.arange(num_samples, dtype=np.float64) * frame_step
        linear_values = _linear_fcurve_samples(eval_time_fc, times) if not use_relative else None

        frames_data = np.empty((num_samples, vertex_count, 3), dtype=np.int16)

        # Prepare Absolute frames lookup if needed
        abs_frame_values = None
        if not use_relative:
            abs_frame_values = np.array([kb.frame for kb in sk_data.key_blocks], dtype=np.float32)

        if not use_relative and num_samples >= 64:
            # Absolute path, large sample count: evaluate simple linear curves
            # in NumPy, then do searchsorted + lerp + quantization in bulk.
            # Complex curves retain Blender evaluation; allocations dominate
            # small animations, which use the scalar interpolation path below.
            vals = np.empty(num_samples, dtype=np.float32)
            if linear_values is not None:
                vals[:] = linear_values
            elif eval_time_fc:
                for i in range(num_samples):
                    vals[i] = eval_time_fc.evaluate(times[i])
            else:
                vals.fill(0.0)

            # Clip to range, then find bracketing shape-key frames.
            np.clip(vals, abs_frame_values[0], abs_frame_values[-1], out=vals)
            idx_right = np.searchsorted(abs_frame_values, vals, side='right')
            idx_left = idx_right - 1
            # Guard against val == last frame -> idx_right == N -> clamp.
            idx_right = np.minimum(idx_right, num_keys_total - 1)
            idx_left = np.minimum(idx_left, num_keys_total - 1)

            f_left = abs_frame_values[idx_left]
            f_right = abs_frame_values[idx_right]
            denom = f_right - f_left
            denom[denom == 0] = 1.0  # avoid div-by-zero on equal frames
            factor = ((vals - f_left) / denom)[:, None, None]

            # co_at[k] = trans_basis if k == 0 else trans_basis + trans_delta[k-1]
            co_left = np.where(
                idx_left[:, None, None] == 0,
                trans_basis[None, :, :],
                trans_basis[None, :, :] + trans_delta[np.maximum(idx_left - 1, 0)],
            )
            co_right = trans_basis[None, :, :] + trans_delta[np.maximum(idx_right - 1, 0)]

            interp_all = co_left + (co_right - co_left) * factor

            frames_data = np.clip(
                np.round(interp_all * 16.0), -32768, 32767
            ).astype(np.int16)
        else:
            for i in range(num_samples):
                t = start + (i * frame_step)

                if not use_relative:
                    # Absolute interpolation (scalar, small sample counts)
                    if linear_values is not None:
                        val = linear_values[i]
                    else:
                        val = eval_time_fc.evaluate(t) if eval_time_fc else 0.0

                    # Find two nearest frames
                    # Optimization: if val is outside range, clip it
                    if val <= abs_frame_values[0]:
                        interp = trans_basis
                    elif val >= abs_frame_values[-1]:
                        interp = trans_basis + trans_delta[-1]
                    else:
                        # Find indices
                        idx_right = np.searchsorted(abs_frame_values, val)
                        idx_left = idx_right - 1

                        f_left = abs_frame_values[idx_left]
                        f_right = abs_frame_values[idx_right]

                        factor = (val - f_left) / (f_right - f_left)

                        # (V, 3)
                        co_left = trans_basis if idx_left == 0 else trans_basis + trans_delta[idx_left - 1]
                        co_right = trans_basis + trans_delta[idx_right - 1]

                        interp = co_left + (co_right - co_left) * factor
                else:
                    # Simple weighted sum (relative mode)
                    current_weights = np.zeros(num_keys_total - 1, dtype=np.float32)
                    for idx, fc in fcurve_map.items():
                        current_weights[idx] = fc.evaluate(t)

                    # (V, 3)
                    interp = trans_basis + np.tensordot(current_weights, trans_delta, axes=([0], [0]))

                # Quantize
                frames_data[i] = np.clip(
                    np.round(interp * 16.0), -32768, 32767
                ).astype(np.int16)

        elapsed = time.perf_counter() - start_sampling
        debug(f"[Timing] bake_range_fast '{name}' sampling took {elapsed:.6f} seconds")
        benchmark = current_session()
        if benchmark:
            benchmark.record_duration(
                "shape_key_bake",
                elapsed,
                animation=name,
                samples=num_samples,
                vertices=vertex_count,
                fcurves=len(fcurve_map),
            )
        return frames_data

    # --- PREPARATIONS FOR BAKE ---
    # Common Matrix for all animations
    mesh_to_arm = np.eye(4)
    if obj.parent and obj.parent.type == 'ARMATURE':
         mesh_to_arm = np.array(obj.parent.matrix_world.inverted() @ obj.matrix_world)
    full_matrix = export_matrix @ mesh_to_arm

    # Fast Path Preliminary Data Extraction
    trans_basis = None
    trans_delta = None
    key_blocks_names = {}
    
    if can_use_fast_path:
        start_sk_gather = time.perf_counter()
        sk_data = obj.data.shape_keys
        key_blocks = sk_data.key_blocks
        num_keys = len(key_blocks)
        key_blocks_names = {kb.name: i for i, kb in enumerate(key_blocks)}
        
        co_blocks = np.empty((num_keys, vertex_count, 3), dtype=np.float32)
        for i, kb in enumerate(key_blocks):
            kb.data.foreach_get("co", co_blocks[i].ravel())
            
        basis = co_blocks[0]
        key_delta = co_blocks[1:] - basis
        
        # Pre-transform
        trans_basis = utils.apply_import_matrix(basis, full_matrix)
        flat_delta = key_delta.reshape(-1, 3)
        linear_matrix = full_matrix[:3, :3]
        trans_delta = (flat_delta @ linear_matrix.T).reshape(num_keys - 1, vertex_count, 3)
        
        extraction_elapsed = time.perf_counter() - start_sk_gather
        debug(f"[Timing] shape_key_data_extraction (pre-bake) took {extraction_elapsed:.6f} seconds")
        benchmark = current_session()
        if benchmark:
            benchmark.record_duration(
                "shape_key_data_extraction",
                extraction_elapsed,
                shape_keys=num_keys,
                vertices=vertex_count,
            )

    try:
        # --- PATH A: NLA TRACKS ---
        if anim_data.nla_tracks:
            debug("Mode: NLA Tracks (Soloing)")
            if not can_use_fast_path:
                anim_data.use_nla = True

            # Evaluated baking solos tracks; direct action sampling needs no NLA mutation.
            state_start = time.perf_counter()
            if not can_use_fast_path:
                for track in anim_data.nla_tracks:
                    track.mute = True
            benchmark = current_session()
            if benchmark:
                benchmark.record_duration(
                    "nla_state_mute_all",
                    time.perf_counter() - state_start,
                    tracks=len(anim_data.nla_tracks),
                )
            
            # Iterate Tracks in REVERSE (Top-most first? or whatever user requested)
            # User requested "reversed order we handle them currently"
            # Usually we want to export the list of animations.
            for track in reversed(anim_data.nla_tracks):
                
                # Solo this track only when Blender evaluates the dependency graph.
                track_start = time.perf_counter()
                if not can_use_fast_path:
                    track.mute = False
                
                for strip in track.strips:
                    action = strip.action
                    if not action: continue
                    
                    # Name
                    anim_name = strip.name
                    # Use the NLA Strip name as the exported animation name.
                    # This allows users to reuse the same Action multiple times (e.g. loops)
                    # or rename animations for export without changing the source Action.
                    clean_name = strip.name
                    
                    # Preserve fractional endpoints created by KPS timing.
                    start = float(strip.frame_start)
                    end = float(strip.frame_end)
                    
                    # KPS/Sound
                    kps = action.get("carnivores_kps", int(scene.render.fps))
                    snd_ptr = resolve_action_sound(action)
                    
                    # Bake
                    if can_use_fast_path:
                        frames = bake_range_fast(clean_name, start, end, kps, action, trans_basis, trans_delta, key_blocks_names)
                    else:
                        frames = bake_range(clean_name, start, end, kps, snd_ptr)
                    
                    if frames is not None and len(frames):
                        animations.append({
                            'name': clean_name,
                            'kps': kps,
                            'frames': frames,
                            'sound_ptr': snd_ptr
                        })
                
                # Re-mute after evaluated processing; direct sampling left it untouched.
                if not can_use_fast_path:
                    track.mute = True
                benchmark = current_session()
                if benchmark:
                    benchmark.record_duration(
                        "nla_track_solo",
                        time.perf_counter() - track_start,
                        track=track.name,
                    )

        # --- PATH B: ACTIVE ACTION (Fallback) ---
        elif anim_data.action:
            debug("Mode: Active Action (No NLA)")
            if not can_use_fast_path:
                anim_data.use_nla = False
            
            action = anim_data.action
            clean_name = action.name.replace("_Action", "")
            start, end = float(action.frame_range[0]), float(action.frame_range[1])
            kps = action.get("carnivores_kps", int(scene.render.fps))
            snd_ptr = resolve_action_sound(action)
            
            # Bake
            if can_use_fast_path:
                frames = bake_range_fast(clean_name, start, end, kps, action, trans_basis, trans_delta, key_blocks_names)
            else:
                frames = bake_range(clean_name, start, end, kps, snd_ptr)
                
            if frames is not None and len(frames):
                animations.append({
                    'name': clean_name,
                    'kps': kps,
                    'frames': frames,
                    'sound_ptr': snd_ptr
                })
                
        else:
            warn("No NLA tracks and no Active Action. No animations exported.")

    except Exception as e:
        error(f"Critical Error during animation bake: {e}")

    finally:
        # --- RESTORE STATE ---
        restore_start = time.perf_counter()
        if used_evaluated_bake:
            scene.frame_set(original_frame)

        if anim_data and not can_use_fast_path:
            try:
                anim_data.action = original_action
            except AttributeError:
                warn("Could not restore active action (likely NLA driven/read-only).")
            
            try:
                anim_data.use_nla = original_use_nla
            except AttributeError:
                warn("Could not restore use_nla state (likely NLA driven/read-only).")
            
            # Restore Mute States
            if original_mute_states:
                for track, state in original_mute_states.items():
                    track.mute = state
        
        # Restore Modifiers
        for mod_name, state in mod_states.items():
            if mod_name in obj.modifiers:
                obj.modifiers[mod_name].show_viewport = state
        
        # Restore Pinning
        if original_show_only_shape_key:
             obj.show_only_shape_key = True

        benchmark = current_session()
        if benchmark:
            benchmark.record_duration(
                "nla_state_restore",
                time.perf_counter() - restore_start,
            )

    return animations

@utils.timed('export_car.serialize')
def export_car(filepath, obj, export_matrix, export_textures=False,
               flip_u=False, flip_v=False, flip_handedness=True,
               model_name_override="", sound_conversion_cache=None):
    
    debug(f"--- Starting .car export to: {filepath} ---")
    session = current_session()
    if session:
        session.add_metadata(format="CAR", filepath=os.path.basename(filepath))
    # 1. Gather Base Mesh Data
    start_mesh = time.perf_counter()
    (vertex_count, face_count, bone_count, texture_size, 
     faces_arr, verts_arr, bones_arr, texture_raw) = gather_mesh_data(
        obj, export_matrix, export_textures, flip_u, flip_v, flip_handedness
    )
    debug(f"[Timing] gather_mesh_data took {time.perf_counter() - start_mesh:.6f} seconds")

    # 2. Gather Animations & Sounds
    start_anim = time.perf_counter()
    anims = gather_car_animations(obj, export_matrix, vertex_count)
    debug(f"[Timing] gather_car_animations took {time.perf_counter() - start_anim:.6f} seconds")
    if session:
        session.add_metadata(
            animations=len(anims),
            animation_frames=sum(len(anim['frames']) for anim in anims),
        )
    if len(anims) > 64:
        warn(
            f"Exporting {len(anims)} animations. Current C2 MEE supports 64 and the fixed "
            "cross-reference table can map sounds only for the first 64 animations."
        )

    start_sound = time.perf_counter()
    sounds_map = {} # Sound DataBlock -> Index in file
    sound_list = [] # List of dicts to write
    cross_ref = np.full(64, -1, dtype=np.int32)
    
    # Process sounds
    for i, anim in enumerate(anims):
        if i >= 64: 
            warn("More than 64 animations, truncation will occur in cross-ref.")
            break
            
        snd = anim['sound_ptr']
        if snd:
            if snd.name not in sounds_map:
                # Convert and add
                data_bytes, length = convert_sound_to_22khz_mono(
                    snd, conversion_cache=sound_conversion_cache
                )
                if data_bytes:
                    idx = len(sound_list)
                    sounds_map[snd.name] = idx
                    sound_list.append({
                        'name': snd.name,
                        'length': length,
                        'data': data_bytes
                    })
                else:
                    sounds_map[snd.name] = -1
            
            cross_ref[i] = sounds_map[snd.name]

    debug(f"[Timing] sound_processing took {time.perf_counter() - start_sound:.6f} seconds")

    # 3. Build Header
    header = np.zeros(1, dtype=CAR_HEADER_DTYPE)
    
    # Model Name
    m_name = model_name_override if model_name_override else os.path.splitext(os.path.basename(filepath))[0]
    # Ensure "msc: #" suffix if not present? 
    # Actually, the user might want to set this exactly.
    # We will truncate to 32 chars.
    header['model_name'] = m_name.encode('ascii', 'ignore')[:32].ljust(32, b'\x00')
    
    header['ani_count'] = len(anims)
    header['sfx_count'] = len(sound_list)
    header['vertex_count'] = vertex_count
    header['face_count'] = face_count
    header['texture_size'] = texture_size # Total bytes

    # 4. Write File
    start_write = time.perf_counter()
    with open(filepath, 'wb') as f:
        # Header
        header.tofile(f)
        
        # Faces
        faces_arr.tofile(f)
        
        # Owners are signed, zero-based indices in the engine format. Owner 0
        # is valid and -1 represents an unowned vertex; no export offset applies.
        verts_arr.tofile(f)
        
        # Texture
        if texture_raw is not None:
            texture_raw.tofile(f)
            
        # Animations
        for anim in anims:
            # Name 32
            f.write(anim['name'].encode('ascii', 'ignore')[:32].ljust(32, b'\x00'))
            # KPS 4
            f.write(struct.pack('<I', anim['kps']))
            # Frames Count 4
            f.write(struct.pack('<I', len(anim['frames'])))
            # Frames Data is kept contiguous so each animation needs one write.
            np.asarray(anim['frames'], dtype='<i2').tofile(f)
                
        # Sounds
        for snd in sound_list:
            # Name 32
            f.write(snd['name'].encode('ascii', 'ignore')[:32].ljust(32, b'\x00'))
            # Length 4
            f.write(struct.pack('<I', snd['length']))
            # Data
            f.write(snd['data'])
            
        # Cross Ref
        cross_ref.tofile(f)
        
    write_elapsed = time.perf_counter() - start_write
    if session:
        session.record_duration(
            "file_write",
            write_elapsed,
            animations=len(anims),
            sounds=len(sound_list),
        )
    info(f"Finished .car export: {filepath}")