# CarnivoresIO Performance Session Handoff

Copy the prompt below into a fresh coding-agent session. Read this file together with [`PERFORMANCE_ANALYSIS.md`](PERFORMANCE_ANALYSIS.md), [`SYSTEMS.md`](SYSTEMS.md), [`DEVELOPMENT.md`](DEVELOPMENT.md), and the repository [`AGENTS.md`](../AGENTS.md) before changing code.

---

## Sendoff Prompt

```text
Continue the CarnivoresIO performance work in
D:/Portable/Blender/portable/extensions/user/carnivores_io.

Read AGENTS.md, doc/PERFORMANCE_HANDOFF.md, doc/PERFORMANCE_ANALYSIS.md,
and the relevant sections of doc/SYSTEMS.md completely before editing.

Constraints:
- Work locally only. Do not fetch, pull, push, or modify remote branches.
- Current branch: main.
- Current committed HEAD: c037643 feat(rig): add round-trip reconciliation.
- Preserve the performance report schema (versions stay 1).
- Performance instrumentation is gated by the "Performance Instrumentation"
  preference toggle, independent of Debug Mode.
- Do not touch or commit .commandcode/.
- Use D:/Portable/Git/.gitmessage when an atomic commit is eventually
  requested. Do not add Co-Authored-By or Signed-off-by lines.
- Benchmark protocol: cold Blender processes, Performance Instrumentation on,
  Debug off. Use a DISTINCT filename per report run — the report Text datablock
  is overwritten each run, so saving the same block repeatedly yields invalid
  duplicate reports.

Measured baselines (DiloTest.car, 518 verts, 992 faces,
19 animations, 13 sounds, 769 shape keys, Blender 5.2 LTS). These values
must not be presented as post-change results until the working tree is
rebenchmarked:

Import total: ~162-166 ms
  - import_car_sounds: ~67-71 ms  (42%; 13 sounds, ~5 ms each)
  - create_shape_keys_from_car_animations: ~37 ms (23%)
  - auto_create_shape_key_actions_from_car: ~36 ms (22%; 19 x ~1.4 ms)
  - parse_car: ~8.2 ms (5%)
  - everything else: ~10 ms

Export total: ~136 ms
  - preflight sound conversion: ~73-76 ms (55%; 13 sounds, 2-8 ms each,
    cached via artifact_cache["sound_conversion"] and reused at export time)
  - export_car.animations: ~51.5 ms (38%)
      - shape_key_bake: ~17.5 ms (19 anims; ~20 us/sample, dominated by
        fc.evaluate() Python calls)
      - nla_track_solo: ~17.3 ms (tracks bake time 1:1 - mute toggles are
        cheap, the bake itself is the cost)
      - nla_state_restore: ~3.6 ms
      - nla_state_mute_all: ~0.15 ms
      - shape_key_fcurve_setup: ~0.2 ms
  - file_write: ~2.1 ms
  - mesh_gather: ~1.2 ms
  - export-time sound_conversion (cached): ~0.002 ms each (correctly cached)

Post-change controlled results (three separate factory-startup Blender 5.2
processes, Debug off, instrumentation on, medians):

Import: 149.923 ms (runs 149.923, 150.362, 149.805 ms; about 8.6% faster
than the midpoint of the earlier 162-166 ms range)
  - import_car_sounds: 70.293 ms
  - create_shape_keys_from_car_animations: 33.997 ms
  - create_image_texture: 19.235 ms
  - auto_create_shape_key_actions_from_car: 9.950 ms
  - parse_car: 8.850 ms

Export: 73.794 ms (runs 78.493, 73.794, 73.476 ms; about 45.7% faster
than the earlier ~136 ms result)
  - export_car.animations: 48.013 ms
      - shape_key_data_extraction: 31.738 ms
      - shape_key_bake: 15.297 ms
  - validation.preflight: 21.649 ms
      - first packed-image resolution: 15.914 ms
      - all 13 compatible sound payload checks: 0.879 ms total
  - file_write: 2.160 ms
  - mesh_gather: 1.411 ms

The optimized export restored the original 4,073,232-byte file size. All 769
animation frames, all 13 PCM payloads by sound name, animation-to-sound links,
texture data, and faces compare exactly after parse. Base coordinates differ by
at most 1.52587890625e-05 from the float import/export transform. A separate
pre-existing correctness issue was exposed: source owner IDs 1-35 export as
owner 0 when the imported CAR mesh has not been reconstructed into an armature.

Earlier round-trip evidence preserved 19/19 animations, 13/13 sounds,
518 vertices, and 992 faces, but the exported file shrank 9,324 bytes
(4,073,232 -> 4,063,908). That difference equals exactly three animation
frames at 518 vertices. The committed fractional-endpoint fix restores all
769 frames and returns to the original 4,073,232-byte size (see open item 2).

Optimization history (committed work plus the explicitly noted working-tree changes):
  - Absolute Shape Keys became the CAR import default (measured 5.4x faster
    action creation, 199.3 -> 36.8 ms; round-trip verified). Relative stays
    available for compatibility; presets set explicit values.
  - Performance report records operator options (record_operator_options in
    utils/performance.py) and splits CAR export timing into
    shape_key_fcurve_setup, shape_key_bake, nla_state_mute_all,
    nla_track_solo, nla_state_restore.
  - import_car_sounds / associate_sounds_with_animations are timed stages.
  - Committed fast path: compatible packed/external PCM16 mono 22050 Hz WAV files
    bypass Audaspace and preserve their PCM bytes exactly. Other formats retain
    the existing conversion fallback. NEVER probe factory.specs; it forces a
    decode and roughly doubles the cost.
  - Committed: import_car_sounds writes the WAV header + int16 bytes directly,
    imports only referenced table entries when actions exist, and stores known
    duration metadata for decode-free panel drawing.
  - Committed: animation coordinates are transformed in batches, shape keys
    are grouped once for action creation, simple linear absolute F-Curves are
    sampled in NumPy, and the direct shape-key path avoids scene/NLA mutation.
  - Committed: fractional action/NLA endpoints are preserved. A focused
    Blender regression test confirms a 14-frame 34 KPS action exports 14 frames.
  - Absolute bake remains vectorized only for >=64 samples; smaller animations
    use scalar coordinate interpolation after vectorized F-Curve sampling.

Current state: instrumentation is usable, but multi-file attribution remains
deferred. Cold DILOPH benchmarks and parsed binary comparisons pass as recorded
above; manual playback, visual deformation, and non-22050/stereo fallback checks
remain required.

Open questions / deferred items (pick up here):
  1. Test 44100 Hz stereo and other incompatible audio through the Audaspace
     fallback. A defer-pack option for import_car_sounds remains optional work.
  2. RESOLVED: the former 9,324-byte round-trip difference was exactly three
     518-vertex frames. Fractional endpoint truncation is fixed and committed;
     DILOPH exports all 769 frames and returns to the original 4,073,232-byte
     size. Covered by tests/test_performance_optimizations.py.
  3. Multi-file performance-report enhancement (file identifiers on stage
     records, per-file totals, aggregate metadata, explicit partial-failure
     status) - queued from an earlier session, not started.
  4. Audio correctness Phase A (doc/AUDIO_TEST_CHECKLIST.md) - not started.
  5. Accessibility / narrow-width UI review (doc/UI_USABILITY_ROADMAP.md) -
     downstream, not performance-related.

Known dead ends (do not retry without new information):
  - aud.Sound.buffer() crashes in Blender 5.2 - cannot create a Sound from
    memory. bpy.data.sounds.load() requires a real filepath and pack() packs
    from that file. There is NO public API to create a Sound datablock from
    memory without a temp file. The temp-file approach is required.
  - Probing factory.specs in sound conversion forces a decode, doubling cost.
  - keyframe_points.add() vs insert() for action creation is a wash
    (~1.4 ms/action either way); the cost is Blender C API calls.

Note on NLA: the earlier claim that "NLA mute toggles are not the bottleneck"
applied to the individual toggle cost. Skipping the entire NLA mutation for the
direct shape-key path (committed fast path) removed nla_state_mute_all / nla_track_solo
/ most of nla_state_restore and was a real, measured win. Do not reintroduce
NLA muting in the fast path.

Test protocol:
- The single strongest regression check is a byte-diff of the exported .car
  against a pre-change reference: identical bytes prove the bake math and
  sound conversion are output-preserving.
- Test non-22050-mono sounds (e.g. 44100 Hz stereo) to verify the conversion
  fast path is conditional.
- Verify packed sounds import correctly, play correctly, preserve compatible
  PCM bytes, and leave no temp files in %TEMP% (carnivores_io_sounds_*).
- Run the Blender suite (currently 101 tests, including focused performance
  fast-path coverage) before and after benchmark-driven changes.
- Use cold Blender starts, Performance Instrumentation on, Debug off.
```
