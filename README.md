# CarnivoresIO Blender Add-on

**CarnivoresIO** is a Blender add-on for importing and exporting Carnivores `.3df` (static) and `.car` (animated) model files, designed to work with Blender 4.0+. It provides tools for handling the custom file formats used in the Carnivores game series, including support for mesh geometry, face flags, textures, bones, UV maps, animations, and embedded sounds.

## Features

- **Import/Export `.3df` Files**:
  - **Geometry**: Vertices, faces, UVs, and face flags.
  - **Textures**: 16-bit ARGB1555 format (imported as PNGs).
  - **Bones**: Import as Hooks or Armatures.
  - **Validation**: Checks for geometry errors (e.g., invalid indices).

- **Import/Export `.car` Files**:
  - **Animations**: Imports animations as Shape Keys with automatic NLA strip creation.
  - **Sounds**: Imports embedded audio and links it to specific animations.
  - **Smooth Playback**: Custom interpolation logic to ensure game-accurate, jitter-free motion.

- **Animation Tools**:
  - **Re-Sync Timing**: One-click operator to recalculate animation timing based on Scene FPS and KPS (Keys Per Second).
  - **KPS Control**: Set playback speed per animation using **Auto** (Scene FPS) or **Override** (Custom KPS) modes.
  - **NLA Sound Playback**: Real-time playback of linked sounds when scrubbing the timeline or playing animations.

- **Face Flag Management**:
  - UI panel to set, clear, or toggle `.3df` face flags (e.g., Double Side, Transparent).
  - Selection tools to find faces based on specific flag masks.

## Installation

1. **Download the Add-on**:
   - Clone or download this repository as a ZIP file.
   - Ensure all files are in a single directory (e.g., `CarnivoresIO/`).

2. **Install in Blender**:
   - Open Blender (version 4.0 or higher).
   - Go to `Edit > Preferences > Add-ons > Install...`.
   - Select the `__init__.py` file or the zipped folder.
   - Enable the add-on: `Import-Export: CarnivoresIO`.

3. **Verify Installation**:
   - Check `File > Import` for `.3df` and `.car` options.
   - Look for the `Carnivores` tab in the Sidebar (`N` key).

## Usage

### Importing/Exporting Models
- **.3df (Static)**: Use `File > Import/Export > Carnivores 3DF (.3df)`.
  - Options: Scale, Texture Import, Axis Conversion.
- **.car (Animated)**: Use `File > Import/Export > Carnivores CAR (.car)`.
  - **Import Options**: 
    - `Import Animations`: Converts game frames to Shape Keys.
    - `Import Sounds`: Extracts embedded sounds to Blender Sound datablocks.

### Animation & Sound System
*Manage animations in the `Carnivores > Carnivores Animation` panel.*

![Animation Panel Overview](PLACEHOLDER: Screenshot of the Carnivores Animation Panel showing NLA tracks and KPS controls)

#### 1. Controlling Speed (KPS)
Carnivores animations run at a specific **KPS (Keys Per Second)**.
- **Auto Mode**: The animation plays at the Scene FPS (1 Blender Frame = 1 Game Frame).
- **Override Mode**: Set a custom KPS (e.g., 15). The add-on will space keyframes to match this speed regardless of your Scene FPS.

#### 2. Re-Syncing Timing
If you change your Scene FPS or the KPS of an animation, the playback might become too fast or too slow.
1. Select the animation in the panel.
2. Click **Re-Sync Timing**.
3. The add-on recalculates keyframes and updates the NLA strip length instantly.

#### 3. Sound Integration
- **Enable NLA Sound**: Toggle the speaker icon in the panel to hear footstep/sfx sounds while playing the animation in the viewport.
- **Linking Sounds**:
  - Select an animation track.
  - Use the **Folder Icon** to import a `.wav` file.
  - The sound is now linked to that animation and will export with the `.car` file.

### Face Flags & Selection
*Manage flags in the `Carnivores > 3DF Face Flags` panel.*

![Face Flags Panel](PLACEHOLDER: Screenshot of the 3DF Face Flags panel)

1. **Set/Clear Flags**: Click the checkboxes or X buttons to modify flags on selected faces.
2. **Select by Flags**: Use the **Selection Tools** panel to find faces with specific properties (e.g., "Select all Transparent faces").
   - **Modes**: `Has Any`, `Has All`, `Has None`.

## File Format Details

- **.3df**: Static geometry. Texture size must be 256px wide.
- **.car**: Animated geometry. Uses a "Stop Motion" style vertex compression. The add-on converts this to linear interpolation for smooth editing in Blender.

## Dependencies
- **Blender 4.0+**
- **NumPy** (Included with Blender)

## License
Licensed under GNU GPL v3 (see `LICENSE` file).

## Credits
- **Author**: Tibor Harsányi (Strider)
- **Support**: Open an issue on GitHub or contact via the Carnivores Saga Discord.