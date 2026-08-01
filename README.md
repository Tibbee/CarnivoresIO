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

All binary I/O uses NumPy for performance. Textures use the ARGB1555 format at a fixed 256-pixel width. Configurable axis conversion and the **Use Carnivores Coordinate Conversion** option convert Carnivores model-file space (`+Y` up, `+Z` forward) to Blender space (`+Z` up, `+Y` forward). With the defaults, `(x, y, z)` maps to `(x, z, y)` and triangle/UV corner order is reversed to preserve face orientation.

### Skeleton Reconstruction

Legacy and experimental topology-first algorithms infer a bone hierarchy from imported CAR owner data or vertex groups. The reconstruction workflow calculates owner-region centroids, uses symmetry-aware hierarchy scoring, and supports Automatic or searchable owner-root selection with a compatible integer override for scripts. Names such as `floor`, `root`, `pelvis`, and `spine` are considered during automatic root scoring, but explicit overrides always take precedence. The resulting armature is parented to the mesh with an Armature modifier in a single step.

### Animation Pipeline

- Vertex animations from `.car` files are converted to Shape Key Actions with NLA strips during import.
- Absolute or Relative shape key modes are supported.
- Each Action carries a **KPS (Keys Per Second)** mode: auto-detect from scene FPS or override with a custom value. KPS-aligned keyframes land on sub-frame positions for exact engine timing.
- **Re-Sync Timing** fully re-bakes all keyframes from shape key data using the current KPS, then updates NLA strip ranges.
- `.vtl` export for standalone animation files.

### NLA Sound System

- Each Action can carry a linked sound for synchronized audio preview and CAR export.
- Sounds can be imported, replaced, inspected as Packed/External/Unavailable, or cleared from an Action.
- Managed audio plays only for an explicitly focused animation: **Play Preview** in the extension UI or Blender's NLA Tweak Mode.
- The authoritative **Preview Audio** On/Off control stops managed playback immediately when disabled.
- Normal unfocused NLA playback does not trigger linked sounds, preventing ambiguous track selection and offset cascades.
- **Play Preview** loops a single Action with its audio for iterative timing work and restores scene/NLA state when stopped.
- The animation panel exposes CAR export order controls, selected-track timing/KPS summaries, duration differences, and multi-strip/empty-track information.
- Self-healing audio device management recovers from driver failures.
- Embedded sounds from `.car` files are imported and auto-associated with the correct animations via cross-reference tables.

### Face Flags

- Engine face property bits (Double Side, Phong, Transparent, Mortal, etc.) are stored as the integer face-domain attribute `3df_flags` on the mesh.
- Flag visualization renders flags as the viewport-only `FlagColors` color attribute (not engine flag data), with Show/Refresh/Hide/Remove controls and an optional named-color legend collapsed by default.
- Bulk modification: set, clear, or toggle individual flags across selectable faces in Object or Edit Mode without leaving Edit Mode.
- Smart selection: find faces by Any, All, or None flag pattern using bitmask logic, then select, deselect, or invert.
- Clear Selected Faces and confirmed Clear All Faces operations make destructive scope explicit.

### Multi-Export

Export all selected mesh objects to separate `.3df` files in one operation.

### Validation and Reports

Non-destructive structural validation always checks section sizes, signed counts, indices, finite coordinates, hierarchy cycles, and texture-row alignment. The **Carnivores Model** sidebar panel adds format-aware preflight checks for Blender meshes, textures, face flags, animation timing, sounds, rig owners, names, modifiers, and C2 MEE compatibility. The **Validate Model** operator and import/export operations preserve structured diagnostics in Blender Text datablocks and provide **Open Report** and **Copy Report** actions where the UI context supports them. Export dialogs run the same checks by default and block only errors; warnings remain reviewable in the export report. Validation preserves source owners, UVs, flags, and hierarchy data rather than silently repairing them.

---

## Usage

Tools are accessed through two locations in Blender:

- **File > Import > Carnivores Engine (.3df, .car)** -- Import submenu
- **File > Export > Carnivores Engine (.3df, .car, .3dn)** -- Export submenu
- **Sidebar** (press `N` in the 3D Viewport) > **Carnivores** tab -- Panels for Model Health, Rig, Animation, Face Flags, and Selection Tools

---

### Importing a Static Model (.3df)

1. Open **File > Import > Carnivores Engine (.3df, .car) > Static Model (.3df)**.
2. Select one or more `.3df` files and configure:
   - **Import Scale** -- import scale factor (default 0.01). Use **Export Scale** 100.0 for the standard round trip.
   - **Use Carnivores Coordinate Conversion** -- enable the default file-to-Blender conversion and its matching face/UV corner reversal (enabled by default). The complete mapping is `(x, y, z)` → `(x, z, y)`, not a visible X negation.
   - **Import Textures / Create Materials** -- loads the embedded ARGB1555 texture as a Blender image and assigns a material when material creation is enabled.
   - **Bone Import Type** -- None, Armature, or Hooks. Hooks (default) create vertex groups with Hook modifiers for each bone. Armature builds an Armature object with proper bone positions and parent-child relationships from the file's bone data.
   - **Smooth Weights** -- apply Laplacian smoothing to vertex weights (configurable iterations, factor, and joints-only mode).
3. Click **Import**. Imported primary meshes are selected automatically and the last imported mesh becomes active. The operation summary reports created objects, collections, animation/sound data, and warnings. Optional viewport framing is available in the **After Import** section and in the add-on preferences; framing is skipped safely when no compatible 3D View is available.

### Importing an Animated Model (.car)

1. Open **File > Import > Carnivores Engine (.3df, .car) > Animated Model (.car)**.
2. Select one or more `.car` files and configure:
   - **Import Animations** -- converts vertex animations to Shape Key Actions with NLA strips (enabled by default).
   - **Absolute Shape Keys** -- use Absolute (Evaluation Time) shape keys instead of the default Relative ones.
   - **Respect KPS Timing** -- align keyframes to sub-frame positions per the file's KPS; disable to snap to integer frames.
   - **Import Sounds** -- load embedded WAV data and link sounds to the corresponding Actions. If **Import Animations** is disabled, sounds are imported as unlinked sound datablocks.
   - **Smooth Weights** -- apply weight smoothing after vertex group creation.
3. Click **Import**. Imported primary meshes are selected automatically and the last imported mesh becomes active. The operation summary reports created objects, collections, animations, sounds, and warnings. Optional viewport framing is available in the **After Import** section and in the add-on preferences. Each model receives vertex groups (with synthetic names like `CarBone_0` mapping the file's raw ownership indices) and, if enabled, shape keys and NLA tracks.

### Reconstructing a Skeleton (.car models)

`.car` files contain vertex ownership data (integer group indices) but no bone positions or hierarchy. Reconstruction builds an armature from the imported vertex groups:

1. Select the imported mesh.
2. In the **Carnivores** sidebar tab, open the dedicated **Carnivores Rig** panel.
3. Choose the reconstruction algorithm and optional root/weight settings, then click **Reconstruct Rig**.
4. The addon computes centroids from the imported owner cache (falling back to vertex groups when needed), infers a bone hierarchy using a scored symmetry-aware MST algorithm, and builds an armature. Optional pre-reconstruct smoothing can be enabled in the **Carnivores Rig** panel. The mesh is parented with an Armature modifier automatically.

To inspect the result, click **Generate Rig Report**, then **Open Report** to view bone positions, parenting, owner-cache, topology, root, and vertex-group statistics. **Reset to Imported Owners** explicitly rebuilds canonical `CarBone_<index>` groups from the cached owner data.

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
2. Click **Import** next to the Sound field and choose a `.wav` file.
3. The sound is linked to the Action. During `.car` export, it will be embedded in the file.
4. To audition, use **Play Preview** to isolate and loop the animation with its audio, or enter NLA Tweak Mode to focus the strip.
5. Toggle **Preview Audio** On/Off to enable or disable focused preview audio; disabling it stops managed playback immediately.

Linked audio retains its authored timing. KPS changes, NLA strip scaling, and reverse playback do not time-stretch audio. As a best-effort safeguard, focused playback restarts the original clip at NLA repeat boundaries, but scaled strips are not guaranteed to remain synchronized. Prepare the clip for the animation's intended duration in an external audio editor. Planned export and timing-assistance tools are described in [Improvements — Audio Workflow](doc/IMPROVEMENTS.md#19-deferred-workflow-enhancements).

### Managing Face Flags

1. Select the mesh and open the **Carnivores** sidebar tab.
2. If the mesh has no `3df_flags` attribute, click **Create '3df_flags'** in the **3DF Face Flags** panel.
3. **Visualize flags:** In the Visualization box, use **Show**, **Refresh**, **Hide**, and **Remove Colors**. Show configures the invoking Solid viewport to use the generated `FlagColors` attribute; Hide restores the prior color display. Enable **Show Color Legend** when you need the named color mapping; it is collapsed by default.
4. **Modify flags:** Enter Edit Mode, select faces, then use the Set / Clear / Toggle buttons next to each flag in the panel. The panel displays None/Mixed/All states and live counts (in Edit Mode, counts reflect only selected faces; in Object Mode, all faces).
5. **Select by flags:** In the **Selection Tools** panel, check the flags to match, choose a mode (**Has Any** = OR, **Has All** = AND, **Has None** = NOT), an action (Select, Deselect, Invert), review the match preview, and click **Apply**.
6. Use **Clear Selected Faces** for a scoped edit or **Clear All Faces** for the confirmed whole-mesh operation.

The flag tooltips and runtime meanings are documented in [Face Flags](doc/reference.md#face-flags-16-bit-bitfield), based on C2 MEE 1.11 renderer, loader, and hit-test behavior.

### Exporting

1. **Prepare the scene:** ensure meshes are triangulated, textures are 256px wide, and `3df_flags` attributes are assigned where needed.
2. Open **File > Export > Carnivores Engine (.3df, .car, .3dn)** and choose:
   - **Static Model (.3df)** -- supports single-file or multi-export of all selected mesh objects. Each file is named after the object, optionally with a base name prefix.
   - **Animated Model (.car)** -- exports the active mesh with its shape keys, armature, linked sounds, and KPS metadata. The model name field accepts a 32-character string; suffix with `msc: #` for engine-specific behavior.
   - **Dinosaur Hunter Mobile/HD (.3dn)** -- static models for mobile/HD Carnivores titles. Supports an optional sprite name.
   - **Animation (.vtl)** -- exports vertex animation data as a standalone file.
3. Configure **Export Scale** (default 100.0 to compensate for the 0.01 import default), **Use Carnivores Coordinate Conversion**, and optional UV flipping. Leave **Preflight Validation** enabled to run non-destructive target checks before writing the file.
4. Review any warnings in the operation report and click **Export**. Preflight errors prevent unsafe output until the model or settings are corrected.

---

## Preferences and debugging

Open **Preferences > Extensions > CarnivoresIO** to configure import selection/framing, default import/export scale, coordinate conversion, default .3DF bone import type, advanced dialog visibility, and Debug Mode. Documentation and issue-tracker links are available there, along with **Restore Defaults**. These preferences initialize newly opened dialogs; scripted operators retain their own explicitly supplied properties.

To debug, enable **Debug Mode** and open the System Console (`Window > Toggle System Console` on Windows). Import/export operations will print detailed parsing steps, NumPy timing data, and validation warnings.

---

## License

Licensed under **GNU GPL v3**. Copyright (c) 2024-2026 Tibor Harsányi.
