# Phase 5 Fix Continuation

> **Status: historical record.** The fixes described here were completed and committed as `d4d9112` ("fix(rig): complete phase 5 reconstruction lifecycle"). For the current committed state, use [RIG_RECONSTRUCTION_HANDOFF.md](RIG_RECONSTRUCTION_HANDOFF.md) and [RIG_RECONSTRUCTION_PLAN.md](RIG_RECONSTRUCTION_PLAN.md).

## Current state

This session reviewed and fixed the Phase 5 rig-reconstruction findings from `doc/RIG_RECONSTRUCTION_PLAN.md`.

At the time of writing (pre-`d4d9112`), the working tree was intentionally uncommitted. The pre-existing untracked `.commandcode/` directory was present and was not touched.

Last verification:

```text
Blender 5.2 background unittest discovery: 65 tests, OK
`git diff --check`: OK
```

Command used:

```powershell
D:/Portable/Blender/blender.exe --background --python-expr "import sys,unittest; sys.path.insert(0,'D:/Portable/Blender/portable/extensions/user'); suite=unittest.defaultTestLoader.discover('D:/Portable/Blender/portable/extensions/user/carnivores_io/tests'); result=unittest.TextTestRunner(verbosity=0).run(suite); sys.exit(0 if result.wasSuccessful() else 1)"
```

## Files changed by the fixes

- `utils/io.py`
  - Added Blender context snapshot/restore helpers.
  - Armature construction now validates inputs, builds replacement data transactionally, cleans partial objects, and restores context.
  - `UPDATE_GENERATED` builds temporary armature data and swaps it into the existing object after success.
  - Replaced fixed bone-length fallback with geometry-scaled minimum lengths.
  - Added projected/deterministic roll alignment and `get_bone_roll()` for data-bone diagnostics.
  - `assign_armature_modifier()` reuses duplicate matching modifiers and preserves mesh world matrix when parenting.
  - Added deferred `finalize_reconstruction_lifecycle()` for CREATE/REPLACE policy cleanup.
  - Export name collection consumes the generated Blender/export name map only when it still matches the current Blender bone set; stale maps fall back to current names.
  - UPDATE data swaps now retain old armature data, actions, and constraints until commit, and restore them on later adapter failure.

- `utils/animation.py`
  - Added source IDs and generated-rig discovery guarded by algorithm/source metadata.
  - Both Legacy and Topology paths now use the same `.get()`-first reconstruction setting helper and early lifecycle validation.
  - Added owner-index-based vertex-group renaming; no string-only weight reconciliation.
  - Added separate Blender names and ASCII/31-byte export names, with serialized name mapping.
  - Added serialized compact/raw owner mapping, raw parent/skipped metadata, skipped reasons, and Legacy metadata versioning.
  - Added reconstruction state snapshots/rollback wrappers for vertex groups, mesh binding, metadata, partial armatures, and Blender context.
  - Added robust Legacy median/PCA/continuation joint geometry.
  - Topology root heads now use robust median/MAD centers; continuation selection includes body-axis, centrality, confidence, and semantic evidence.
  - Both paths now pass deterministic roll references.

- `utils/rig_reconstruction.py`
  - Fixed tiny-set roll reference test (`len()` instead of scalar `ndarray.size`).
  - Added finite SVD fallback handling.
  - Improved robust topology root heads and continuation scoring.

- `operators/animation.py`
  - Skipped-group reports include reasons, vertex counts, and details.
  - Added explicit `Remove Skipped Groups` cleanup action; it only removes groups named in recorded metadata and never guesses by compact ID.
  - Debug reports use `get_bone_roll()`, show raw/compact skipped metadata and name maps, and read JSON parent maps with Legacy `ast` fallback.

- `utils/validation.py`
  - Generated rigs are validated against serialized export names while matching vertex groups against actual Blender names.

- `tests/test_rig_adapter.py`, `tests/test_rig_geometry.py`
  - Fixed RNA/custom-property test setup behavior indirectly through the common setting helper.
  - Added tests for UPDATE, shared-rig protection, post-swap rollback of action/constraints, non-generated rig protection, parent/nonuniform transforms, transaction input failure, Unicode/long-name collisions, export-name mapping, context restoration, skipped-group cleanup, and tiny roll references.

## Important implementation details

- Current generated armature metadata includes:
  - `carnivores_rig_algorithm`
  - `carnivores_rig_algorithm_version`
  - `carnivores_rig_metadata_version`
  - `carnivores_reconstruct_source_id`
  - `carnivores_reconstruct_source_mesh`
  - `carnivores_reconstruct_owner_mapping`
  - `carnivores_reconstruct_bone_name_map`
- `CREATE_NEW` preserves the old generated armature object but removes its old active modifier from the target mesh, avoiding double deformation.
- `REPLACE_GENERATED` only deletes the old generated armature after successful assignment and only if no other object/constraint/driver references it.
- Unrelated user armatures are refused rather than automatically updated/replaced.
- Blender-side names preserve source Unicode/long names up to Blender’s name limit; export names are separate ASCII-safe names. Validation and export use the serialized export mapping.

## Suggested next steps after compaction

1. Read this document, then inspect the complete diff carefully, especially:
   - `utils/animation.py` around `_snapshot_reconstruction_state`, `_reconstruct_armature_topology_impl`, and `_reconstruct_armature_impl`.
   - `utils/io.py` around `_create_armature_impl`, `create_armature`, and `finalize_reconstruction_lifecycle`.
2. Run the full Blender test command above again after any edits.
3. Run the repository build command if release/build validation is needed:

   ```powershell
   .\tools\build_repo.ps1
   ```

4. Consider a second Blender-version check (4.2/4.3) for `Bone.AxisRollFromMatrix`, EditBone operations, Blender name limits, and the newly registered cleanup operator; current runtime verification is Blender 5.2.
5. Review performance of the reconstruction state snapshot for very large meshes; it currently records per-vertex group assignments so failed reconstruction can restore weights exactly.
6. Decide whether `CREATE_NEW` should reject or explicitly support a non-generated user armature. Current safe behavior is to reject all meshes with unrelated armature bindings.
7. The repository build command was run successfully for validation, but its generated `public_repo` artifacts were restored afterward because the build archive also included the pre-existing `.commandcode/` and large test resources.
