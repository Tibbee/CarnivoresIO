"""Helpers for resolving CarnivoresIO preferences in Blender add-ons/extensions."""

import bpy


def addon_package_name(package_name):
    """Return the add-on root from a module package name.

    Traditional installs look like ``carnivores_io.utils`` while Blender
    extensions are namespaced, for example
    ``bl_ext.user_default.carnivores_io.utils``.  In both cases the final
    package component is the utility/operator subpackage.
    """
    package_name = str(package_name or "")
    if "." not in package_name:
        return package_name
    return package_name.rsplit(".", 1)[0]


def get_addon_preferences(package_name):
    """Return this add-on's preferences, or ``None`` if unavailable."""
    addon_name = addon_package_name(package_name)
    if not addon_name:
        return None
    try:
        addon = bpy.context.preferences.addons.get(addon_name)
        return addon.preferences if addon else None
    except (AttributeError, RuntimeError, TypeError):
        return None
