# Blender 5.0 Migration Guide for CarnivoresIO Add-on

## Problem Statement

Upon upgrading Blender to version 5.0, users encountered several issues when importing `.car` models using the CarnivoresIO add-on:

1.  **`AttributeError: 'Action' object has no attribute 'fcurves'`**: This error occurred during the animation import process, causing the import to fail.
2.  **Broken Model / Missing Animations**: Imported models appeared "upside down" and animations were not correctly recognized or displayed in the NLA editor, with only a single NLA action showing without any strips, and the UI panel for animations remaining empty.

## Root Cause Analysis

Blender 5.0 introduced significant breaking changes to its Python API, particularly concerning the `bpy.types.Action` object. The `action.fcurves` property, which was previously used to access and manipulate F-Curves directly on an Action, was completely removed.

Instead, in Blender 5.0, F-Curves are now managed through a more granular system involving **Action Slots** and **Channelbags**. Each `Action` can have multiple `slots`, and each `slot` can contain a `channelbag` which then holds the `fcurves` for a specific data block type (e.g., `KEY` for shape keys, `POSE` for pose bones, etc.). Direct dictionary-like access to `action.fcurves` is no longer supported.

The "upside down" model issue was likely a secondary symptom, possibly arising from the animation system failing to initialize correctly or conflicting with other transformations when animations were expected but not properly applied. Once the core animation API issue was resolved, the model orientation corrected itself, suggesting it was related to how the animated state was being set up.

## Solution Steps

The following modifications were implemented in `utils.py` to adapt to the Blender 5.0 API changes:

### 1. Introduction of `get_action_channelbag` Helper Function

A new helper function, `get_action_channelbag`, was introduced to encapsulate the logic for retrieving or creating the appropriate `channelbag` for shape key animations within an `Action`.

-   **Location**: `utils.py`
-   **Purpose**: This function ensures that a specific `Action Slot` of type `KEY` (for shape keys) and with a designated name ("ShapeKeys") exists for a given `Action`. It then returns the `channelbag` associated with that slot, using `bpy_extras.anim_utils.action_ensure_channelbag_for_slot`.

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

### 2. Refactoring `keyframe_shape_key_animation_as_action`

The `keyframe_shape_key_animation_as_action` function was updated to utilize the new `get_action_channelbag` helper and the `channelbag.fcurves` interface.

-   **Location**: `utils.py`, `keyframe_shape_key_animation_as_action` function.
-   **Changes**:
    *   Before creating any F-Curves or clearing existing ones, a `channelbag` is obtained for the `action` using `get_action_channelbag(action)`.
    *   All instances of `action.fcurves.clear()` were replaced with `channelbag.fcurves.clear()`.
    *   All instances of `action.fcurves.new(...)` were replaced with `channelbag.fcurves.new(...)`.

### 3. Refactoring `get_action_frame_range`

The `get_action_frame_range` function, which determines the overall frame span of an animation, was updated to correctly retrieve F-Curves from the new Action Slot/Channelbag structure.

-   **Location**: `utils.py`, `get_action_frame_range` function.
-   **Changes**:
    *   Instead of directly accessing `action.fcurves`, the function now iterates through all `action.slots`.
    *   For each slot, it retrieves its `channelbag` using `bpy_extras.anim_utils.action_get_channelbag_for_slot(action, slot)`.
    *   It then collects all F-Curves from these channelbags and processes their keyframe points to determine the minimum and maximum frame numbers.
    *   A fallback for legacy `action.fcurves` was also included for broader compatibility, although the primary fix targets Blender 5.0.

    ```python
    def get_action_frame_range(action):
        if not action:
            return (1, 1)

        all_fcurves = []
        if hasattr(action, "slots"): # Blender 5.0+ support
            for slot in action.slots:
                cb = anim_utils.action_get_channelbag_for_slot(action, slot)
                if cb:
                    all_fcurves.extend(cb.fcurves)
        elif hasattr(action, "fcurves"): # Legacy support (for older Blender versions)
            all_fcurves = action.fcurves

        if not all_fcurves:
            return (1, 1)

        frames = [kp.co[0] for fc in all_fcurves for kp in fc.keyframe_points]
        
        if not frames:
            return (1, 1)
            
        return (int(min(frames)), int(max(frames)))
    ```

## Verification

The changes were verified through an iterative process:

1.  **Isolated Test Scripts**: Small Python scripts (`test_action_api.py`, `verify_fix.py`, `test_nla_push.py`) were created and executed in Blender's text editor. These scripts confirmed the correct syntax and behavior of `Action Slots`, `channelbag` creation, and `F-Curve` management in Blender 5.0, as well as the successful push of an `Action` to the NLA editor.
2.  **Full Add-on Test**: After applying the fixes, the user re-imported a `.car` model into Blender using the CarnivoresIO add-on. The animation data was correctly imported, the NLA editor displayed the animation strips, and the model appeared with the correct orientation, indicating a complete resolution of the reported issues.
