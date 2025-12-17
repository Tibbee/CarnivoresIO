# Proposed Future Enhancements for CarnivoresIO Blender Add-on

This document outlines a series of proposed enhancements and new features for the CarnivoresIO Blender add-on, building upon the recently stabilized core import/export functionality. The goal is to improve the user experience, provide better debugging tools, and streamline common modding workflows.

---

## Phase 1: Enhanced Visualization & Debugging (The "See What You Get" Update)

This phase focuses on making otherwise invisible or complex game-specific data more accessible and understandable directly within the Blender viewport.

### 1.1. Face Flag Heatmap / Viewport Overlay

*   **Problem:** Carnivores `.3df` and `.car` models utilize `3df_flags` (e.g., `WATER`, `NO_COLLISION`, `DEATH`) that are crucial for in-game behavior. Currently, identifying which faces have which flags relies on the "Select Faces by 3DF Flags" tool, which only selects. It doesn't offer a persistent visual overview.
*   **Proposed Solution:** Implement a custom Blender Viewport Overlay that colors faces based on their assigned `3df_flags`.
    *   **Features:**
        *   Option to toggle the overlay on/off in the viewport properties.
        *   Each major flag (or combination) could have a distinct, semi-transparent color (e.g., blue for water, red for death zones, green for specific material types).
        *   A legend in the overlay or sidebar explaining the color mapping.
        *   Interactive feedback: Selecting a face could highlight its flags in the Carnivores panel.
*   **Benefit:** Provides immediate visual feedback on game logic embedded in the mesh, simplifying debugging and level design.

### 1.2. Model "Health Check" & Pre-Export Validation Tool

*   **Problem:** Game engines often have strict requirements for assets (e.g., non-convex geometry, non-power-of-two textures, vertex count limits) that can lead to crashes or unexpected behavior. Manually checking these is tedious and error-prone.
*   **Proposed Solution:** A dedicated "Validate Model" operator that runs a suite of checks against common Carnivores engine limitations and best practices.
    *   **Features:**
        *   Check: Texture dimensions (power of two).
        *   Check: Mesh convexity (if required).
        *   Check: Vertex count limits (if the engine has limits like 32k).
        *   Detect N-gons or quads if the target game only supports triangles.
        *   (Advanced) Check if the model's bounding box/sphere is reasonable for game physics.
        *   Generate a user-friendly report with warnings and suggested fixes.
*   **Benefit:** Reduces export-related errors, saves iteration time, and improves model compatibility.

---

## Phase 2: Animation Workflow (The "Animator's Toolkit")

This phase focuses on streamlining the management of NLA (Non-Linear Animation) tracks and their associated properties (KPS, sound), recognizing that animation order often matters for `.car` exports.

### 2.1. Unified Carnivores Animation Panel (Refactored from Audio Panel)

*   **Problem:** Animation-related settings (NLA tracks, KPS, sounds) are currently scattered or require deep diving into Blender's generic animation editors. The previous "Audio" panel was limited.
*   **Proposed Solution:** Transform the existing `VIEW3D_PT_carnivores_audio` panel into a comprehensive `VIEW3D_PT_carnivores_animation` panel.
    *   **Features:**
        *   **Global NLA Sound Playback Toggle:** Retain the existing enable/disable switch for real-time sound preview.
        *   **NLA Track List:** A custom `UIList` to display all NLA tracks associated with the active object's animation data.
            *   Tracks will be displayed in the *export order* (top-most in UI = first exported).
            *   Each list item shows the track name and a mute toggle.
        *   **Track Reordering:** "Move Up" and "Move Down" buttons next to the list to physically reorder NLA tracks, directly influencing the export sequence.
        *   **Selected Track Details:** When an NLA track is selected in the list, a dedicated section expands to show its properties:
            *   **Action Name:** Editable field for the Action linked to the track's first strip.
            *   **Sound Link:** The sound datablock selector (moved from old audio panel), allowing users to easily assign/change the linked sound for the track's first strip's action.
            *   **KPS (Keys Per Second) Override:** A numerical input field (`IntProperty`) for `carnivores_kps` custom property on the Action.
                *   Displays "Auto (Scene FPS)" if the property is not set.
                *   An "Override" button to create/set the property.
                *   A "Reset" button (X icon) to remove the `carnivores_kps` property, reverting to auto.
            *   **Play Track Preview:** A button to temporarily solo the selected NLA track and play its animation with linked sound in Blender's viewport.

### 2.2. Batch Action Renaming/Cleanup

*   **Problem:** Imported animations often have Blender's `.001`, `_Action` suffixes, and general naming inconsistencies. Manually cleaning these is tedious.
*   **Proposed Solution:** Add an operator to batch-rename selected actions or actions within selected NLA tracks.
    *   **Features:**
        *   Remove suffixes like `.001`, `_Action`.
        *   Convert spaces to underscores.
        *   Apply a common prefix/suffix.
*   **Benefit:** Cleaner asset management and adherence to game naming conventions.

## Phase 4: Codebase Architecture (Maintenance & Future-Proofing)

Ensuring the add-on remains maintainable and scalable as more features are added.

### 4.1. Refactor `operators.py` into Modular Files

*   **Problem:** The `operators.py` file is growing very large, containing importers, exporters, UI panels, and various helper operators. This makes it harder to navigate, maintain, and test.
*   **Proposed Solution:** Split `operators.py` into a more modular structure within a `carnivores_ops/` directory (or similar).
    *   `carnivores_ops/__init__.py`: Handles local registration.
    *   `carnivores_ops/import_ops.py`: Contains `CARNIVORES_OT_import_3df`, `CARNIVORES_OT_import_car`.
    *   `carnivores_ops/export_ops.py`: Contains `CARNIVORES_OT_export_3df`, `CARNIVORES_OT_export_car`.
    *   `carnivores_ops/animation_ops.py`: Contains all animation-related operators (move track, KPS, play preview).
    *   `carnivores_ops/ui_ops.py`: Contains face flag operators and modal messages.
*   **Benefit:** Improves code organization, readability, reduces merge conflicts, and simplifies future feature development and debugging.

---
This comprehensive plan aims to address key usability, visualization, and workflow challenges, making the CarnivoresIO add-on a more powerful and user-friendly tool for content creators.
