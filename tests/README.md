# Tests

Pure owner-mapping and validation tests run with Blender's bundled Python because the project depends on Blender's NumPy installation.

From the repository root in Git Bash:

```bash
WINPWD=$(pwd -W)
../../../../blender.exe --background --factory-startup --python-expr \
  "import sys,unittest; p=r'$WINPWD\\tests'; sys.path.insert(0,p); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(p)); raise SystemExit(0 if result.wasSuccessful() else 1)"
```

The tests load pure source modules directly and do not register the addon or modify Blender user preferences.
