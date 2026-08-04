# Tests

Owner-mapping, validation, pure rig-geometry/hierarchy, and Blender-adapter tests run with Blender's bundled Python because the project depends on Blender and its NumPy installation.

From the repository root in Git Bash:

```bash
WINPWD=$(pwd -W)
../../../../blender.exe --background --factory-startup --python-expr \
  "import sys,unittest; sys.path.insert(0,r'$WINPWD\\..'); import carnivores_io; carnivores_io.register(); p=r'$WINPWD'; sys.path.insert(0,p); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(p)); carnivores_io.unregister(); raise SystemExit(0 if result.wasSuccessful() else 1)"
```

The suite registers the addon because several operator tests call `bpy.ops.carnivores.*`. It does not modify Blender user preferences.
