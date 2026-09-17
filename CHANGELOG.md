# Changelog

All notable changes to CarnivoresIO are documented in this file. Newest entries first; the format loosely follows [Keep a Changelog](https://keepachangelog.com). Entries listed under **Unreleased** are merged into a version section when that version is built and tagged.

> A compact, user-facing summary ships inside every release zip as `CHANGELOG.txt`.

## [Unreleased]

- _Nothing yet — add entries here as changes land._

## [2.4.0] — 2026-09-17

Audio playback, rig reconstruction, export safety, and general usability. This release contains 54 commits; the highlights are grouped below.

### Audio

- New `AudioManager` owns all runtime audio: one linked-sound resolver for playback, preview, export, and validation; safe handling of deleted objects; idempotent cleanup on file load and unregister.
- Focused playback is now strictly exclusive: preview plays only the previewed action, NLA tweak mode only the focused strip, ordinary playback only the active object's selected track. Unfocused timeline playback stays silent.
- Deterministic playback offsets and repeat-boundary restarts; linked clips always play at their authored speed (no pitch shifting or stretching).
- Per-action and scene sound-volume controls with live updates on already-playing handles.
- Failure handling: sound and device failures are classified, retries use a bounded exponential backoff (5→10→20→40→60 s), and persistent failures no longer spam warnings every frame.
- CAR sound export is validated end-to-end (mono signed 16-bit PCM @ 22050 Hz, exact declared byte length); CAR parsing validates sound-block lengths and cross-references before reading.
- Legacy `action["carnivores_sound"]` name links are migrated to pointers on file load; unresolved legacy names stay repairable.
- "Add Sound Strip to Sequencer" authoring operator (replaces the old play-linked-sound operator).

### Rig reconstruction

- Phases 0–7 of the rig redesign landed: lossless CAR owner handling, pure geometry analysis, anatomy-aware Topology v2–v4 (bilateral pairing, central backbone, mirrored components), improved weight generation, transactional armature lifecycle, safe proposal workflow (analyze → preview → apply with staleness checks), and round-trip export reconciliation.
- Legacy rig remains the default; detached centroid-cluster pruning is now diagnostic-only unless explicitly enabled.
- New reset-to-imported-owners operator and richer debug reporting with owner/source metadata.

### Export and import safety

- CAR export no longer silently drops animations: failed bakes abort the export with a report error, and bake diagnostics (static animations, missing sound data, >64 animations, cross-reference truncation) reach the operation report instead of the console.
- All exporters (`.3df`, `.car`, `.3dn`, `.vtl`) write atomically: a temporary file is swapped in on success, so an interrupted export can no longer destroy the previous file.
- Imports and exports are undoable (`Ctrl+Z`); a failed import rolls back everything it created (objects, meshes, actions, sounds, images, materials) instead of leaving orphans.
- Resync Animation preserves the imported shape-key mode; re-syncing absolute shape keys no longer exports a static rest pose.
- The shape-key fast path is only used for position-preserving modifiers; visible Mirror/Subdivision/Decimate/… modifiers force the slower, correct evaluated bake in both CAR and VTL export.
- One shared sanitization rule for serialized names (split at first NUL, byte-aware truncation) across `.3df`, `.car`, `.3dn`, validation, and exporters.
- The 3DN exporter refuses bone counts beyond the signed 16-bit parent-index range instead of silently wrapping.
- Untextured models (`texture_size = 0`) import cleanly with a report warning instead of creating a degenerate image.
- Importing no longer replaces the scene's world: the CustomWorld lighting setup is an explicit opt-in option, off by default.
- Export sound mapping is keyed by sound datablock identity, so differently linked sounds can no longer collide on shared names.

### Model health and validation

- Non-destructive preflight validation (`utils/validation.py`) wired into all export dialogs: geometry, flags, UVs, rigs/owners, names, quantization, audio, and C2 MEE compatibility checks with PASS/INFO/WARNING/ERROR outcomes; errors block export by default.

### Performance

- Faster CAR/VTL round trips: direct shape-key sampling fast path, NumPy-vectorized baking, reduced import/export overhead, and a structured performance report (JSON text datablock) when Performance Instrumentation is enabled in preferences.

### UI and reporting

- Redesigned file-dialog option panels, clearer tooltips, post-import focus/selection/framing options, model health panel, operation reports with structured warnings, and modern face-flag editing with preserved filtered selections.
- Modernized GitHub Pages landing page (excluded from the extension zip).

### Compatibility notes

- CAR exports that previously succeeded with missing animations will now abort with an error; fix the reported animation and re-export.
- The add-on no longer switches your scene's world on import. Enable **Set Up CustomWorld Lighting** in the import dialog if you want the old behavior.
- The ARGB1555 alpha bit is still forced to 0 on export — this is deliberate (engine-undefined behavior with alpha = 1) and documented in `doc/reference.md`.

### Documentation and testing

- Consolidated documentation structure; new rig-reconstruction plan/handoff, performance analysis/handoff, and audio test checklist documents.
- Automated test suite expanded to 118 tests across 11 files, including new regression coverage for the export/import robustness fixes.
