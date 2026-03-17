# CarnivoresIO Developer Notes

This document provides technical details, implementation notes, and development considerations for the CarnivoresIO Blender add-on, which supports importing and exporting `.3df` files for the Carnivores game series.

## Project Overview

CarnivoresIO is a Blender add-on for handling `.3df` files, including mesh geometry, face flags, UVs, bones, and ARGB1555 textures. It integrates with Blender’s UI via custom panels and operators, leveraging NumPy for efficient data processing and BMesh for mesh manipulation.

- **Blender Version**: 4.0+
- **Dependencies**: `numpy` (bundled with Blender), `bpy`, `bpy_extras.io_utils`, `mathutils`, `bmesh`
- **Modules**:
  - `__init__.py`: Add-on registration and UI property definitions.
  - `operators_part1.py`, `operators_part2.py`, `operators_part3.py`: Operators for import/export, flag management, and selection.
  - `utils_part1.py`, `utils_part2.py`: Utility functions for mesh creation, bone handling, and texture processing.
  - `parse_3df.py`: Parsing logic for `.3df` files with validation.
  - `export_3df.py`: Export logic for `.3df` files.
  - `core/constants.py`: Constants (`FACE_FLAG_OPTIONS`, `TEXTURE_WIDTH`).
  - `core/core.py`: NumPy dtype definitions for `.3df` file structure.

## Implementation Details

### File Format Handling
- **Parsing (`parse_3df.py`)**:
  - Uses NumPy’s `fromfile` for efficient binary reading of header, faces, vertices, bones, and textures.
  - Flips vertex indices and UV coordinates (`v`, `u_tex`, `v_tex`) to match Blender’s coordinate system.
  - Validates data integrity (e.g., vertex/face counts, bone cycles) with optional repairs (e.g., clamping invalid indices).
  - Texture parsing converts ARGB1555 to RGBA for Blender compatibility.
  - Bone names are cleaned (ASCII-only, truncated to 32 bytes) to prevent issues.

- **Exporting (`export_3df.py`)**:
  - Ensures meshes are triangulated using `triangulated_mesh_copy`.
  - Converts Blender UVs to `.3df` integer coordinates (0-255 for U, scaled for V based on texture height).
  - Supports bone export from armatures or hooks, with vertex group mapping.
  - Texture export converts Blender images to ARGB1555, requiring 256-pixel width.

### Face Flags
- **Storage**: Face flags are stored as a face-domain `INT` attribute (`3df_flags`) on the mesh.
- **UI**: The `VIEW3D_PT_3df_face_flags` panel displays flag counts and provides set/clear/toggle operators.
- **Operators**: `CARNIVORES_OT_set_3df_flag`, `CARNIVORES_OT_clear_3df_flag`, and `CARNIVORES_OT_toggle_3df_flag` modify flags for selected faces (Edit Mode) or all faces (Object Mode).
- **Selection**: `CARNIVORES_OT_select_by_flags` uses NumPy for efficient flag-based face selection with modes (`ANY`, `ALL`, `NONE`) and actions (`SELECT`, `DESELECT`, `INVERT`).

### Bone Handling
- **Import**: Bones can be imported as hooks (`HOOKS`) or armatures (`ARMATURE`). Hooks use vertex groups and modifiers; armatures use Blender’s armature system.
- **Export**: Bones are collected from armatures or hooks, with vertex ownership mapped via vertex groups. Handles Blender’s `.001` suffix naming convention.
- **Validation**: Detects bone hierarchy cycles and clamps invalid parent indices to `-1`.

### Performance
- **NumPy**: Used extensively for array operations (e.g., vertex transformations, flag manipulation, texture conversion).
- **Timing**: The `@timed` decorator logs execution times for key functions (e.g., import/export).
- **BMesh**: Used for Edit Mode operations to access selected faces and update mesh data efficiently.

### UI Integration
- **Panels**: `VIEW3D_PT_3df_face_flags` and `VIEW3D_PT_carnivores_selection` in the 3D Viewport Sidebar (`Carnivores` tab).
- **Properties**: Scene properties (`cf_flag_*`, `cf_select_mode`, `cf_select_action`) dynamically registered for flag selection and modes.
- **Import/Export**: Integrated into Blender’s `File > Import/Export` menus.

## Known Issues

1. **Edit Mode Switching**:
   - Flag modification operators (`set`, `clear`, `toggle`) switch to Object Mode temporarily to ensure attribute consistency, which may disrupt user workflow.
   - Workaround: Restore Edit Mode in `finally` blocks, but this can fail if Blender’s context changes unexpectedly.

2. **Texture Limitations**:
   - Textures must be 256 pixels wide, enforced during export. No support for resizing or validation during import.
   - Alpha channel in ARGB1555 textures is parsed but not fully utilized in-game (assumed unused).

3. **Bone Name Handling**:
   - Non-ASCII or long bone names are cleaned/truncated, which may lead to naming conflicts or loss of information.
   - Duplicate bone names are warned but not resolved, relying on Blender’s merging behavior.

4. **Validation Limits**:
   - High vertex/face counts (>1024) trigger warnings due to AltEdit compatibility but are not capped unless exceeding 2048.
   - Non-zero `dmask`, `distant`, `next`, `group`, or `reserv` fields are logged as warnings but not modified, as their purpose is unknown.

5. **UV Flipping**:
   - UV coordinates are flipped (`v_tex` inverted) during import to match Blender’s UV space. Export supports optional U/V flipping, but the default behavior may confuse users unfamiliar with the `.3df` format.

## Development Decisions

- **NumPy Usage**: Chosen for performance in handling large arrays (vertices, faces, textures). Avoids Python loops where possible.
- **BMesh for Edit Mode**: Used to access selected faces and update flags in Edit Mode, ensuring compatibility with Blender’s mesh editing workflow.
- **Validation Toggle**: Import validation is optional (`validate` property) to allow faster imports when file integrity is trusted.
- **Hook vs. Armature**: Hooks are the default bone import type for simplicity and compatibility with `.3df`’s lightweight bone system, but armatures are supported for advanced rigging.
- **Error Reporting**: Warnings are collected during parsing and displayed via a modal dialog (`CARNIVORES_OT_modal_message`) to avoid silent failures.

## Future Improvements

1. **Direct Edit Mode Flag Editing**:
   - Modify `bulk_modify_flag` to work directly with BMesh in Edit Mode, avoiding mode switches.
   - Potential Approach: Use BMesh layers (`bm.faces.layers.int`) consistently for all flag operations.

2. **Texture Enhancements**:
   - Add texture resizing during export to enforce 256-pixel width.
   - Support alpha channel rendering in Blender materials (currently ignored except for `sfOpacity` flag).

3. **Bone Name Resolution**:
   - Implement unique name generation for duplicate or invalid bone names during import/export.
   - Add UI option to rename bones before export.

4. **Validation Customization**:
   - Allow users to configure validation thresholds (e.g., vertex/face count limits) via preferences.
   - Provide options to automatically fix issues (e.g., remove degenerate faces) during import.

5. **Performance Optimization**:
   - Optimize `foreach_get`/`foreach_set` calls by batching attribute updates.
   - Cache frequently accessed data (e.g., `3df_flags` values) to reduce redundant reads.

6. **Testing Suite**:
   - Develop automated tests with sample `.3df` files to verify import/export fidelity.
   - Test edge cases (e.g., empty meshes, invalid textures, cyclic bone hierarchies).

7. **Documentation**:
   - Add inline comments to clarify complex functions (e.g., `collect_bones_and_owners`).
   - Create a separate `.3df` specification document to decouple it from code comments.

## Debugging Tips

- **Enable Timing**: Use the `@timed` decorator output in the console to identify performance bottlenecks.
- **Check Warnings**: Review `ParserContext.warnings` during import for insights into file issues.
- **Inspect Attributes**: Verify the `3df_flags` attribute in Blender’s Data API or Outliner to debug flag operations.
- **Console Output**: Look for `[Export]` or `[Warning]` prefixes in the console for bone or texture issues.

## Contributing

- **Code Style**: Follow PEP 8 with 4-space indentation. Use descriptive variable names and comment complex logic.
- **Testing**: Test changes with `.3df` files containing various edge cases (e.g., high vertex counts, empty textures).
- **Pull Requests**: Include a description of changes, affected files, and test results. Reference any fixed issues.
- **Issues**: Report bugs or feature requests on the GitHub repository, including steps to reproduce and sample files if possible.

## References

- **`.3df` Format**: See the specification in the code comments (e.g., `core/core.py`) for detailed field descriptions.
- **Blender API**: Refer to Blender 4.0 Python API docs for `bpy`, `bmesh`, and `mathutils`.
- **NumPy**: Used for array operations; see NumPy documentation for `fromfile`, `reshape`, and bitwise operations.

