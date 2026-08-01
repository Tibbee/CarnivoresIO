from pathlib import Path
import sys
import unittest

PACKAGE_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(PACKAGE_ROOT.parent))
from carnivores_io.utils.addon import addon_package_name


class AddonPreferenceResolutionTests(unittest.TestCase):
    def test_traditional_package_name(self):
        self.assertEqual(
            addon_package_name("carnivores_io.utils"),
            "carnivores_io",
        )

    def test_blender_extension_package_name(self):
        self.assertEqual(
            addon_package_name("bl_ext.user_default.carnivores_io.utils"),
            "bl_ext.user_default.carnivores_io",
        )

    def test_root_package_name(self):
        self.assertEqual(addon_package_name("carnivores_io"), "carnivores_io")


if __name__ == "__main__":
    unittest.main()
