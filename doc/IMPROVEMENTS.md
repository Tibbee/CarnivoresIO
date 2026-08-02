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

Managed audio intentionally plays only when the user has selected an unambiguous animation source:

1. Extension track preview
2. Blender NLA tweak mode

Normal, unfocused NLA timeline playback must not trigger linked sounds. In the Carnivores workflow, one clip belongs to one animation; evaluating the normal NLA stack can otherwise cascade through unrelated strips and start clips at confusing offsets. The resolver must ignore muted or inactive tracks and strips and return enough source identity to distinguish explicit preview selections, not only a sound name.

NLA strip scaling is intentionally outside managed audio synchronization. Linked clips retain their authored speed and are not pitch-shifted, time-stretched, or reversed. For defensive compatibility with nonstandard workflows, focused playback restarts the authored clip at detected NLA repeat boundaries; this is best-effort behavior and does not guarantee synchronization for scaled or fractional repeats. Audio modders should prepare a clip matching the animation's intended frame range and KPS. See [Deferred Workflow Enhancements](#19-deferred-workflow-enhancements) for planned export and timing-assistance tools.

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

Create one helper that resolves an action's linked `bpy.types.Sound`:

1. Return `action.carnivores_sound_ptr` when set.
2. Otherwise resolve the legacy `action["carnivores_sound"]` name through `bpy.data.sounds`.
3. Return `None` if neither resolves.

Use the helper in playback, preview, export, UI operators, and validation where applicable. In particular, `CARNIVORES_OT_play_linked_sound` currently checks only the legacy string while current imports assign the pointer.

Do not remove legacy fallback support without a migration. An optional load-time migration may assign the pointer when the legacy name resolves. It should retain unresolved legacy data so a missing datablock can be repaired later.

`CARNIVORES_OT_play_linked_sound` currently uses only the legacy string property and creates a Sequencer strip instead of using the managed Audaspace playback path. It is registered but not exposed by the current panel. Either remove this legacy operator after confirming it has no supported caller, or route it through the shared linked-sound resolver and audio manager. Do not retain a second playback architecture solely for this operator.

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

#### Phase B: Resource Ownership

1. Introduce the audio manager and route all handle/device operations through it.
2. Consolidate stop, reset, critical recovery, file-load, and unregister cleanup.
3. Move the device-reset cooldown into manager runtime state.
4. Make playback cleanup safe for removed Blender objects.
5. Centralize handler registration.
6. Consolidate temporary-file cleanup.
7. Remove direct access to audio manager internals from `__init__.py` and operators.

#### Phase C: Reliability and Usability

1. Classify sound and device failures.
2. Apply bounded retry backoff to every persistent failure path.
3. Add scene and action volume controls.
4. Update current handles when volume changes.

#### Phase D: Measured or Optional Enhancements

1. Profile first-play latency and fallback factory loading.
2. Add narrowly scoped fallback caching or preloading only if justified.
3. Test whether packed-sound handling can be simplified without breaking persistence.
4. Profile scene-object scanning before adding candidate caches.
5. Add optional spatial audio after listener and attenuation decisions are settled.

### 1.11 Verification Matrix

There is currently no automated test suite, so each implementation phase must include Blender verification. Pure source-resolution and timing functions should receive unit tests if a test harness is introduced.

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

#### Compatibility

- Newly imported CAR actions use `carnivores_sound_ptr`.
- Legacy actions containing only `action["carnivores_sound"]` still play and export.
- An unresolved legacy sound name remains repairable.
- Packed imported CAR sounds survive save, close, and reopen.
- External WAV, MP3, OGG, and FLAC links retain current supported behavior.

#### CAR Sound Round Trip

- Exported payloads are mono signed `int16` PCM at 22050 Hz.
- Every sound's declared length equals its serialized payload length and is even.
- Export followed by parse preserves sound count and animation cross-reference mappings.
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
- File load and unregister leave no managed handles, stale device, retry state, or tracked temporary files.
- Legacy sound links continue to work or are safely migrated.
- CAR sound conversion always writes validated PCM with an exact declared byte length.
- Malformed sound lengths cannot silently misalign subsequent CAR sections.
- Persistent failures are rate-limited without hiding successful recovery.
- Manual verification results are recorded for supported Blender versions.

Caching and spatial audio are not required for core completion.

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
