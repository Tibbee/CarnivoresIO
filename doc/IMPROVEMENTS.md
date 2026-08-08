# CarnivoresIO Improvement Roadmap

Implementation-oriented proposals for improving CarnivoresIO. This document records intended behavior, constraints, ordering, and verification criteria before code changes are made.

The roadmap starts with the NLA audio system. Add future subsystem proposals as new top-level sections using the same structure.

## Status Labels

| Label | Meaning |
|-------|---------|
| Required | Correctness, lifecycle, or compatibility work that should be implemented first |
| Recommended | Valuable usability or maintainability work after required behavior is stable |
| Optional | Enhancement that needs a confirmed use case or profiling evidence |
| Decision needed | Behavior must be chosen before implementation |

## General Implementation Principles

- Prefer the smallest change that establishes correct behavior.
- Preserve compatibility with persisted `.blend` data unless an explicit migration is provided.
- Keep Blender handlers thin. Put reusable state transitions and lifecycle operations behind focused APIs.
- Do not optimize unmeasured paths. Record a baseline before adding caches or preload behavior.
- Use `utils.logger` instead of `print()` and avoid silent exception handling.
- Make cleanup idempotent so it is safe during unregister, file changes, playback stops, and partial failures.
- Verify changes in every supported Blender version where the relevant API differs.

---

## 1. NLA Audio System

**Implementation status:** Items 1.3.2 (source identity), 1.3.3 (offsets/sync), 1.3.5 (export conversion), 1.3.6 (parse validation), 1.3.7 (lifecycle), 1.3.8 (temp files), 1.4.1 (failure classification), 1.4.2 (backoff), 1.5 (volume), 1.7 (fallback factory caching), and the formerly partial requirements below (preview exclusivity, preview state lifecycle across file load, linked-sound migration) are implemented and committed. All core completion criteria in 1.12 are met; the deferred 1.9 authoring tools remain separate follow-up work. Spatial audio (1.6) is explicitly **out of scope** — Blender is used as an editor/authoring tool where managed timeline playback and VSE strip placement are sufficient; no listener/attenuation policy is needed.

### 1.1 Goals

- Play the sound linked to an explicitly focused animation during extension preview and NLA tweak mode.
- Start, stop, seek, and retrigger sounds predictably as strips change or loop.
- Centralize audio resource ownership and cleanup.
- Preserve sound links created by older versions of the extension.
- Add useful volume controls without changing CAR file semantics.
- Make spatial playback available only when its listener and attenuation behavior are well-defined.

### 1.2 Current Architecture

Audio playback is primarily implemented in `operators/animation.py` and registered from `__init__.py`.

Current module state:

- `_playing_sounds`: active handles keyed by Blender object
- `_aud_device`: lazily created `aud.Device`
- `_is_real_playback`: playback state set by Blender playback handlers
- `_preview_restore_state`: temporary NLA preview and UI restoration data
- `_failed_sound_blocklist`: fixed five-second retry suppression

Temporary files created while importing CAR sounds are tracked separately by `_temp_sound_files` in `utils/animation.py` and are removed only during addon unregister.

Current sound links use `Action.carnivores_sound_ptr`. Older extension versions stored the sound name in the `action["carnivores_sound"]` ID property, and that data may still exist in saved blend files.

### 1.3 Confirmed Problems

#### Required: Focused Playback Policy

**Status: Implemented.** `_resolve_active_source` (`operators/animation.py`) applies priority preview → tweak mode → selected Carnivores track, skips muted tracks/strips, and gates on `scene.carnivores_nla_sound_enabled`. Playback is strictly exclusive: preview plays only the previewed object's action, NLA tweak mode only the active object's focused strip, and ordinary playback only the active object's selected track (previously preview/tweak scanned all scene objects and could start unrelated tracks).

Managed audio intentionally plays only when the user has selected an unambiguous animation source:

1. Extension track preview
2. Blender NLA tweak mode

Normal, unfocused NLA timeline playback must not trigger linked sounds. In the Carnivores workflow, one clip belongs to one animation; evaluating the normal NLA stack can otherwise cascade through unrelated strips and start clips at confusing offsets. The resolver must ignore muted or inactive tracks and strips and return enough source identity to distinguish explicit preview selections, not only a sound name.

NLA strip scaling is intentionally outside managed audio synchronization. Linked clips retain their authored speed and are not pitch-shifted, time-stretched, or reversed. For defensive compatibility with nonstandard workflows, focused playback restarts the authored clip at detected NLA repeat boundaries; this is best-effort behavior and does not guarantee synchronization for scaled or fractional repeats. Audio modders should prepare a clip matching the animation's intended frame range and KPS. See [Deferred Workflow Enhancements](#19-deferred-workflow-enhancements) for planned export and timing-assistance tools.

#### Required: Preview Exclusivity

**Status: Implemented.** `start_or_sync` (`operators/animation.py`) now restricts candidate objects during preview to the previewed object only, and during NLA tweak mode to the active object only. Other objects' selected, unmuted tracks can no longer start playing during a preview or tweak session. Previously the handler scanned `scene.objects` while `_preview_restore_state` was set, so every other scene object's focused Carnivores track also played.

#### Required: Preview State Lifecycle Across File Load

**Status: Implemented.** `_clear_preview_state()` — called from the `load_post` handler (`clear_aud_device_on_new_file`) and from `unregister_audio_handlers` — removes the dynamically registered `preview_loop_handler` from `frame_change_post` and discards `_preview_restore_state`. Loading a file or disabling the addon mid-preview can no longer leak preview mode, stale frame-range restoration, or all-object audio scanning into the next file.

#### Required: Source Identity and Retriggering

The current state compares only object and sound name. This fails when consecutive actions or strips use the same sound because the second source is treated as unchanged.

Each active playback record should identify at least:

- Object
- Action
- Focused NLA strip or preview source
- Sound datablock
- Handle, when one is actively playing

A completed handle must not restart every frame while its source remains active. Retain a triggered/completed state until one of these events occurs:

- The explicitly focused source changes
- Extension preview explicitly loops
- Playback restarts under a documented restart policy

#### Required: Playback Offset and Synchronization

Starting focused playback in the middle of an unscaled action sub-range should use the corresponding authored audio offset. Account for strip start and `action_frame_start`. Strip scaling, reverse audio, and pitch-preserving time stretching are deliberately unsupported. Repeat-boundary restarts are a defensive convenience, not a synchronization guarantee.

Use `aud.Handle.position` only for playback time in seconds. It is not a spatial coordinate.

Synchronization behavior must be defined for:

- Starting at the strip beginning
- Starting in the middle of a strip
- Timeline jumps during playback
- Preview loops
- Repeated NLA strips
- Scaled or reversed strips
- Playback stop and restart

Small continuous drift correction is not required initially. First ensure deterministic start offsets and loop transitions. Add resynchronization only if manual testing demonstrates audible drift.

#### Required: Linked Sound Resolution and Migration

**Status: Implemented.** `resolve_action_sound()` (`utils/animation.py`) is the single resolver used by playback, preview, export, and validation. A load-time migration (`migrate_legacy_sound_links()` in `operators/animation.py`, run from the `load_post` handler) assigns `carnivores_sound_ptr` whenever a legacy `action["carnivores_sound"]` name resolves; unresolved legacy names are retained so a missing datablock can be repaired later. `CARNIVORES_OT_play_linked_sound` uses the shared resolver.

Create one helper that resolves an action's linked `bpy.types.Sound`:

1. Return `action.carnivores_sound_ptr` when set.
2. Otherwise resolve the legacy `action["carnivores_sound"]` name through `bpy.data.sounds`.
3. Return `None` if neither resolves.

Use the helper in playback, preview, export, UI operators, and validation where applicable. In particular, `CARNIVORES_OT_play_linked_sound` currently checks only the legacy string while current imports assign the pointer.

Do not remove legacy fallback support without a migration. An optional load-time migration may assign the pointer when the legacy name resolves. It should retain unresolved legacy data so a missing datablock can be repaired later.

**Decision (authoring tool):** `CARNIVORES_OT_play_linked_sound` is retained as an explicit VSE authoring operator, renamed to **Add Sound Strip to Sequencer**. It resolves the active (or panel-selected) animation's linked sound through the shared resolver and inserts a sound strip aligned with the animation's NLA timeline position (falling back to the current frame), so audio plays in sync while scrubbing or rendering. It does not play audio and is not a second playback architecture; Blender's VSE is used for trimming/fades/rough sync only, with high-quality retiming and restoration left to external editors (Audacity/DAW). The operator resolves animation data through `get_active_animation_data()`, so it works for CAR imports stored on shape-key animation data.

#### Required: CAR Sound Export Conversion

`convert_sound_to_22khz_mono()` must guarantee that every successful result is contiguous, mono, signed 16-bit PCM at 22050 Hz before bytes are written.

Current conversion concerns:

- `aud.Sound.data()` is documented to return a two-dimensional floating-point NumPy array. Blender 5.2 returns `float32`, but the current `int32` branch performs no conversion.
- If an unexpected four-byte sample type reaches the writer, `data.tobytes()` writes four bytes per sample while the current `len(data) * 2` calculation declares only two. This corrupts the CAR layout rather than dropping the samples.
- `factory.limit(0, 100000)` uses seconds, not samples. It is a roughly 27.8-hour cap, not a 4.5-second cap. It is unexplained and should not be treated as an engine duration limit.

Conversion requirements:

- Resample to 22050 Hz and rechannel to one channel.
- Validate the returned array shape and sample type.
- Clip floating-point samples to the supported range before conversion to `np.int16`.
- Convert any intentionally supported non-floating sample representation explicitly rather than passing it through.
- Make the final array contiguous and flatten the mono channel before serialization.
- Derive the declared byte length from the final payload or `data.nbytes`, not an assumed element width.
- Reject empty, non-finite, malformed, or unrepresentable results with a clear export warning or fatal validation error, as appropriate.
- Ensure the byte length is even and fits the CAR sound block's unsigned 32-bit length field.

Remove the 100,000-second `limit()` call unless a real need for bounding procedural or effectively infinite sound sources is demonstrated. If a safety bound is needed, expose it as explicit validation with a documented value and diagnostic; do not silently truncate to an unexplained constant. Do not add an engine-specific maximum-duration warning until that limit is established from engine behavior or source documentation.

#### Required: CAR Sound Parse Validation

CAR sound blocks declare a byte length followed by signed 16-bit PCM. Parsing must validate this length before reading samples.

- Reject or explicitly repair odd byte lengths. Reading `length // 2` samples without consuming or rejecting the extra byte misaligns all following sound and cross-reference data.
- Check the declared length against the remaining file size before reading.
- Report truncated payloads through `ParserContext.warnings`; do not continue from an ambiguous file position.
- Confirm that the cross-reference table still has its required 256 bytes after all sound blocks.
- Preserve the current validation behavior that clamps invalid sound indices to `-1`.

Add round-trip coverage using both embedded CAR sounds and externally linked Blender sounds: export, parse the result, compare sound count and cross-reference mappings, and confirm each PCM payload has the declared length.

#### Required: Complete Audio Lifecycle

Introduce one audio manager instance responsible for runtime audio resources. It does not need a singleton class pattern; one module-owned, explicitly constructed instance is sufficient.

The manager should own:

- Lazy device creation
- Active playback records
- Real playback state
- Sound-specific retry state
- Device retry/reset state
- Optional fallback factory cache

The current OpenAL recovery cooldown attempts to assign `scene.carnivores_last_audio_reset` even though no RNA property is registered. Blender raises `AttributeError`, and the surrounding bare exception hides it, so the cooldown is not retained. Keep this transient cooldown in the manager and measure it with the injected monotonic clock. It must not be stored in the blend file.

Keep NLA preview UI restoration state outside the audio manager unless the manager is also made explicitly responsible for muting tracks, restoring frame ranges, and controlling playback. Avoid creating one class with unrelated UI and audio responsibilities.

The manager API should include idempotent operations equivalent to:

```python
get_device()
start_or_sync(...)
stop_object(obj)
stop_all()
reset()
on_file_load()
```

`reset()` must attempt to stop every handle, call `Device.stopAll()`, release the device reference, clear runtime records, clear retry state, and invalidate fallback caches. One failed cleanup operation must not prevent the remaining cleanup steps.

Critical device recovery must use this reset path. Do not clear handle references and replace `_aud_device` without first attempting to stop the old resources.

Playback records must tolerate Blender objects being removed while playback is active. Current cleanup can access `obj_playing.name` on an invalid RNA proxy before entering its exception handler. Cleanup must not require dereferencing an owner merely to stop its handle. Treat `ReferenceError` as an expected lifecycle condition, and keep diagnostic naming separate from resource identity where practical.

For testability, allow injection of the device factory and monotonic clock. Extract active-source selection and timing calculations into functions that do not create devices or mutate Blender data.

#### Required: Temporary Sound File Lifecycle

Move temporary sound tracking behind shared cleanup functions or a focused sound-resource owner. Use that cleanup from both file-change handling and addon unregister.

Requirements:

- Stop and release audio resources before deleting files that may still be open.
- Remove files from the tracking set only after processing them.
- Treat missing files as already cleaned.
- Log deletion failures with `warn()` rather than silently ignoring them.
- Clear old-session paths after a new blend file is loaded.
- Never delete a path unless the extension created and tracked it.

Longer term, prefer uniquely named files in a dedicated temporary directory rather than unpacking into the project's `sounds/` directory. Confirm that packed CAR sounds remain playable before removing the existing unpack-and-repack workaround.

Do not assume that one `Sound.pack()` call makes the current unpack-and-repack workaround redundant. Before simplifying the flow, verify:

- Immediate playback after CAR import
- Playback after the original temporary WAV is deleted
- Save, close, and reopen playback
- CAR re-export after reopening
- CAR export and re-import with the same PCM length and mapping
- Behavior across the minimum and maximum supported Blender versions

Manually linked external sounds have different persistence semantics from embedded CAR sounds. Do not use the manual import operator's intentionally unpacked behavior as evidence that imported CAR sounds should also remain external.

Process crashes can still leave files behind. A dedicated extension temporary directory would allow conservative stale-file cleanup on startup, but this is optional and must avoid deleting files owned by another running Blender process.

#### Required: Handler Registration and File-Load Behavior

Centralize handler setup and teardown in `register_audio_handlers()` and `unregister_audio_handlers()` or equivalent functions.

- Registration must be idempotent.
- Unregistration must remove all extension audio handlers and stop audio resources.
- Persistent handlers normally survive blend-file loads, so the load handler should reset resources rather than repeatedly re-register handlers.
- If defensive re-registration remains necessary for a verified Blender behavior, call the same centralized registration function instead of duplicating membership checks.
- Do not force `Scene.carnivores_nla_sound_enabled` to `True` on file load. Preserve the value stored in the blend file.
- Confirm the callback signatures for each supported Blender version. `load_post` does not provide a scene object in the same way as frame-change handlers.

### 1.4 Failure Handling

#### Recommended: Classify Failures

Track sound-specific failures separately from device failures.

Sound-specific examples:

- Missing sound datablock
- Missing external file
- Invalid or unsupported audio data
- Failure to create a factory

Device examples:

- OpenAL buffer errors
- Device initialization failure
- Handle creation failure caused by the backend

Missing factories currently produce a warning on every frame because only thrown exceptions enter the blocklist. Every persistent failure path should be rate-limited.

#### Recommended: Bounded Exponential Backoff

Use a retry record containing attempt count, next retry time, and optionally the last error. A suitable initial policy is 5, 10, 20, 40, then 60 seconds maximum.

- Reset a sound's retry record after successful factory creation and playback.
- Reset all retry records on file load.
- Use a monotonic clock rather than wall-clock time.
- Log the first failure and meaningful state changes, not every skipped frame.
- Do not let a sound-specific decode failure repeatedly reset the global audio device.

### 1.5 Volume Controls

#### Recommended: Scene and Action Volume

Register persistent preview properties:

```python
bpy.types.Action.carnivores_sound_volume = bpy.props.FloatProperty(
    name="Sound Volume",
    description="Volume multiplier for this animation's preview sound",
    default=1.0,
    min=0.0,
    max=2.0,
)

bpy.types.Scene.carnivores_nla_sound_volume = bpy.props.FloatProperty(
    name="NLA Sound Volume",
    description="Master volume for Carnivores NLA sound preview",
    default=1.0,
    min=0.0,
    max=2.0,
)
```

Set the handle volume from the RNA properties:

```python
handle.volume = (
    action.carnivores_sound_volume
    * scene.carnivores_nla_sound_volume
)
```

Do not use `action.get()` to read a registered RNA property. Add both properties to unregister cleanup and expose them next to the existing sound controls.

Volume changes should update an already-playing handle. Use focused property update callbacks or update managed handles without restarting playback.

These values control Blender preview only. CAR export must not imply or serialize per-action volume unless the file format is later confirmed to support it.

### 1.6 Spatial Audio

#### Optional: Camera-Relative 3D Playback

**Decision: Out of scope — not useful for this project's use of Blender as an editor/authoring tool.** The extension is used to import/author models and verify animation/audio alignment, not to simulate in-game audio. Managed timeline playback and VSE sound-strip placement cover the authoring workflow; no listener/attenuation policy is needed. Do not implement without a confirmed use case.

Carnivores uses spatialized sounds in-game, but Blender preview currently has no listener policy. Spatial audio should be implemented only after the desired preview behavior is selected.

Correct Audaspace properties include:

```python
handle.location = tuple(obj.matrix_world.translation)
handle.relative = False
```

`handle.position` is the playback cursor and must never receive object coordinates.

A complete implementation also needs to:

- Update `handle.location` while the source object moves.
- Set `device.listener_location` from the selected listener.
- Set `device.listener_orientation` using the correct quaternion convention.
- Update listener velocity only if Doppler behavior is wanted.
- Choose and document a distance model.
- Configure reference distance, maximum distance, and attenuation.
- Define fallback behavior when no listener exists.

Recommended initial policy:

- Add a scene toggle for spatial preview, defaulting to off to preserve current behavior.
- Use `scene.camera` as the listener.
- Fall back to non-spatial playback when no scene camera exists.
- Use Blender world coordinates directly because imported objects have already been converted into Blender's handedness.

Do not add spatialization as part of the core lifecycle refactor. Implement and verify it separately so changes in loudness or audibility are easy to diagnose.

### 1.7 Factory Loading and Caching

#### Optional: Cache Only Proven Expensive Fallbacks

**Status: Implemented.** The `AudioManager` caches only fallback factories (when `bpy.types.Sound.factory` fails), keyed by datablock identity + resolved path + mtime + packed size, with stale-entry eviction and invalidation on file load and reset. Blender's native factory is never cached. First-trigger latency preloading and per-frame candidate caches remain unimplemented by design (profiling first).

`bpy.types.Sound.factory` already exposes Blender's sound factory. Do not add a second cache for every sound unless profiling demonstrates a benefit.

If direct fallback loading through `aud.Sound.file()` causes repeated work, cache only those fallback factories. The key must include stable sound identity and resolved filepath, not only the sound name. Invalidate the cache when:

- The filepath changes
- The sound datablock is removed or replaced
- A blend file is loaded
- The audio manager resets
- The addon unregisters

Lazy caching does not eliminate first-trigger latency. If measured first-play stutter is significant, preload only sounds relevant to the current scene when playback starts or immediately after import. Measure startup cost and retained memory before keeping this behavior.

The per-frame handler currently scans `scene.objects`, but existing measurements place the complete handler around 0.1 to 1 ms. Do not introduce a candidate cache until profiling demonstrates a meaningful problem in representative large scenes. A simple `obj.animation_data` filter is not correct because Carnivores animation data may be stored on shape keys or a parent armature. Any optimization must preserve `get_active_animation_data()` behavior and define cache invalidation.

### 1.8 Focus and Timing Policy

#### Focused source selection

The runtime supports one managed sound per object and requires explicit focus. Extension preview selects the requested action; NLA Tweak Mode selects the focused strip; ordinary timeline playback uses the active object's track selected in the Carnivores Animation panel. Muted selected tracks remain silent. Other overlapping strips are intentionally ignored, so influence and blend-stack arbitration are not part of the audio manager.

#### Authored timing

Linked audio plays at its authored speed. The manager does not alter `handle.pitch`, time-stretch audio, or reverse it. In focused mode it may restart the original clip at a detected NLA repeat boundary as a defensive fallback; it does not stretch the clip to fit that cycle. This preserves the asset supplied by the audio modder and avoids introducing DSP dependencies or pitch distortion. KPS-aware export and timing assistance are planned in [Deferred Workflow Enhancements](#19-deferred-workflow-enhancements).

### 1.9 Deferred Workflow Enhancements

These improvements deliberately keep audio authoring separate from core CAR import/export and preview playback.

Carnivores associates one sound with one animation. The linked sound is treated as an asset authored for that animation's original frame range and KPS. Audio should be prepared to match the animation's intended duration before it is linked. CAR export performs only the format conversion required by the engine: 22050 Hz, mono, signed 16-bit PCM.

#### Phase 1: Export Linked Audio

Add tools that make imported and linked sounds easy to take into an external audio editor.

##### Selected-action export

Expose an **Export Linked Audio** button beside the selected action's Sound control.

Requirements:
- Resolve both current pointer links and legacy sound-name links.
- Support packed sounds imported from CAR and externally linked Blender sounds.
- Export an editable WAV without changing or unpacking the source datablock permanently.
- Use a sanitized, collision-safe filename derived from the action and sound names.
- Report missing links, unreadable data, and write failures through the operator report and extension logger.

##### Batch export

Add an **Export All Linked Audio** operator with a directory picker.

Requirements:
- Export each unique linked sound once by default.
- Optionally name files by action when the same sound is intentionally assigned to multiple actions.
- Continue after individual failures and provide a summary.
- Never overwrite files silently; provide an overwrite policy or generate unique names.
- Preserve an action-to-exported-file manifest when useful for larger projects.

##### Output modes

Two explicit modes:
- **Editable WAV**: preserve the decoded source sample rate and channels where Blender/Audaspace exposes them reliably.
- **CAR-ready WAV**: 22050 Hz, mono, signed 16-bit PCM, matching export conversion.

The editable mode should be the default. If exact source encoding cannot be preserved, the UI must describe the output as decoded WAV rather than an original-file extraction.

#### Phase 2: Timing Assistance

Provide non-destructive information before considering audio processing:
- Display animation frame count, effective KPS, and calculated duration.
- Display linked audio duration.
- Show the duration difference and whether the audio is shorter or longer.
- Provide copyable target-duration information for external editors.
- Allow users to relink a finished WAV easily after editing.

These tools help audio modders synchronize assets without making CarnivoresIO an audio editor.

#### Phase 3: Basic Audio Editing (Deferred)

KPS-aware audio tools may be reconsidered only after the export and timing workflow is proven useful. Any implementation must be optional and non-destructive.

Potential workflow:
1. Calculate target duration from animation frame range and KPS.
2. Create a new processed WAV and Blender Sound datablock.
3. Preserve the original sound and link unless the user explicitly replaces it.
4. Clearly separate preview processing from the PCM embedded during CAR export.

Pitch-preserving time stretching requires dedicated DSP such as WSOLA, a phase vocoder, SoundTouch, or Rubber Band. Blender/Audaspace pitch controls change speed and pitch together and are not an acceptable transparent solution. A third-party dependency should not be added without confirming supported Blender platforms, licensing, packaging size, quality, and maintenance cost.

Blender's Video Sequence Editor can assist with trimming, fades, mixing, and rough synchronization, while dedicated applications such as Audacity or a DAW remain the recommended tools for high-quality retiming and restoration.

#### Other Deferred Items

- Confirm whether Blender's pointer-property control already makes clearing a linked sound discoverable before adding a dedicated Remove Sound operator.
- Add missing-sound export diagnostics for unresolved legacy links, missing files, and failed PCM conversion.
- Add batch assignment or clearing of sounds for selected animations if repeated manual assignment is a demonstrated workflow problem.
- Add a sound debug report showing action-to-sound links, exported sound indices, cross-reference entries, source format, sample rate, channels, and converted byte length.
- Consider NLA sound-range visualization only after normal playback and timing behavior are stable.
- Define whether a pitch property affects Blender preview only or bakes a distinct exported PCM payload before exposing it. CAR does not store an independent per-animation pitch value.

#### Audio Export Verification

- Packed CAR sounds export after import and after save/reopen.
- External WAV, MP3, OGG, and FLAC links export to editable WAV where Blender supports decoding them.
- CAR-ready output is contiguous mono `int16` PCM at 22050 Hz.
- Batch export handles duplicate links and filename collisions deterministically.
- A failed sound does not prevent other sounds from exporting.
- Export does not alter source datablocks, links, or packed state.

#### Timing Assistance Verification

- Displayed animation duration matches frame range divided by KPS.
- Audio duration is reported consistently for packed and external sounds.
- Changing KPS updates timing information but does not modify audio.
- Relinking edited audio preserves the action association and CAR round-trip mapping.

### 1.10 Suggested Implementation Sequence

#### Phase A: Correctness and Compatibility

1. Add the centralized linked-sound resolver.
2. Fix `CARNIVORES_OT_play_linked_sound` and all duplicated resolution paths.
3. Make CAR sound export produce validated mono `int16` payloads with exact byte lengths.
4. Validate sound-block lengths and cross-reference availability during parsing.
5. Extract focused preview and tweak-mode source resolution.
6. Track source identity separately from sound identity.
7. Implement deterministic start offsets and explicit loop/retrigger behavior.

**Status: All implemented.** The VSE authoring operator was kept (renamed **Add Sound Strip to Sequencer**) per the decision in [Linked Sound Resolution](#required-linked-sound-resolution-and-migration); load-time legacy link migration was added with the `load_post` handler.

#### Phase B: Resource Ownership

1. Introduce the audio manager and route all handle/device operations through it.
2. Consolidate stop, reset, critical recovery, file-load, and unregister cleanup.
3. Move the device-reset cooldown into manager runtime state.
4. Make playback cleanup safe for removed Blender objects.
5. Centralize handler registration.
6. Consolidate temporary-file cleanup.
7. Remove direct access to audio manager internals from `__init__.py` and operators.

**Status: All implemented.**

#### Phase C: Reliability and Usability

1. Classify sound and device failures.
2. Apply bounded retry backoff to every persistent failure path.
3. Add scene and action volume controls.
4. Update current handles when volume changes.

**Status: All implemented.**

#### Phase D: Measured or Optional Enhancements

1. Profile first-play latency and fallback factory loading.
2. Add narrowly scoped fallback caching or preloading only if justified.
3. Test whether packed-sound handling can be simplified without breaking persistence.
4. Profile scene-object scanning before adding candidate caches.
5. Add optional spatial audio after listener and attenuation decisions are settled.

**Status:** Fallback factory caching (item 2) is implemented; preloading remains unprofiled. Item 5 is explicitly out of scope — see [Spatial Audio](#16-spatial-audio).

### 1.11 Verification Matrix

A Blender test harness exists under `tests/` (101 tests across 10 files; see `tests/README.md`), but audio playback remains mostly manual: each implementation phase must include Blender verification. Pure source-resolution and timing functions should receive unit tests in that harness.

#### Playback Modes

- Dedicated track preview starts, loops, switches tracks, and restores state.
- NLA tweak-mode playback starts and stops the linked sound.
- Normal unfocused NLA playback does not trigger managed linked audio.
- Timeline scrubbing while playback is stopped does not trigger sound.
- Disabling NLA sound stops managed handles immediately.

#### Transitions and Timing

- Two consecutive actions with different sounds transition once.
- Two consecutive actions with the same sound retrigger at the source boundary.
- Playback started in the middle of a strip begins at the expected audio offset.
- Focused NLA repeat restarts the authored clip once per detected visual cycle without time stretching.
- A naturally completed sound does not restart every frame.
- Timeline jumps do not leave obsolete handles playing.

#### Lifecycle

- Playback stop clears active handles.
- Opening a different blend file stops old handles and releases the old device.
- Loading a file preserves its saved NLA sound-enabled setting.
- Disabling the extension removes handlers and temporary files.
- Re-enabling the extension does not duplicate handlers.
- A failed cleanup call does not prevent the rest of reset from completing.
- Deleting an object during playback does not raise `ReferenceError` or leave its handle playing.
- Repeated OpenAL failures respect the manager's reset cooldown.
- Loading a file mid-preview leaves no preview loop handler, restore state, or active handles in the new file.
- Preview plays only the previewed object's action; other objects' selected tracks stay silent during preview and during NLA tweak mode.

#### Compatibility

- Newly imported CAR actions use `carnivores_sound_ptr`.
- Legacy actions containing only `action["carnivores_sound"]` still play and export.
- An unresolved legacy sound name remains repairable.
- Packed imported CAR sounds survive save, close, and reopen.
- External WAV, MP3, OGG, and FLAC links retain current supported behavior.

#### CAR Sound Round Trip

- Exported payloads are mono signed `int16` PCM at 22050 Hz.
- Every sound's declared length equals its serialized payload length and is even.
- Export followed by parse preserves sound count and animation cross-reference mappings. Automated round-trip coverage: `tests/test_performance_optimizations.py::test_car_sound_round_trip_preserves_count_mapping_and_payloads`.
- Odd, oversized, or truncated input lengths produce deterministic validation results without misaligning later sections.
- Long finite sounds are not silently limited by the old 100,000-second magic constant.
- Save, close, reopen, and re-export preserves embedded CAR sounds.
- Any simplification of pack handling passes the same matrix on all supported Blender versions.

#### Failure Recovery

- Missing files do not log once per frame.
- Invalid audio enters bounded retry without resetting a healthy device.
- Device initialization failure retries without blocking Blender playback controls.
- A successful retry clears prior failure state.
- The device-reset cooldown is transient manager state and is not serialized into the scene.

#### Volume and Spatial Options

- Scene and action volume multiply correctly and update live handles.
- Volume settings do not alter exported CAR data.
- If spatial audio is implemented, moving source and camera updates positioning.
- Spatial mode without a scene camera follows the documented fallback.

### 1.12 Completion Criteria

The core audio improvement is complete when:

- All supported focused playback modes use one linked-sound resolver and one audio resource owner.
- Extension preview and NLA tweak mode select one explicit animation source, while normal unfocused NLA playback remains silent.
- Focused-source transitions, supported midpoint starts, and repeat-boundary restarts have deterministic behavior; scaled repeats do not imply audio synchronization.
- File load and unregister leave no managed handles, stale device, retry state, preview state, or tracked temporary files.
- Legacy sound links continue to work or are safely migrated.
- CAR sound conversion always writes validated PCM with an exact declared byte length.
- Malformed sound lengths cannot silently misalign subsequent CAR sections.
- Persistent failures are rate-limited without hiding successful recovery.
- Manual verification results are recorded for supported Blender versions.

**Status: All core criteria met in the working tree** (preview exclusivity, preview lifecycle cleanup, and legacy migration landed together with the round-trip test). Spatial audio is out of scope by decision; the deferred 1.9 authoring tools (Export Linked Audio, batch export, timing assistance) remain separate follow-up work. Caching is implemented for fallback factories only.

---

## 2. Future Improvement Areas

Add future proposals here as independent top-level sections. Each should include:

- Current behavior and confirmed problem
- Compatibility and file-format constraints
- Required decisions
- Ordered implementation phases
- Verification matrix
- Completion criteria

Potential areas already mentioned in the development roadmap include export validation, animation workflow, face-flag editing, texture handling, bone-name resolution, performance profiling, and automated testing. Their detailed plans should be added only after the current implementation and behavior have been re-evaluated.

---

## 3. Import/Export Robustness and Data Integrity

Findings from a code audit (parsers, operators, animation, tests) cross-checked against all roadmap documents. None of these are covered by the rig, UI, audio, or performance plans. Each item records the confirmed behavior at the time of writing (HEAD `fab49f4`); verify current line references before editing.

### 3.1 Goals

- No export may silently drop or corrupt animation data while reporting success.
- Every diagnostic that affects output must reach the operator report.
- Imports and exports must be undoable and must not mutate scene state without disclosure.
- Binary output must be atomic and data-preserving (import→export round trips must not lose declared bits).
- Parsers, validators, and exporters must agree on the same sanitization rules.
- Documented format coverage must match actual support.

### 3.2 Confirmed Problems

#### Required: CAR Export Must Not Silently Drop Animations

`parsers/export_car.py`:

- `bake_range()` returns `None` when the evaluated frame's vertex count differs from the base mesh (`:296-300`; console-only `error()`).
- `if frames is not None and len(frames):` silently skips that animation (`:614`).
- The outer `except Exception` around the whole NLA/action bake swallows ALL exceptions (for example `to_mesh()` failure or NumPy errors in the fast path) with a console-only log, then continues (`:662-663`).

The operator report counts the file as exported successfully, so a `.car` can be written missing animations with no warning in the user report — silent data loss. The identical condition in `parsers/export_vtl.py` raises `ValueError` and fails the whole export loudly (`:163-165`, `:284-286`), so CAR and VTL offer contradictory failure contracts for the same data condition.

#### Required: Resync Animation Must Preserve Absolute Mode

`operators/animation.py:1316` calls `keyframe_shape_key_animation_as_action(..., frame_start=1, kps=kps, scene_fps=...)` without `use_absolute`. With absolute shape keys imported (the default), the action is rebaked into relative-style `key_blocks[...].value` fcurves while `sk_data.use_relative` stays `False`. On export, `bake_range_fast` (`parsers/export_car.py`, `use_relative = sk_data.use_relative`) finds no `eval_time` fcurve → `vals.fill(0.0)` → every frame exports the basis pose. The result is a silently static export after a successful Resync operation.

#### Required: Shape-Key Fast Path Must Respect Topology-Changing Modifiers

`can_use_fast_path` in `parsers/export_car.py:224-231` and `parsers/export_vtl.py:117-120` excludes only `{'ARMATURE', 'HOOK', 'CLOTH', 'SOFT_BODY'}`. Any other visible modifier (Subdivision Surface, Mirror, Decimate, Shrinkwrap, ...) passes the filter:

- CAR: the fast path samples raw shape-key deltas (`trans_basis`/`trans_delta`), so exported animation ignores the visible deformation the user sees — wrong-but-plausible data, no error, no warning.
- VTL: `vertex_count` comes from the evaluated mesh while `trans_basis` comes from base-mesh shape keys → array shape mismatch inside `bake_range_fast` (`export_vtl.py:239`) → cryptic NumPy exception, export fails.

Neither path handles the case correctly or explains it.

#### Required: ARGB1555 Alpha Bit Preservation

`parsers/parse_3df.py:78-81` shifts bit 15 out (`>> 10`) and sets alpha to `np.zeros_like(r)` — the 1-bit alpha is discarded on import. `utils/io.py:968-973` forces `a = 0` on export, so the bit is never written. Files that use the alpha bit (relevant to the `sfOpacity` alpha-tested cutout flag, `doc/reference.md`) lose it on import, and re-export after import permanently zeroes it. Import→export is not data-preserving for the texture's declared alpha bit.

#### Required: Empty-Texture Import Safety

A `texture_size = 0` file parses to an empty `(0, 4)` array (reshape skipped, `parsers/parse_3df.py:67-87`). The only guard in `operators/io.py:580-584` and `:1484-1488` is `texture is not None`, which an empty ndarray passes, so `utils/io.py:760-775` calls `bpy.data.images.new(width=256, height=0)` followed by `update()/pack()/reload()`. Untextured models are a documented common case (AGENTS.md testing checklist), and no test covers import with `parse_texture=True` on `texture_size=0` (tests use `parse_texture=False`). Default-settings import of an untextured file may raise or create a degenerate image — untested and unguarded.

#### Required: Bone-Name Sanitization Consistency

- `parsers/parse_3df.py:53-54` only `rstrip('\x00')`, so an embedded NUL mid-name (for example `b"Root\x00junk"`) survives into vertex-group/armature names.
- `parsers/validate.py:295` splits at the first NUL for duplicate detection, so validation and the actual imported names disagree on what the name is.
- Truncation is by characters (`name[:32]`, `parse_3df.py:58-59`), not bytes; a long name with multibyte characters can pass validation yet be silently truncated to 32 bytes on re-export.
- `parse_car_header` (`parse_car.py:23`) does split at the first NUL, so `.3df` and `.car` disagree on the same rule.

Malformed names pass validation but import differently than validated; round-trip renames are nondeterministic.

#### Required: 3DN Export int16 Parent Range Guard

`parsers/export_3dn.py:20-21` has no equivalent of the 3DF/CAR guard (`parsers/export_3df.py:39-40`, `bone_count > np.iinfo(np.int16).max + 1`). `BONE_DTYPE.parent` is `'<i2'` (`core/core.py`), and `gather_3dn_data` fills `bones_arr['parent']` directly (`export_3dn.py:95`). Above 32,767 bones, parent indices silently wrap negative, producing an invalid hierarchy. `utils/validation.py` has no 3DN bone-count preflight check either.

#### Required: Undo Support for I/O Operators

Every I/O operator declares `bl_options = {'PRESET'}` only (`operators/io.py:341, 676, 941, 1144, 1574, 1787`). Imports create collections, meshes, objects, packed images, materials, vertex groups, hooks/armatures, shape keys, actions, NLA strips, and sound datablocks; exports mutate scene state during CAR/VTL baking (`scene.frame_set`, NLA mute toggles, modifier `show_viewport`, `show_only_shape_key`). A single Ctrl+Z cannot remove an import or restore pre-export state; users must manually delete dozens of datablocks. Blender's built-in file importers use `{'REGISTER', 'UNDO'}`. The flag operators already use `{'REGISTER', 'UNDO'}`.

#### Required: Import Must Not Silently Replace the Scene World

`operators/io.py:641-642` (3DF) and `:1531-1532` (CAR) call `setup_custom_world_shader()` on any successful batch with textures+materials; `utils/io.py:861-906` creates or reassigns `bpy.context.scene.world = "CustomWorld"` and rebuilds its node tree. Importing a model changes the scene's lighting/environment without disclosure and cannot be undone.

#### Recommended: Atomic File Writes

All exporters stream directly to the destination path (`export_3df.py:160-166`, `export_car.py:788-824`, `export_3dn.py:118-126`, `export_vtl.py:344-354`). On disk-full, I/O error, or interruption mid-write, the user's previous valid file is replaced by a truncated file with no recovery path. No temp-file + atomic-rename pattern exists.

#### Recommended: All Diagnostics Must Reach the Operator Report

A subset of diagnostics bypasses the report system (`utils/reporting.py` + `_finalize_operation_report`):

- `parsers/parse_3df.py:60,63` — bone-name truncation/non-ASCII cleaning uses module-level `warn()` instead of `context.warnings`, so these never appear in the import report.
- `parsers/export_car.py:339` (static animation), `:662-663` (bake error), `:731-735` (>64 animations), `:745` (cross-reference truncation) — all console-only; the export report still says "Exported successfully."

CAR export in particular can warn about truncated animation/sound mapping and still report total success.

#### Recommended: Failed-Import Datablock Cleanup

`operators/io.py:260-274` (`_remove_failed_import_collection`) deletes only the collection; objects are merely unlinked (`do_unlink=True`), and images/materials/hooks/armatures (3DF path `:579-606`) and shape keys/actions/sounds (CAR path `:1444-1483`) are created before later steps can fail. A failed import leaves orphaned mesh objects, images, materials, actions, and sound datablocks (visible under Orphan Data in 4.x). The UI roadmap documents the collection cleanup requirement only; the datablock leak is not covered.

#### Recommended: Collision-Safe Keying for Export Maps

- `_strip_cycles` keyed `(obj, strip.name)` (`utils/animation.py`) collides for same-named strips on different tracks of one object.
- `export_car`'s `sounds_map` keyed by sound datablock name: two different sounds sharing a name collapse to the first index.

#### Recommended: Format Coverage Documentation

- No `import_vtl` operator exists, and no doc mentions it (the `.3dn` import absence is noted only in `doc/RIG_RECONSTRUCTION_PLAN.md`, a rig-focused doc).
- The README claims `.3df`, `.car`, `.3dn` "importing/exporting" support without qualification (`doc/README.md`). Users and maintainers have no documented statement of what is import-supported.

### 3.3 Testing Blind Spots

No test coverage exists for: `.3dn` export, `.vtl` export, ARGB1555 texture conversion (including the alpha bit), empty-texture import (`texture_size = 0` with `parse_texture=True`), face-flag UI operators, reporting/report datablocks, preset deployment, and `parse_3df` edge cases (truncated files, malformed names, oversized counts). These should be filled as each phase below lands.

### 3.4 Ordered Implementation Phases

#### Phase A: Data-Integrity Bugs

1. CAR export: fail or warn through the report (never silently skip) when `bake_range()` returns `None`; route bake exceptions into the report; unify the CAR/VTL failure contract.
2. Resync Animation: pass `use_absolute` matching the imported shape-key mode.
3. Fast path: treat topology-changing visible modifiers as incompatible with the shape-key fast path in both CAR and VTL.
4. ARGB1555: preserve bit 15 through import and export.
5. Empty texture: skip image creation for zero-size payloads and record a warning.

#### Phase B: Scene-State Safety

1. Add `'REGISTER', 'UNDO'` to import/export operators (verify undo granularity and memory cost on large imports).
2. Replace unconditional world replacement with a disclosed, optional shader setup (default off) or restore the prior world.
3. Expand failed-import cleanup to remove created datablocks, not just the collection.

#### Phase C: Binary and Output Robustness

1. Add the 3DN int16 parent-count guard and preflight check.
2. Unify bone-name sanitization (single rule: split at first NUL, byte-aware truncation) across `.3df`, `.car`, validation, and export name generation.
3. Write exports to a temp file in the destination directory and atomically rename on success.

#### Phase D: Reporting and Coverage

1. Route every console-only `warn()`/`error()` that affects output through `ParserContext.warnings` or the operation report.
2. Make `sounds_map` and `_strip_cycles` keys collision-safe (datablock identity, track index).
3. Document import support explicitly in the README and `doc/README.md` (`.3dn`/`.vtl` import status, `.vtl` export only).

#### Phase E: Tests

Fill the blind spots in section 3.3 as each phase lands, with at least: CAR export dropping an animation produces a reported warning/error; VTL and CAR fail identically on vertex-count mismatch; absolute resync round-trips byte-identical animation; fast path disabled with SUBSURF/MIRROR visible; alpha-bit round trip; empty-texture import; undo of import; 3DN with >32,767 bones fails preflight; atomic write leaves no partial file on injected failure.

### 3.5 Verification Matrix

- Byte-diff of exported `.car`/`.vtl` against pre-change references after each Phase A fix.
- `bake_range` failure path: report contains an explicit animation-level error and the file is not reported as fully successful.
- Resync on an absolute-mode import, then export and re-import: animation frames identical.
- SUBSURF/MIRROR visible during export: CAR falls back to the slow path or fails with a clear message; VTL does not raise a raw NumPy error.
- ARGB1555 alpha bit survives import→export (parse the re-export and compare bit 15).
- `texture_size = 0` import with `parse_texture=True`: imports without error, no degenerate image datablock.
- Import with `{'REGISTER', 'UNDO'}`: one Ctrl+Z removes the imported collection and its created datablocks.
- Textured import leaves `scene.world` unchanged unless the shader option is enabled.
- Injected mid-write failure (disk full) leaves the previous file intact.
- Report contents cover every warning the parser/exporters produce.
- 3DN with >32,767 bones fails preflight with a clear message.

### 3.6 Completion Criteria

- No export path can report success while dropping or corrupting animation data; every diagnostic affecting output reaches the report.
- Import/export round trips preserve every declared binary bit, including ARGB1555 alpha.
- Imports are undoable, do not replace the scene world without disclosure, and clean up after failures.
- Parsers, validators, and exporters share one sanitization rule set.
- All exporters write atomically.
- Import support is documented exactly as implemented, and the blind-spot tests exist.
