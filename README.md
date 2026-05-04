# CarnivoresIO

Blender extension (4.2+) for importing, editing, and exporting models from the *Carnivores* game engine. Supports `.3df`, `.car`, and `.3dn` formats with full animation, rigging, and audio integration.

---

## Compatibility

| Requirement | Version |
|---|---|
| Blender | 4.2 to 6.0 |
| Python dependencies | NumPy (bundled with Blender) |

Blender 3.x and earlier are not supported due to the Extensions API introduced in 4.2.

---

## Installation

**Option A -- Release ZIP**

1. Download `carnivores_io-<version>.zip` from the [Releases](https://github.com/Tibbee/CarnivoresIO/releases) page.
2. In Blender, open **Edit > Preferences > Get Extensions**.
3. Drag and drop the `.zip` file into the window, or use the menu to install from disk.

**Option B -- Dev Channel (auto-updates)**

1. In Blender, navigate to **Preferences > File Paths > Extensions**.
2. Click the **+** icon to add a new **Remote Repository**.
3. Name it and paste this URL:
   `https://raw.githubusercontent.com/Tibbee/CarnivoresIO/refs/heads/main/public_repo/index.json`

Enable **Check for updates on startup** under the Extensions tab to sync automatically.

---

## Features

### Format Support

| Format | Import | Export | Description |
|---|---|---|---|
| `.3df` | Yes | Yes (single + multi-export) | Static models (map objects, decorations) |
| `.car` | Yes | Yes | Animated models with vertex animations, embedded audio, and vertex ownership data |
| `.3dn` | No | Yes | Static models for mobile/HD Carnivores titles |
| `.vtl` | No | Yes | Standalone vertex animation export |

All binary I/O uses NumPy for performance. Textures use the ARGB1555 format at a fixed 256-pixel width. Configurable axis conversion and a **Flip Handedness** option handle the engine's left-handed coordinate system.

### Skeleton Reconstruction

A Prim's MST algorithm infers a bone hierarchy from the spatial centroids of vertex groups. The operator calculates a weighted center for each vertex group, then connects them via a symmetry-aware minimum spanning tree that penalizes connections crossing the X=0 midline. A bone named "floor" (case-insensitive) is automatically selected as the root. The resulting armature is parented to the mesh with an Armature modifier in a single step.

### Animation Pipeline

- Vertex animations from `.car` files are converted to Shape Key Actions with NLA strips during import.
- Absolute or Relative shape key modes are supported.
- Each Action carries a **KPS (Keys Per Second)** mode: auto-detect from scene FPS or override with a custom value. KPS-aligned keyframes land on sub-frame positions for exact engine timing.
- **Re-Sync Timing** fully re-bakes all keyframes from shape key data using the current KPS, then updates NLA strip ranges.
- `.vtl` export for standalone animation files.

### NLA Sound System

- Each Action can carry a linked `.wav` file for synchronized audio playback.
- Sound plays during timeline scrubbing and full animation playback.
- **Play Preview** loops a single Action with its audio for iterative timing work.
- Self-healing audio device management recovers from driver failures.
- Embedded sounds from `.car` files are imported and auto-associated with the correct animations via cross-reference tables.

### Face Flags

- Engine face property bits (Double Side, Phong, Transparent, Mortal, etc.) are stored as the integer face-domain attribute `3df_flags` on the mesh.
- Flag visualization renders flags as color attributes in the viewport.
- Bulk modification: set, clear, or toggle individual flags across selectable faces.
- Smart selection: find faces by Any, All, or None flag pattern using bitmask logic, then select, deselect, or invert.

### Multi-Export

Export all selected mesh objects to separate `.3df` files in one operation.

### Validation

Optional import validation checks vertex/face/bone count limits, file size integrity, UV ranges, degenerate faces, bone cycles, texture alignment, and out-of-range field values. Auto-repair clamps invalid indices and breaks bone hierarchy cycles. All warnings are collected and displayed in a modal dialog after import.

---

## Usage

Tools are accessed through two locations in Blender:

- **File > Import > Carnivores Engine (.3df, .car)** -- Import submenu
- **File > Export > Carnivores Engine (.3df, .car, .3dn)** -- Export submenu
- **Sidebar** (press `N` in the 3D Viewport) > **Carnivores** tab -- Panels for Animation, Face Flags, and Selection Tools

---

### Importing a Static Model (.3df)

1. Open **File > Import > Carnivores Engine (.3df, .car) > Static Model (.3df)**.
2. Select one or more `.3df` files and configure:
   - **Scale** -- import scale factor (default 0.01).
   - **Flip Handedness** -- negate X-axis to match the engine's left-handed system (enabled by default).
   - **Import Textures / Create Materials** -- loads the embedded ARGB1555 texture as a Blender image and assigns a material.
   - **Bone Import Type** -- None, Armature, or Hooks. Hooks (default) create vertex groups with Hook modifiers for each bone. Armature builds an Armature object with proper bone positions and parent-child relationships from the file's bone data.
   - **Smooth Weights** -- apply Laplacian smoothing to vertex weights (configurable iterations, factor, and joints-only mode).
3. Click **Import**.

### Importing an Animated Model (.car)

1. Open **File > Import > Carnivores Engine (.3df, .car) > Animated Model (.car)**.
2. Select one or more `.car` files and configure:
   - **Import Animations** -- converts vertex animations to Shape Key Actions with NLA strips (enabled by default).
   - **Absolute Shape Keys** -- use Absolute (Evaluation Time) shape keys instead of the default Relative ones.
   - **Respect KPS Timing** -- align keyframes to sub-frame positions per the file's KPS; disable to snap to integer frames.
   - **Import Sounds** -- load embedded WAV data and link sounds to the corresponding Actions.
   - **Smooth Weights** -- apply weight smoothing after vertex group creation.
3. Click **Import**. Each model receives vertex groups (with synthetic names like `CarBone_0` mapping the file's raw ownership indices) and, if enabled, shape keys and NLA tracks.

### Reconstructing a Skeleton (.car models)

*.car files contain vertex ownership data (integer group indices) but no bone positions or hierarchy. Reconstruction builds an armature from the imported vertex groups:*

1. Select the imported mesh.
2. In the **Carnivores** sidebar tab, find the **Rigging Utilities** box in the **Carnivores Animation** panel.
3. Click **Reconstruct Rig from Owners**.
4. The addon computes the weighted centroid of each vertex group, infers a bone hierarchy using a symmetry-aware MST algorithm, and builds an armature. The mesh is parented with an Armature modifier automatically.

To inspect the result, click **Log Rig Debug Info** to write bone positions, parenting, and vertex group statistics to a text datablock.

### Working with KPS (Keys Per Second)

1. Select the mesh and open the **Carnivores** sidebar tab.
2. In the **Carnivores Animation** panel, the NLA track list shows all imported animations.
3. Select a track. The detail box shows:
   - **Sound** -- the linked sound datablock (file picker to assign).
   - **KPS Mode dropdown** -- switch between "Auto (Scene FPS)" and "Override".
   - **KPS value** -- when in Override mode, enter a custom value.
4. After changing shape keys or KPS, click **Re-Sync Timing** to fully re-bake keyframes and update NLA strip ranges.

### Adding Sound to an Animation

1. Select a track in the **Carnivores Animation** panel.
2. Click the folder icon next to the Sound field and choose a `.wav` file.
3. The sound is linked to the Action. During `.car` export, it will be embedded in the file.
4. To audition: click **Play Linked Sound** to add it to Blender's sequencer, or use **Play Preview** to loop the animation with audio in the viewport.
5. Toggle **Enable NLA Sound** to activate/deactivate automatic playback when scrubbing the timeline.

### Managing Face Flags

1. Select the mesh and open the **Carnivores** sidebar tab.
2. If the mesh has no `3df_flags` attribute, click **Create '3df_flags'** in the **3DF Face Flags** panel.
3. **Visualize flags:** Click **Visualize Flags (Colors)** to render each flag type as a distinct color on the mesh's `FlagColors` color attribute.
4. **Modify flags:** Enter Edit Mode, select faces, then use the Set / Clear / Toggle buttons next to each flag in the panel. The panel displays live counts of how many faces carry each flag (in Edit Mode, counts reflect only selected faces; in Object Mode, all faces).
5. **Select by flags:** In the **Selection Tools** panel, check the flags to match, choose a mode (**Has Any** = OR, **Has All** = AND, **Has None** = NOT), an action (Select, Deselect, Invert), and click **Apply**.
6. **Clear All Flags** resets every flag bit on selected or all faces.

### Exporting

1. **Prepare the scene:** ensure meshes are triangulated, textures are 256px wide, and `3df_flags` attributes are assigned where needed.
2. Open **File > Export > Carnivores Engine (.3df, .car, .3dn)** and choose:
   - **Static Model (.3df)** -- supports single-file or multi-export of all selected mesh objects. Each file is named after the object, optionally with a base name prefix.
   - **Animated Model (.car)** -- exports the active mesh with its shape keys, armature, linked sounds, and KPS metadata. The model name field accepts a 32-character string; suffix with `msc: #` for engine-specific behavior.
   - **Static Model Hunter (.3dn)** -- for mobile/HD titles. Supports an optional sprite name.
   - **Animation (.vtl)** -- exports vertex animation data as a standalone file.
3. Configure **Scale** (default 100.0 to compensate for the 0.01 import default), **Flip Handedness**, and optional UV flipping.
4. Click **Export**.

---

## Debugging

1. Go to **Preferences > Extensions > CarnivoresIO** and enable **Debug Mode**.
2. Open the System Console (`Window > Toggle System Console` on Windows).
3. Import/export operations will print detailed parsing steps, NumPy timing data, and validation warnings.

---

## License

Licensed under **GNU GPL v3**. Copyright (c) 2024-2026 Tibor Harsányi.
