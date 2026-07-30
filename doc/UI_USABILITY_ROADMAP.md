# UI Usability and User-Friendliness Roadmap

Implementation-oriented design and handoff document for improving the CarnivoresIO Blender extension UI. This document is intended to give another coding agent enough context, priorities, constraints, and acceptance criteria to implement the work incrementally.

> **Status:** WP-01 (operator context/outcomes), WP-02 (dialog consistency), WP-03 (result and warning reports), and WP-04 (model health/pre-export validation) are implemented; remaining work packages remain proposals unless marked otherwise.
>
> **Target:** Blender 4.2+ extension supporting `.3df`, `.car`, `.3dn`, and `.vtl` workflows.
>
> **Primary runtime target:** Carnivores 2 Modder's Engine Extended (C2 MEE) 1.11.

---

## 1. Goals

1. Make common import, editing, preview, validation, and export workflows discoverable without requiring format knowledge.
2. Prevent avoidable failures before file export.
3. Clearly separate routine actions from advanced, destructive, and experimental actions.
4. Make batch operation results and parser warnings reviewable.
5. Preserve exact engine compatibility while improving labels and presentation.
6. Keep narrow Blender sidebar layouts readable.
7. Give operators correct context availability, completion status, and recovery guidance.

## 2. Non-goals

- Do not change binary formats merely to simplify the UI.
- Do not silently repair imported or exported data.
- Do not replace structural validation with UI-only checks.
- Do not remove compatibility with properties already stored in `.blend` files without migration.
- Do not expose unverified engine limits as hard errors.
- Do not combine this roadmap with a rewrite of parsers, audio management, or rig reconstruction algorithms.
- Do not modify the C2 MEE source as part of extension UI work.

---

## 3. Required reading and sources of truth

Before implementing a phase, read the relevant files completely.

### Extension

- `AGENTS.md` — project conventions and critical compatibility notes
- `doc/DEVELOPMENT.md` — architecture and Blender compatibility
- `doc/FORMATS.md` — extension format and coordinate-conversion reference
- `doc/SYSTEMS.md` — animation, audio, and rig systems
- `doc/reference.md` — canonical extension face flags, limits, and validation rules
- `doc/IMPROVEMENTS.md` — detailed audio correctness and workflow roadmap
- `doc/RIG_RECONSTRUCTION_PLAN.md` — rig proposal, preview, and reconciliation roadmap
- `doc/RIG_RECONSTRUCTION_HANDOFF.md` — current rig implementation state
- `operators/io.py`, `operators/flags.py`, `operators/animation.py`
- `parsers/validate.py`
- `__init__.py`

### C2 MEE runtime source

Local path:

```text
E:/Munka/Programming/C++/Carnivores/Carnivores2MEE1.11
```

Read its `AGENTS.md` before using the engine repository. Canonical shared engine documentation is under:

```text
E:/Munka/Programming/C++/Carnivores/CarnivoresDoc
```

Relevant runtime references include:

- `CarnivoresDoc/reference/car-format.md`
- `Carnivores2MEE1.11/Hunt/Core/ModelTypes.h`
- `Carnivores2MEE1.11/Hunt/Loaders/ModelLoader.cpp`
- `Carnivores2MEE1.11/Hunt/Renderer/GLModel.cpp`
- `Carnivores2MEE1.11/Hunt/Renderer/RenderSoft.cpp`
- `Carnivores2MEE1.11/Hunt/Core/Constants.h`
- `Carnivores2MEE1.11/Hunt/Core/AudioTypes.h`

### Authority policy

Use each source for its appropriate purpose:

1. Engine source is authoritative for actual C2 MEE runtime behavior.
2. Shared `CarnivoresDoc` explains intended and verified engine behavior, but check source when a UI validation rule depends on exact loader behavior.
3. Extension `core/core.py` is authoritative for the extension's NumPy binary structures.
4. Extension `doc/reference.md` is canonical for user-facing compatibility rules already adopted by the add-on.
5. Legacy AltEdit constraints are compatibility diagnostics, not C2 MEE structural limits.
6. If sources disagree, do not guess. Document the discrepancy, inspect loader/consumer code, and keep uncertain checks informational until verified.

Every new validation result should identify its target where relevant, for example:

- Structural safety
- C2 MEE compatibility
- Legacy AltEdit compatibility

---

## 4. Current UI inventory

### File menus and dialogs

Implemented in `__init__.py` and `operators/io.py`:

- Import `.3df`
- Import `.car`
- Export `.3df`
- Export `.car`
- Export `.3dn`
- Export `.vtl`
- Warning dialog via `CARNIVORES_OT_modal_message`

### 3D View sidebar

Implemented in `operators/flags.py` and `operators/animation.py`:

- `VIEW3D_PT_3df_face_flags`
- `VIEW3D_PT_carnivores_selection`
- `VIEW3D_PT_carnivores_animation`

The animation panel currently also contains global audio and rig reconstruction controls.

The Model Health panel uses `utils/validation.py` for a compact summary and an explicit format-aware preflight action. Export dialogs reuse the same validator before writing files.

### Preferences

Implemented in `__init__.py`:

- Debug mode only

---

## 5. UX principles for implementation

1. **Context first:** Disable unavailable actions through `poll()` instead of allowing predictable failures.
2. **Progressive disclosure:** Show safe, common controls first; put coordinate details, topology policies, and debug tools under advanced sections.
3. **Explicit scope:** Destructive labels must say whether they affect selected faces, the active object, or all selected objects.
4. **Actionable diagnostics:** State what is wrong, why it matters, which compatibility target is affected, and whether a safe fix exists.
5. **No surprise scene mutation:** Preview and visualization tools must explain and restore viewport, playback, mute, and frame-range changes.
6. **Narrow-panel readability:** Do not rely on long permanent labels. Prefer concise labels and complete tooltips.
7. **Consistent vocabulary:** Use the same names in panels, file dialogs, reports, README instructions, and operator descriptions.
8. **Stable data:** UI refactors should preserve existing RNA property identifiers unless a migration is supplied.
9. **Report partial success:** Batch operations must distinguish success, partial success, and total failure.
10. **No silent fixes:** Auto-fixes must be opt-in, undoable where possible, and clearly report modifications.

---

## 6. Proposed information architecture

Keep one `Carnivores` sidebar category, but reorganize features into focused panels. Child panels may be used if they improve grouping without creating excessive scrolling.

### 6.1 Carnivores Model

Purpose:

- Identify whether the active object contains Carnivores data.
- Show compact model health.
- Provide pre-export validation and common export shortcuts.
- Show source metadata when available.

Suggested content:

- Active object/type
- Vertex and triangle counts
- Texture status
- Face-flag status
- Animation/owner status
- Compatibility target selector, if implemented
- `Validate Model` / `Preflight Export`
- Contextual export shortcuts

### 6.2 Face Flags

Merge editing and selection into one workflow:

1. Selected-face summary
2. Per-flag state and modification controls
3. Collapsed `Find Faces With Flags` filter
4. Visualization/overlay controls and legend

### 6.3 Animation & Audio

Contain only:

- Animation source
- NLA export-order list
- Selected action details
- KPS and timing
- Linked sound and duration
- Preview controls
- Global preview-audio controls

### 6.4 Rig Reconstruction

Move reconstruction out of the animation panel. Contain:

- Owner-cache status
- Recommended reconstruction controls
- Experimental topology controls
- Root selection
- Reconstruction action
- Reset/recovery
- Proposal/report diagnostics

### 6.5 Empty states

Every panel should provide a next action rather than only reporting absence. Examples:

- No object: `Select a mesh object to inspect Carnivores data.`
- Missing flags: show `Create Face Flags`.
- Missing animation: show whether the object has shape keys, object animation, or neither.
- Owner cache present but no rig: show `Reconstruct Rig from Owners`.
- Not a Carnivores object: show a neutral message rather than an error.

---

## 7. Work packages

Each work package is independently reviewable. Avoid implementing all packages in one large change.

## WP-01: Operator context, outcomes, and immediate correctness

**Priority:** Required / first

### Problems

- Several operators remain clickable when the current context cannot succeed.
- Multi-3DF export can finish with `FINISHED` after no file was exported.
- Batch import may finish with `FINISHED` after all files fail.
- Partial success is not summarized distinctly.
- The Edit Mode `CLEAR_ALL` face-flag path switches to Object Mode and then attempts Edit BMesh access.
- CAR import failure cleanup does not consistently remove a partially created collection.
- `.3dn` and `.vtl` failure paths use `traceback.print_exc()`, contrary to project logging conventions.

### Requirements

- Add suitable `poll()` methods and `poll_message_set()` calls where supported.
- Ensure operator return values represent total success versus total failure.
- Continue after individual batch failures, but report a final summary.
- Keep warnings in parser/export contexts and log exceptions through `utils.logger`.
- Correct face-flag Edit Mode handling without unnecessary mode switching.
- Ensure partial import data is cleaned only for the failed file.

### Candidate files

- `operators/io.py`
- `operators/flags.py`
- `operators/animation.py`

### Acceptance criteria

- Invalid-context actions are disabled and expose a useful reason.
- Exporting zero files returns `CANCELLED`.
- Importing zero files successfully returns `CANCELLED`.
- Mixed-success batches return `FINISHED` and report success/failure counts.
- Clearing flags in Edit Mode affects the documented scope and restores mode/selection.
- No new `print()` or `traceback.print_exc()` calls are introduced.

---

## WP-02: Import/export dialog consistency and terminology

**Priority:** Required

**Status:** Implemented in `operators/io.py`, `operators/flags.py`, `operators/animation.py`, `core/constants.py`, `__init__.py`, and the user documentation.

### Requirements

Use consistent sections across dialogs:

- Content
- Geometry
- Animation or Rig, when applicable
- Compatibility
- Advanced Coordinate Conversion

Specific changes:

- Use `Import Scale` and `Export Scale` labels or equally explicit wording.
- Explain the standard `0.01` import / `100.0` export relationship.
- Present the legacy `flip_handedness` property as a clearer user-facing concept such as `Use Carnivores Coordinate Conversion`; preserve its identifier unless migration is provided.
- Keep axis-forward/up controls visually advanced.
- Explain Hooks versus Armature in the 3DF importer.
- Clarify CAR smoothing: it changes generated deform groups even though CAR has no stored hierarchy.
- Define behavior when `Import Sounds` is enabled but `Import Animations` is disabled. Either disable sound association controls or explain that sounds are imported without linked Actions.
- Rename `Static Model Hunter (.3dn)` to wording that identifies the target game/platform more clearly, after confirming intended `.3dn` scope.
- Keep dependent rows disabled consistently.

### Acceptance criteria

- All file dialogs use consistent section names and ordering.
- Default coordinate conversion remains binary-compatible and round-trips as before.
- Existing presets continue to load where Blender permits.
- Tooltips explain effects, not implementation details alone.
- Advanced labels do not imply that changing axes is normally required.

---

## WP-03: Result and warning report system

**Priority:** Required

**Status:** Implemented in `utils/reporting.py`, `operators/reporting.py`, and `operators/io.py`.

### Problems

`CARNIVORES_OT_modal_message` displays unwrapped labels in a fixed-width dialog. Large or multi-file reports are difficult to review or retain.

### Requirements

Create a reusable operation report model with at least:

- Operation name
- Source/destination file
- Severity (`INFO`, `WARNING`, `ERROR`)
- Category
- Message
- Optional suggested action

Presentation requirements:

- Concise popup summary
- Complete report in a Blender Text datablock
- Group entries by file and severity
- Display counts
- Provide `Open Report` and, if practical, `Copy Report`
- Use one final report for a batch, not one dialog per file
- Preserve `ParserContext.warnings`; adapt presentation rather than bypassing parser validation

Suggested Text datablock names:

- `Carnivores_Import_Report`
- `Carnivores_Export_Report`
- `Carnivores_Validation_Report`

### Candidate files

- New `utils/reporting.py`
- New or existing UI operator module
- `operators/io.py`

### Acceptance criteria

- Long messages remain readable.
- Batch results identify every failed file.
- Full details remain available after dismissing the popup.
- Total failure is clearly differentiated from warnings-only completion.
- Report generation works without changing the current editor type; opening the report is an explicit action.

---

## WP-04: Model Health and pre-export validation

**Priority:** Highest-value workflow improvement

**Status:** Implemented in `utils/validation.py`, `operators/validation.py`, `operators/io.py`, and the structured reporting layer.

### Requirements

Add a non-destructive preflight operator and compact panel summary. Validation should be format-aware.

Potential checks:

#### Common mesh checks

- Active object exists and is a mesh
- Mesh contains vertices/faces
- Coordinates are finite
- Faces can be triangulated
- Face indices and attributes are structurally valid
- UV data exists when required
- Evaluated mesh/export modifier behavior is understood

#### Texture checks

- Image can be resolved from the expected material/image source
- Texture width is 256
- Encoded byte size is row-aligned
- Texture availability matches `Export Textures`
- C2 MEE hardware-renderer expectations are identified separately from software-loader behavior

Engine basis to verify:

- `ModelLoader.cpp` derives software height from `TextureSize >> 9`.
- Hardware paths set model texture height to 256.
- Do not collapse these into a single unexplained rule; identify the target in the diagnostic.

#### Face flags

- `3df_flags` exists, is `FACE` domain, and is `INT`
- Values fit the serialized field
- Unknown bits are reported without destructive removal
- Flag combinations with meaningful runtime ordering or transparency behavior are explained only after verification against engine consumers

#### CAR checks

- Internal model and animation names fit serialized fields
- Animation and sound counts match verified C2 MEE arrays
- KPS is positive and representable
- Every exported animation has valid frame data
- Linked sounds resolve and can be converted to CAR PCM
- Owner mapping and rig/export source are reconcilable

#### Format-specific checks

- `.3df`, `.car`, `.3dn`, and `.vtl` should expose only relevant checks.
- Legacy AltEdit warnings must remain distinct from structural and current-engine checks.

### Result levels

- `PASS`: ready
- `INFO`: relevant fact or compatibility note
- `WARNING`: export can proceed but compatibility may be affected
- `ERROR`: export cannot produce a structurally valid requested format

### Fix actions

Only add fixes that are safe and explicit. Examples:

- Create `3df_flags`
- Select problematic faces
- Create a triangulated duplicate
- Reset generated owner groups from canonical imported owners

Texture resizing, topology deletion, or owner reassignment must not happen silently.

### Architecture

Prefer reusable validation data over drawing checks directly in panels. UI code should render results produced by validation functions. Extend `parsers/validate.py` where checks belong to format validation; use a focused Blender-side validator for scene/datablock checks.

### Acceptance criteria

- Preflight does not mutate the model.
- Export can invoke the same checks without duplicating rules.
- Every diagnostic identifies the affected format/target when ambiguity exists.
- Current C2 MEE limits are verified from source before being enforced.
- Legacy limits remain warnings unless the selected target requires them.
- A valid import-export round trip is not blocked by new speculative checks.

---

## WP-05: Post-import focus and summaries

**Priority:** Recommended

### Requirements

After successful import:

- Select imported model objects.
- Make the last or only imported model active.
- Optionally frame imported objects in a 3D View when invoked from a compatible context.
- Do not disrupt unrelated areas or fail import because framing is unavailable.
- Report created collections, objects, animations, sounds, and warning counts.
- Add preferences for automatic select/frame behavior if users need control.

### Acceptance criteria

- Single import leaves the imported mesh active.
- Multi-import selects all newly imported primary meshes.
- Background/scripted import does not depend on a 3D View.
- Framing failure does not roll back a successful import.

---

## WP-06: Face flag editor and selection redesign

**Priority:** Recommended

### Requirements

Merge existing face-flag and flag-selection concepts into a coherent workflow.

For each flag show:

- Concise label
- Count within documented scope
- State: none, mixed, or all
- Set, clear, and toggle controls
- Complete tooltip based on `FACE_FLAG_OPTIONS`

Selection filter:

- Keep flag mask choices collapsed by default.
- Show selected mask summary.
- Keep Any/All/None and Select/Deselect/Invert terminology.
- Move lengthy Boolean explanations to tooltips or a help popover.
- Disable Apply when no flags are selected.
- Preview or report matched-face count.

Destructive scope:

- Separate `Clear Flags on Selected Faces` from `Clear Flags on All Faces`.
- Confirm all-face clearing.
- Preserve mode and selection.

Performance:

- Do not perform expensive repeated conversions during every panel redraw.
- Measure `count_flag_hits()` on representative meshes before adding caches.
- If caching is added, define invalidation for mesh edits and flag operators.

### Acceptance criteria

- The panel remains understandable at narrow N-panel widths.
- No essential action depends only on an unexplained icon.
- Selection semantics match Any/All/None descriptions.
- Counts and mixed states update after edits.
- Operations work in Object and Edit Mode with documented scope.

---

## WP-07: Face flag visualization

**Priority:** Recommended after WP-06

### Current issue

`Visualize Flags (Colors)` creates/updates `FlagColors` but does not ensure the viewport displays that attribute, so the action may appear to do nothing.

### Requirements

Provide an explicit workflow:

- Show visualization
- Refresh visualization
- Hide visualization
- Optional removal of generated visualization data
- Visible legend
- Defined priority/blending rule for faces with multiple flags

Preferred direction:

- A viewport overlay that does not modify export data.

Acceptable initial direction:

- Continue using `FlagColors`, but clearly explain and optionally configure viewport shading.
- Record and restore any viewport settings changed by the extension where practical.
- Never modify `3df_flags` while visualizing.

### Engine-aware legend

Descriptions should reflect runtime use, not merely names. Verify flag definitions and consumers in C2 MEE before claiming effects. Keep extension `doc/reference.md` canonical for the exposed bit list.

### Acceptance criteria

- Clicking Show produces visible feedback or a precise instruction explaining the remaining manual step.
- A user can return to the previous display state.
- Multiple-flag color behavior is deterministic and documented.
- Visualization data is never serialized as a substitute for `3df_flags`.

---

## WP-08: Animation and audio workflow

**Priority:** Recommended; coordinate with `doc/IMPROVEMENTS.md`

### 8.1 Remove duplicate audio toggles

The panel currently displays the RNA Boolean and a second operator that toggles the same value. These paths differ because only the operator immediately stops managed audio.

Requirements:

- Expose one authoritative control.
- Use a property update callback or shared setter to stop audio immediately when disabled.
- Show clear On/Off state.
- Do not create a second playback architecture.

### 8.2 Complete export-order controls

The list is labeled `NLA Tracks (Export Order)` but has no reorder controls.

Add:

- Export index
- Move up/down
- Rename
- Empty-track indication/removal
- Clear behavior for tracks containing multiple strips

Verify whether export order uses track order directly before changing list reversal behavior. Ensure the visually selected row and `carnivores_active_nla_index` always resolve to the same underlying track.

### 8.3 Timing summary

For the selected action display:

- Action name
- Frame range/count
- Effective KPS
- Animation duration
- Linked sound duration, where resolvable
- Duration difference

Changing KPS must update information but must not alter audio. Re-sync remains an explicit operation.

### 8.4 Preview state

- Highlight the previewed track.
- Use a text label such as `Stop Preview`, not only a small icon.
- Show preview range/status.
- Keep a persistent stop action while preview is active.
- Restore track mute states, frame range, frame, sound-enabled state, and handlers on every normal stop/error path.

### 8.5 Sound assignment

Potential controls:

- Assign/import or replace
- Clear link
- Reveal external file
- Show packed/external/missing state
- Export linked audio

Detailed audio-export and timing-assistance requirements already exist in `doc/IMPROVEMENTS.md`; do not duplicate or contradict them here.

### Acceptance criteria

- Disabling preview audio stops active managed handles immediately.
- Track reordering changes actual CAR export order and survives save/reopen.
- Selected-row details correspond to the selected visual row.
- Preview always restores modified scene/NLA state.
- Timing values agree with the exporter's KPS/frame interpretation.

---

## WP-09: Rig reconstruction panel and guidance

**Priority:** Recommended; algorithm changes are out of scope

### Requirements

Move rig controls into their own panel.

Recommended section:

- Stable/legacy algorithm as default
- Semantic naming
- Optional generated deform-weight smoothing
- Clear reconstruction button

Experimental section:

- Topology algorithm
- Disconnected-component policy
- Warning that behavior is experimental
- Explanations of canonical owner boundaries versus generated weights

Root selection:

- Replace or supplement raw integer entry with a searchable list.
- Display owner index, generated name, and owned vertex count.
- Include `Automatic`.
- Preserve the existing integer property for compatibility if it remains the stored value.

Diagnostics:

- Rename `Log Rig Debug Info` to `Generate Rig Report`.
- Provide `Open Report` after generation.
- Show owner-cache presence, existing armature state, and whether reconstruction may replace/generated data.

Future proposal preview must follow `doc/RIG_RECONSTRUCTION_PLAN.md`, not a separate implementation invented from this UI roadmap.

### Acceptance criteria

- Rig controls no longer require users to open an Animation panel.
- Experimental controls are visually and textually distinct.
- Root override does not require memorizing an owner index.
- Reset-to-imported-owners clearly states that it rebuilds vertex groups from canonical cached owners.
- Reports are accessible without manually finding a Text datablock.

---

## WP-10: Preferences and onboarding

**Priority:** Optional after core workflows stabilize

Potential preferences:

- Default import/export scale
- Default coordinate conversion
- Default 3DF bone import type
- Default compatibility target
- Auto-select imported objects
- Auto-frame imported objects
- Show advanced options
- Debug mode under a Diagnostics heading
- Documentation URL
- Issue tracker URL
- Restore defaults

Requirements:

- Do not move per-file decisions into global preferences when users reasonably vary them per operation.
- Persisted defaults must not silently alter existing workflows after update.
- Explain whether settings affect newly invoked operators only.

Onboarding may include a small help menu or links, but should not show intrusive startup dialogs.

---

## 8. Suggested implementation sequence

### Phase A — correctness and foundations

1. WP-01: operator context, outcomes, and face Edit Mode fix
2. WP-03: reusable reports
3. WP-02: consistent dialogs and terminology
4. Add focused regression tests for pure/report logic where possible

### Phase B — export confidence

1. WP-04: non-destructive validation model — implemented
2. Compact Model Health panel — implemented
3. Export integration — implemented
4. WP-05: post-import selection and summaries — next

### Phase C — editing workflows

1. WP-06: merge face editing and selection
2. WP-07: usable visualization
3. Split panels according to the proposed information architecture

### Phase D — animation and rig UX

1. WP-08.1: authoritative preview-audio toggle
2. WP-08.2: export-order controls
3. WP-08.3/8.4: timing and preview state
4. WP-09: dedicated rig panel and root selector
5. Implement deeper audio/rig items only through their dedicated roadmaps

### Phase E — preferences and polish

1. WP-10 preferences
2. Documentation links and empty-state actions
3. Accessibility and narrow-width review
4. Final README screenshots and usage updates

---

## 9. Testing matrix

Run tests against at least Blender 4.2 and the latest supported Blender version, especially where action slots/channelbags differ.

### Context and panel tests

- No active object
- Active non-mesh object
- Ordinary mesh with no Carnivores attributes
- Imported 3DF mesh
- Imported CAR mesh with and without animation/sound
- Mesh with owner cache but no vertex groups
- Mesh with vertex groups but no owner cache
- Mesh with an existing reconstructed armature

### Import/export tests

- Single successful import/export
- Multi-file success
- Mixed-success batch
- Total failure
- Missing texture
- Non-256 texture width
- No UV map
- Empty mesh
- Non-triangulated mesh
- Unknown face-flag bits
- Legacy-limit warning without structural failure
- Current C2 MEE animation/sound count diagnostics

### Face flag tests

- Object Mode and Edit Mode
- No selected faces
- Partial selection
- None/mixed/all states
- Set, clear, toggle
- Clear selected and clear all
- Any/All/None matching
- Visualization show/hide/restore

### Animation/audio tests

Use `doc/AUDIO_TEST_CHECKLIST.md` plus:

- Correct visual list selection after reorder
- Export order matches list
- Preview switch and stop
- Disable sound during playback
- Missing external sound
- Packed imported sound
- Duration summary under KPS override

### Rig tests

Use `doc/RIG_TEST_CHECKLIST.md` plus:

- Dedicated panel availability
- Root selector/index mapping
- Experimental warning visibility
- Report generation/opening
- Reset owner groups

### Engine verification

For validation changes that claim C2 MEE compatibility:

1. Cite the relevant loader/consumer function in code comments or validation documentation.
2. Export a representative asset.
3. Load it in the deployed C2 MEE game using the repository's documented deployment workflow.
4. Test both OpenGL and software paths when the rule differs by renderer.
5. Record discrepancies rather than broadening a hard rule without evidence.

---

## 10. Implementation constraints and conventions

- Use `from ..utils.logger import debug, info, warn, error`; never `print()`.
- Preserve `ParserContext.warnings` for parser/export warnings.
- Raise `ValueError` for fatal validation/parsing failures.
- Use NumPy vectorization for face, vertex, flag, and owner bulk work.
- Keep format structures in `core/core.py`; do not hardcode binary offsets/sizes in UI code.
- Use `utils/flags.py` helpers for `3df_flags`.
- Use Blender 5-compatible animation helpers in `utils/animation.py`.
- Register every new class in `operators/__init__.py`.
- Add and remove every RNA property symmetrically in `register()`/`unregister()`.
- Prefer `PropertyGroup` containers for a large set of new UI state rather than continuing to add unrelated properties directly to `Scene`.
- Avoid storing transient playback, report-view, or viewport-restore state in `.blend` files unless persistence is intentional.
- Keep UI `draw()` methods presentation-focused; do not run heavy validation or mutate scene data during draw.

---

## 11. Decisions required before implementation

Resolve these explicitly in the relevant pull request or design note:

1. Should Model Health validate only the active export format, or show a format selector?
2. Should export be blocked on validation errors automatically, or should the exporter remain the final authority?
3. Should face visualization use a true overlay or managed color attributes initially?
4. What is the deterministic color rule for faces with multiple flags?
5. Should `Import Sounds` without `Import Animations` import unlinked datablocks or be disabled?
6. What does `.3dn` target in user-facing terminology?
7. Should post-import framing default on or off?
8. How should tracks containing multiple strips map to CAR's one-animation-per-entry workflow?
9. Should root selection use a dynamic enum or a dedicated searchable operator?
10. Which preferences are valuable enough to persist globally rather than remain operator presets?

Do not bury these policy choices inside implementation details.

---

## 12. Definition of done for each work package

A package is complete only when:

- Behavior and scope match this document or an explicitly recorded revised decision.
- New classes and properties register/unregister cleanly.
- Existing `.blend` data remains usable.
- No regression is introduced in default import/export coordinate conversion.
- Operator success/cancel results are correct.
- Errors and warnings are available outside the system console.
- Narrow sidebar layout has been manually checked.
- Relevant manual checklist items pass.
- Engine-specific claims have been checked against C2 MEE source.
- README and affected documentation are updated.
- The extension builds with `./tools/build_repo.ps1` (PowerShell invocation on Windows may be `./tools/build_repo.ps1` or `powershell -ExecutionPolicy Bypass -File tools/build_repo.ps1`, depending on shell).

---

## 13. Recommended first implementation task

Start with **WP-01 only**. It has limited architectural risk and fixes visible correctness issues required by later UI work.

Suggested first-agent prompt:

> Read `AGENTS.md` and `doc/UI_USABILITY_ROADMAP.md` completely. Implement WP-01 only. Inspect all affected operators before editing. Add context-aware polls, correct batch return/report behavior, fix Edit Mode clear-all flags without unsafe mode/BMesh access, clean failed import collections consistently, and replace traceback printing with project logging. Preserve binary behavior and property identifiers. Run available tests, report files changed, and list Blender manual checks still required.
