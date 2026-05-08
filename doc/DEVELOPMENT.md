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

- **Blender Version**: 4.0+
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
- Panels: `VIEW3D_PT_3df_face_flags`, `VIEW3D_PT_carnivores_selection`, `VIEW3D_PT_carnivores_animation`
- Import/Export: Integrated into `File > Import/Export` menus
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
#### 1.1 Face Flag Viewport Overlay
- Custom overlay coloring faces by `3df_flags` (e.g., blue=water, red=death zones)
- Toggle in viewport properties, legend, interactive feedback

#### 1.2 Model Health Check & Pre-Export Validation
- Check texture dimensions (power-of-two), mesh convexity, vertex limits
- Detect N-gons/quads if engine only supports triangles
- Generate user-friendly report with warnings/fixes

### Phase2: Animation Workflow
#### 2.1 Unified Carnivores Animation Panel
- Refactor `VIEW3D_PT_carnivores_audio` → `VIEW3D_PT_carnivores_animation`
- Global NLA sound toggle, NLA track list (export order), track reordering
- Selected track details: action name, sound link, KPS override, play preview

#### 2.2 Batch Action Renaming/Cleanup
- Remove `.001`/`_Action` suffixes, convert spaces to underscores
- Apply common prefix/suffix

### Phase3: Codebase Architecture
#### 3.1 Refactor Operators into Modular Files
- Split monolithic `operators.py` into `carnivores_ops/` directory:
  - `import_ops.py`, `export_ops.py`, `animation_ops.py`, `ui_ops.py`
- Improves maintainability, reduces merge conflicts

### Phase4: Additional Improvements
#### 4.1 Direct Edit Mode Flag Editing
- Modify `bulk_modify_flag` to use BMesh layers directly, avoid mode switches

#### 4.2 Texture Enhancements
- Auto-resize textures to 256px width on export
- Support alpha channel rendering for `sfOpacity` flag

#### 4.3 Bone Name Resolution
- Unique name generation for duplicate/invalid bone names
- UI option to rename bones before export

#### 4.4 Validation Customization
- Configurable validation thresholds via preferences
- Auto-fix options (e.g., remove degenerate faces)

#### 4.5 Performance Optimization
- Batch `foreach_get`/`foreach_set` calls
- Cache frequently accessed data (e.g., `3df_flags`)

#### 4.6 Testing Suite
- Automated tests with sample `.3df`/`.car` files
- Edge case coverage (empty meshes, invalid textures, cyclic bones)

---

## Known Issues

1. **Edit Mode Switching**: Flag operators switch to Object Mode temporarily, disrupting workflow. Workaround: restore Edit Mode in `finally` blocks.
2. **Texture Limitations**: Must be 256px wide (enforced on export). No import validation/resize.
3. **Bone Name Handling**: Non-ASCII/long names cleaned/truncated → possible conflicts. Duplicate names warned but not resolved.
4. **Validation Limits**: >1024 verts/faces triggers warning (AltEdit compat), not capped unless >2048. Unknown header fields logged as warnings.
5. **UV Flipping**: UVs flipped on import to match Blender space. Export supports optional U/V flip, may confuse new users.

---

## Development Decisions

- **NumPy**: Chosen for performance on large arrays. Avoid Python loops.
- **BMesh**: Used for Edit Mode operations to access selected faces efficiently.
- **Validation Toggle**: Optional import validation (`validate` property) for trusted files.
- **Hook vs. Armature**: Hooks default for simplicity; armatures supported for advanced rigging.
- **Error Reporting**: Warnings collected in `ParserContext.warnings`, displayed via modal dialog.

---

## Debugging Tips

- **Enable Timing**: Use `@timed` decorator output in console for bottlenecks.
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
- **Blender API**: Blender 4.0+ Python API docs for `bpy`, `bmesh`, `mathutils`
- **NumPy**: Documentation for `fromfile`, `reshape`, bitwise operations
