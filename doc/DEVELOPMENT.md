# CarnivoresIO Development Documentation

For developers, contributors, and long-term project roadmap.

> **Note**: Face flags and engine limits are in [Reference](reference.md). Coding conventions and architecture guidelines are documented in the repository root.

---

## Table of Contents
1. [Developer Notes](#developer-notes)
2. [Blender 5.0 Migration Guide](#blender-50-migration-guide)
3. [Future Roadmap](#future-roadmap)
4. [Known Issues](#known-issues)
5. [Development Decisions](#development-decisions)
6. [Debugging Tips](#debugging-tips)
7. [Contributing](#contributing)
8. [References](#references)

---

## Developer Notes

### Project Overview
CarnivoresIO is a Blender add-on for handling `.3df`, `.car`, `.3dn` files, including mesh geometry, face flags, UVs, bones, and ARGB1555 textures. Integrates with Blender's UI via custom panels/operators, using NumPy for efficient data processing.

- **Blender Version**: 4.2+
- **Dependencies**: `numpy` (bundled), `bpy`, `bpy_extras.io_utils`, `mathutils`, `bmesh`
- **Module Structure**:
  - `__init__.py`: Registration, custom properties, preferences, handlers
  - `operators/`: `CARNIVORES_OT_*` operators, `VIEW3D_PT_*` panels
  - `parsers/`: Format-specific import/export (e.g., `parse_3df.py`, `export_car.py`)
  - `core/`: `core.py` (NumPy dtypes), `constants.py` (FACE_FLAG_OPTIONS, TEXTURE_WIDTH)
  - `utils/`: Shared utilities (mesh creation, bone collection, texture conversion, animation helpers, flags, logger)

### Implementation Details

#### File Format Handling
- **Parsing**: Uses `numpy.fromfile` for efficient binary reading. Flips vertex winding/UVs for coordinate conversion. Validates integrity (vertex/face counts, bone cycles). Texture conversion ARGB1555 ↔ RGBA.
- **Exporting**: Triangulates meshes, converts UVs to integer coordinates (0-255), supports bone export from armatures/hooks. Texture export enforces 256px width.
- **Face Flags**: Stored as face-domain `INT` attribute `3df_flags`. Use `utils/flags.py` helpers. UI in `VIEW3D_PT_3df_face_flags`.
  > **Canonical Definition**: See [Reference: Face Flags](reference.md#face-flags-16-bit-bitfield)
- **Bone Handling**: Import as hooks (`HOOKS`) or armatures (`ARMATURE`). Export from armatures/hooks with vertex group mapping. Validates hierarchy cycles.
- **Performance**: NumPy vectorization, `@timed` decorator for logging, BMesh for Edit Mode operations.

#### UI Integration
- Panels: `VIEW3D_PT_carnivores_model_health`, `VIEW3D_PT_3df_face_flags`, `VIEW3D_PT_carnivores_selection`, `VIEW3D_PT_carnivores_animation`
- Import/Export: Integrated into `File > Import/Export` menus with consistent Content, Geometry, Animation/Rig, Compatibility, and Advanced Coordinate Conversion sections
- Operator tooltips: Face-flag descriptions follow C2 MEE surface-flag behavior; animation, sound, timing, rig, and selection operators describe their scope and side effects
- Model validation: `utils/validation.py` provides reusable non-destructive Blender-side preflight checks; export operators consume the same results and write them to the structured validation/export reports
- Preferences: Debug mode toggle (enables verbose logs/`@timed` output)

---

## Blender 5.0 Migration Guide

### Problem Statement
Blender 5.0 removed `action.fcurves` property. Importing `.car` models caused `AttributeError: 'Action' object has no attribute 'fcurves'`, broken animations, and upside-down models.

### Root Cause
Blender 5.0 replaced direct F-Curve access on Actions with **Action Slots** and **Channelbags**:
- Actions have multiple `slots` (typed: `KEY` for shape keys, `POSE` for armatures)
- Each slot has a `channelbag` holding `fcurves` for a specific data block type
- Direct `action.fcurves` access no longer supported

### Solution

#### 1. `get_action_channelbag` Helper
Added to `utils/animation.py` to retrieve/create `channelbag` for shape key animations:
```python
def get_action_channelbag(action):
    sk_slot_name = "ShapeKeys"
    sk_slot = None
    for slot in action.slots:
        if slot.id_type == 'KEY' and slot.name == sk_slot_name:
            sk_slot = slot
            break
    if sk_slot is None:
        sk_slot = action.slots.new(id_type='KEY', name=sk_slot_name)
    return anim_utils.action_ensure_channelbag_for_slot(action, sk_slot)
```

#### 2. Refactor `keyframe_shape_key_animation_as_action`
- Use `get_action_channelbag(action)` to get `channelbag`
- Replace `action.fcurves.clear()` → `channelbag.fcurves.clear()`
- Replace `action.fcurves.new(...)` → `channelbag.fcurves.new(...)`

#### 3. Refactor `get_action_frame_range`
Now iterates through `action.slots` to collect F-Curves:
```python
def get_action_frame_range(action):
    if not action:
        return (1, 1)
    all_fcurves = []
    if hasattr(action, "slots"):  # Blender 5.0+
        for slot in action.slots:
            cb = anim_utils.action_get_channelbag_for_slot(action, slot)
            if cb:
                all_fcurves.extend(cb.fcurves)
    elif hasattr(action, "fcurves"):  # Legacy support
        all_fcurves = action.fcurves
    if not all_fcurves:
        return (1, 1)
    frames = [kp.co[0] for fc in all_fcurves for kp in fc.keyframe_points]
    return (int(min(frames)), int(max(frames))) if frames else (1, 1)
```

### Verification
- Tested with isolated scripts (`test_action_api.py`, `verify_fix.py`)
- Full add-on test: `.car` import works, NLA editor shows strips, model orientation correct

---

## Future Roadmap

Combined from `dev_notes.md` and `future_changes.md`.

### Phase1: Enhanced Visualization & Debugging
#### 1.1 Face Flag Viewport Overlay — Implemented
- Generated `FlagColors` visualization with Show, Refresh, Hide, and Remove controls
- Optional, collapsed-by-default color-name legend and deterministic overlap blending based on serialized `3df_flags`
- Viewport color settings are restored when hiding the visualization

#### 1.2 Model Health Check & Pre-Export Validation — Implemented
- Format-aware non-destructive mesh, UV, texture, face-flag, animation, sound, rig, name, modifier, and coordinate checks
- Compact Model Health panel and explicit `Validate Model` report
- Export operators reuse the same checks; errors block output and warnings remain reviewable

### Phase2: Animation Workflow
#### 2.1 Unified Carnivores Animation Panel — Implemented
- Authoritative NLA preview-audio toggle with immediate playback stop
- NLA track list with export indices, Earlier/Later reordering, and multi-strip indication
- Selected track details include sound state, KPS, timing, duration difference, and explicit preview stop/restoration

#### 2.2 Batch Action Renaming/Cleanup
- Remove `.001`/`_Action` suffixes, convert spaces to underscores
- Apply common prefix/suffix

### Rig Reconstruction Improvements

Improve the `.car` rig reconstruction pipeline for faithfulness, stability, and determinism. Current algorithm documentation is in [Systems: Skeleton Reconstruction](SYSTEMS.md#skeleton-reconstruction-car-models); the complete staged redesign is in [Rig Reconstruction Implementation Plan](RIG_RECONSTRUCTION_PLAN.md).

**Current baseline (Phases 0-7 implemented in the working tree):**
- Preserve imported owner indices on the mesh for reconstruction use
- Degeneracy pruning (empty groups → filtered out, not mesh_mean)
- Diagnostic disconnected-cluster detection via BFS; destructive largest-cluster filtering is an explicit Legacy option and disabled by default
- Scored root selection with symmetry blacklisting and X-offset penalties
- Richer MST edge scoring (centrality + body-axis + weight bias)
- PCA-derived leaf tail placement via SVD on owner-group vertices
- Left/Right semantic naming with adaptive X-margin
- Manual root override in the UI
- Persist reconstruction metadata on armature custom properties
- Reset-to-imported-owners recovery button
- Signed, lossless raw/compact CAR owner mapping with always-on structural validation
- Pure mesh-analysis input, characteristic-scale normalization, group PCA/bounds, and topology-island diagnostics
- Experimental anatomy-constrained topology graph with robust boundary joints, bilateral limb isolation, a proximity-completed central backbone, deterministic `RigProposal`, explicit tails/roll references, and disconnected-component policies
- Analyze → Apply proposal storage, payload integrity checks, stale-source/settings rejection, scoped previews, and transactional generated-rig lifecycle handling
- Shared non-writing export-owner dry run used by `.3DF`, `.CAR`, and `.3DN` mapping, with explicit fuzzy/fallback/skipped/name-collision diagnostics
- True reconciliation of canonical raw/compact owners, generated dominant deform groups, final export owners, source groups, hierarchy, and generated-weight checksums
- Structured `PASS`, `EXPECTED_DRIFT`, `WARNING`, and `ERROR` levels in rig validation, reports, and model/export preflight

**Planned redesign (remaining phases):**
- Add scored body/symmetry axes and refine topology root/hierarchy costs against a broader real-asset matrix
- Add optional animation-assisted rigid-transform and shared-pivot fitting
- Follow separately with experimental skeletal animation conversion

Milestones, acceptance criteria, tests, risks, and file touch points are maintained in [Rig Reconstruction Implementation Plan](RIG_RECONSTRUCTION_PLAN.md). The current committed implementation state and copyable fresh-session prompt are maintained in [Rig Reconstruction Session Handoff](RIG_RECONSTRUCTION_HANDOFF.md).

### Phase3: Codebase Architecture
#### 3.1 Refactor Operators into Modular Files — Implemented
- `operators.py` is split into a `operators/` package:
  - `io.py` (import/export), `animation.py` (animation + rig), `flags.py` (face flags/selection),
    `reporting.py` (report actions), `validation.py` (model health), `preferences.py`
- Improves maintainability, reduces merge conflicts

### Phase4: Additional Improvements
#### 4.1 Direct Edit Mode Flag Editing — Implemented
- Modify and clear face flags through live BMesh layers without switching to Object Mode
- Keep Edit Mode counts, selection scope, and generated FlagColors visualization synchronized

#### 4.2 Texture Enhancements
- Auto-resize textures to 256px width on export
- Support alpha channel rendering for `sfOpacity` flag

#### 4.3 Bone Name Resolution
- Unique name generation for duplicate/invalid bone names
- UI option to rename bones before export

#### 4.4 Validation Customization
- Configurable validation thresholds via preferences
- Auto-fix options (e.g., remove degenerate faces)

#### 4.5 Performance Optimization — Partially Implemented
- Batch `foreach_get`/`foreach_set` calls
- Cache frequently accessed data (e.g., `3df_flags`)
- See [Performance Handoff](PERFORMANCE_HANDOFF.md) for the committed CAR import/export fast paths

#### 4.6 Testing Suite — Partially Implemented
- 50 automated tests across 9 files under `tests/`, covering owner mapping, rig geometry/hierarchy/adapter,
  structural validation, CAR validation, performance fast paths, and audio playback selection
- Edge case coverage for empty meshes, invalid textures, cyclic bones, and fractional animation ranges
- No fixture `.3df`/`.car` files are committed; synthetic fixtures are generated in test code

---

## Known Issues

1. **Texture Limitations**: Must be 256px wide (enforced on export and structurally validated on import). Current C2 MEE OpenGL loading expects exactly 256px height; its software loader supports variable-height rows.
2. **Bone Name Handling**: Non-ASCII/long names cleaned/truncated → possible conflicts. Duplicate names warned but not resolved.
3. **Compatibility Limits**: Legacy AltEdit may reject models above 1024 vertices/faces. The addon and current C2 MEE model arrays have no fixed 2048 mesh limit; current-engine fixed arrays are documented separately.
4. **UV Flipping**: UVs flipped on import to match Blender space. Export supports optional U/V flip, may confuse new users.

---

## Development Decisions

- **NumPy**: Chosen for performance on large arrays. Avoid Python loops.
- **BMesh**: Used for Edit Mode operations to access selected faces efficiently.
- **Validation Layers**: Non-destructive structural validation always runs for binary input. `utils/validation.py` provides format-aware Blender model preflight checks; export operators reuse those checks without mutating the source model.
- **Hook vs. Armature**: Hooks default for simplicity; armatures supported for advanced rigging.
- **Error Reporting**: Parser warnings remain in `ParserContext.warnings` and are recorded in structured operation reports. Import/export reports are written to stable Blender Text datablocks (`Carnivores_Import_Report` or `Carnivores_Export_Report`) and summarized in one popup with explicit `Open Report` and `Copy Report` actions.

---

## Debugging Tips

- **Enable Timing**: Enable **Performance Instrumentation** in add-on preferences for nested operation stages and JSON output in the `Carnivores_Performance_Report` Text datablock. Debug Mode additionally shows individual `@timed` console lines.
- **Check Warnings**: Review `ParserContext.warnings` during import.
- **Inspect Attributes**: Verify `3df_flags` in Blender Data API/Outliner.
- **Console Output**: Look for `[Export]`/`[Warning]` prefixes for bone/texture issues.

---

## Contributing

- **Code Style**: PEP 8, 4-space indent, descriptive names, comment complex logic.
- **Testing**: Test with edge-case `.3df`/`.car` files (high vert counts, empty textures, cyclic bones).
- **Pull Requests**: Include description, affected files, test results.
- **Issues**: Report on GitHub with steps to reproduce, sample files.

---

## References

- **Formats**: [Formats Doc](formats.md) and `core/core.py` for binary specs
- **Blender API**: Blender 4.2+ Python API docs for `bpy`, `bmesh`, `mathutils`
- **NumPy**: Documentation for `fromfile`, `reshape`, bitwise operations
