# CarnivoresIO Blender Extension

**CarnivoresIO** is a high-performance Blender extension for importing and exporting Carnivores `.3df` (static) and `.car` (animated) model files. Optimized for **Blender 4.2+**, it provides a professional toolset for handling the custom legacy formats of the Carnivores game series.

## Key Features

- **Blender 4.2 Extension Architecture**: Fully compatible with the new Blender Extensions platform, supporting online updates and standardized metadata.
- **Robust Import/Export**:
  - **.3df (Static)**: Geometry, UVs, and face flags.
  - **.car (Animated)**: High-speed animation baking, shape key management, and sound embedding.
  - **.3dn (Mobile)**: Optimized static model export for *Carnivores: Dinosaur Hunter* (Mobile/HD).
- **Advanced Rigging & Animation**:
  - **Skeleton Reconstruction**: Automatically infer and build a functional Armature from .car vertex groups using a proximity-based Minimum Spanning Tree (MST) algorithm.
  - **Weight Smoothing**: Optional topology-based Laplacian smoothing on import to fix jagged vertex transitions.
- **NLA Sound System**: 
  - Real-time sound playback synchronized with NLA strips.
  - Link `.wav` files directly to Actions via the Sidebar panel.
  - "Self-healing" audio device management to prevent playback crashes.
- **Face Flag Tools**:
  - **Visualizer**: Automatically generate vertex colors (`FlagColors`) to visualize 3DF face flags (e.g., Transparent, Double-Sided) directly in the viewport.
  - **Selection Tools**: Select, deselect, or invert faces based on specific bitmask combinations.
- **Developer-Ready Logging**: New internal logging system with a **Debug Mode** toggle in Addon Preferences for verbose console output.

## Installation (Blender 4.2+)

### Standard Installation
1. Download the `carnivores_io-2.x.x.zip` from the [Releases](https://github.com/Tibbee/CarnivoresIO/releases) page.
2. In Blender, go to `Edit > Preferences > Get Extensions`.
3. Drag and drop the `.zip` file into the Blender window, or use the menu to install from disk.

### Dev Channel (Auto-Updates)
If you want to stay on the "bleeding edge" with automatic updates:
1. Go to `Preferences > File Paths > Extensions`.
2. Click **+** to add a new Remote Repository.
3. Paste the URL to the `index.json` hosted on this repository:
   `https://raw.githubusercontent.com/Tibbee/CarnivoresIO/refs/heads/main/public_repo/index.json`
4. You can now update the extension directly from the Extensions tab whenever a new dev build is pushed.

## Usage

### 1. Rigging & Animation
*Located in the Sidebar (N) > Carnivores > Animation Panel.*
- **Reconstruct Rig**: Click "Reconstruct Rig from Owners" to instantly turn a vertex-animated .car model into a poseable skeleton.
- **KPS Control**: Set the Keys Per Second for each animation. Use **Auto** for Scene FPS or **Override** for custom game speeds.
- **Linked Sounds**: Use the folder icon to associate a sound with an animation. Toggle the speaker icon to enable real-time playback while scrubbing or playing.
- **Re-Sync**: Recalculate animation timing instantly after changing FPS or KPS.

### 2. Face Flags & Visualization
*Located in the Sidebar (N) > Carnivores > 3DF Face Flags.*
- **Visualize Flags**: Generates a vertex color layer that tints faces based on their 3DF flags (e.g., Magenta for Double Sided, Blue for Opacity).
- **Modify Flags**: Set, Clear, or Toggle specific bits on selected faces.

### 3. Debugging
- If you encounter issues, go to `Preferences > Extensions > CarnivoresIO` and enable **Debug Mode**.
- Open the System Console (`Window > Toggle System Console` on Windows) to view detailed logs.

## Compatibility
- **Min Version**: Blender 4.2.0
- **Max Version**: Blender 5.0.0 (Exclusive)
- **Dependencies**: Uses `numpy` and `aud` (standard in Blender distributions).

## License
Licensed under **GNU GPL v3**.  
Copyright © 2024-2025 Tibor Harsányi.
