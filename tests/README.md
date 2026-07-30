# Tests

Owner-mapping, validation, pure rig-geometry/hierarchy, and Blender-adapter tests run with Blender's bundled Python because the project depends on Blender and its NumPy installation.

From the repository root in Git Bash:

```bash
WINPWD=$(pwd -W)
../../../../blender.exe --background --factory-startup --python-expr \
  "import sys,unittest; p=r'$WINPWD\\tests'; sys.path.insert(0,p); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(p)); raise SystemExit(0 if result.wasSuccessful() else 1)"
```

The tests import the addon modules but do not register the addon or modify Blender user preferences.
