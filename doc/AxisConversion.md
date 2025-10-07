## Handedness Conversion: X-Axis Flip and Winding Order Fix

### Background
Carnivores uses a left-handed coordinate system (typical for Direct3D-based engines from the late 1990s), where the axes follow a left-hand rule: thumb (+X, left), index (+Y, up), middle (-Z, forward). Blender, however, is right-handed (right-hand rule: thumb +X left, index +Y forward, middle +Z up). Exporting models directly from Blender to .3df without adjustment results in a **mirrored** appearance in Designer 2 or in-game—e.g., an axis gizmo's +X arrow points left instead of right when aligned with Y forward in front view.

To correct this mirroring without full axis remapping (which could misalign up/forward), we apply a **handedness flip** by scaling the X-axis by -1. This mirrors the model across the YZ plane, swapping left/right chirality. The transformation is integrated into the import/export matrices via a user-toggleable BoolProperty (`flip_handedness`) in the operators.

However, this flip inverts the **face winding order**:
- Blender exports triangles in counter-clockwise (CCW) winding for front-facing (right-handed view).
- Post-flip, the mirrored vertex positions make the winding appear clockwise (CW) in the left-handed game space.
- Carnivores' renderer (D3D) enables backface culling by default, discarding CW faces → the mesh renders "hollow" (invisible from the front, visible only from behind).

### Solution
- **X-Flip**: Applied conditionally via `mathutils.Matrix.Scale(-1, 4, (1, 0, 0))` in the transformation matrices (in `operators_part1.py`, `CARNIVORES_OT_import_3df.execute` and `CARNIVORES_OT_export_3df.execute`). Inserted between axis conversion and scaling for modularity.
- **Winding Fix**: When `flip_handedness` is enabled, reverse the vertex index order (`faces_arr['v'][:, ::-1]`) and corresponding UV arrays (`u_tex` and `v_tex`) in the .3df data. This swaps CCW → CW pre-flip, resulting in CCW post-flip (visible fronts).
  - On **export** (`parsers/export_3df.py`, after collecting face vertices): Reverse `v`, `all_us`, and `all_vs` arrays.
  - On **import** (`parsers/parse_3df.py`, in `parse_3df_faces`): Reverse after reading raw data, for round-trip consistency.
- **UI Integration**: The `flip_handedness` prop appears in import/export dialogs (after `flip_v` in `draw`). Defaults to `False` to match recent diffs (no global flip); enable for standard Carnivores exports. Pairs with `axis_forward='-X'` and `axis_up='Z'` for gizmo alignment (Y forward, X right, Z up).

### Code References
- **Operators** (`operators_part1.py`): Prop definition (~line 110/220), matrix composition in `execute` (~line 130/240), and prop passing to `parse_3df`/`export_3df`.
- **Export** (`parsers/export_3df.py`): Function sig update (`flip_handedness=False`), conditional reversal after `face_verts_flat` (~line 50), and UV handling.
- **Import** (`parsers/parse_3df.py`): Function sig updates (`flip_handedness=False` in `parse_3df_faces` and main `parse_3df`), conditional reversal post-`np.fromfile` (~line 30), and prop pass in main call (~line 60).

### Testing Notes
- **Gizmo Round-Trip**: Export axis gizmo with flip enabled + axis_forward='-X' + axis_up='Z'; import back—should overlay original without mirroring or culling.
- **Edge Cases**: 
  - Double-sided faces (`sfDoubleSide` flag, bit 0) ignore culling; test with/without.
  - UV shearing: Ensure reversal happens *after* V-flip (`1.0 - all_vs`) but *before* optional `flip_u/v`.
  - No-flip mode: Preserves Blender winding for right-handed tools or custom specs.
- **Perf**: Reversal is O(n) on faces (negligible for <2048 limit); no impact on validation.

This approach keeps the addon flexible (toggle per-file) and spec-compliant (.3df doesn't enforce winding). If Carnivores docs surface (unlikely), revisit defaults.