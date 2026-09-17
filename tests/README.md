# Tests

Owner-mapping, validation, pure rig-geometry/hierarchy, and Blender-adapter tests run with Blender's bundled Python because the project depends on Blender and its NumPy installation.

From the repository root in Git Bash:

```bash
WINPWD=$(pwd -W)
../../../../blender.exe --background --factory-startup --python-expr \
  "import sys,unittest; sys.path.insert(0,r'$WINPWD\\..'); import carnivores_io; carnivores_io.register(); p=r'$WINPWD'; sys.path.insert(0,p); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(p)); carnivores_io.unregister(); raise SystemExit(0 if result.wasSuccessful() else 1)"
```

The suite registers the addon because several operator tests call `bpy.ops.carnivores.*`. It does not modify Blender user preferences.

The suite currently holds **118 tests across 11 files**:

| File | Coverage |
|---|---|
| `test_rig_adapter.py` | Blender adapter contract: Legacy/Topology reconstruction, lifecycle policies, transactional updates, proposal storage, export-name mapping, dry-run owner mapping |
| `test_rig_geometry.py` | Pure geometry: centroids, PCA/SVD, boundary joints, rolls |
| `test_rig_hierarchy.py` | Pure hierarchy: MST scoring, mirror pairing, root selection |
| `test_rig_reconciliation.py` | Round-trip reconciliation levels and dry-run diagnostics |
| `test_performance_optimizations.py` | CAR import/export fast paths, fractional endpoints, sound byte preservation |
| `test_structural_validation.py` | Non-destructive structural validation of binary input |
| `test_car_validation.py` | CAR-specific validation (owners, counts, texture rows) |
| `test_audio_playback_selection.py` | NLA-focused audio playback selection |
| `test_addon_preferences.py` | Preference registration and defaults |
| `test_owner_mapping.py` | Raw → compact owner mapping |
| `test_release_fixes.py` | Import/export robustness fixes: atomic writes, name sanitization, fast-path modifier rule, empty textures, loud CAR animation failures, absolute resync |
