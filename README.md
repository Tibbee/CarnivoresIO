# CarnivoresIO Blender Add-on

**CarnivoresIO** is a Blender add-on for importing and exporting Carnivores `.3df` model files, designed to work with Blender 4.0+. It provides tools for handling the custom `.3df` file format used in the Carnivores game series, including support for mesh geometry, face flags, textures, bones, and UV maps. The add-on integrates into Blender’s UI with dedicated panels for managing face flags and selection tools.

## Features

- **Import `.3df` Files**:
  - Supports importing mesh geometry, vertices, faces, UVs, bones, and textures (16-bit ARGB1555 format).
  - Configurable import options: scale, texture import, material creation, face smoothing, and bone import (as hooks or armatures).
  - Optional validation to detect and report issues in `.3df` files (e.g., invalid vertex indices, bone cycles).
  - Axis conversion for compatibility with different coordinate systems.

- **Export `.3df` Files**:
  - Exports selected mesh objects to `.3df` format, with options for single or multiple file exports.
  - Supports texture export in ARGB1555 format, with UV flipping options.
  - Handles bone data from armatures or hook modifiers, with automatic vertex group assignment.
  - Axis conversion and scaling for export.

- **Face Flag Management**:
  - UI panel (`Carnivores > 3DF Face Flags`) to set, clear, or toggle `.3df` face flags (e.g., Double Side, Transparent, Phong).
  - Supports editing flags for selected faces in Edit Mode or all faces in Object Mode.
  - Creates a `3df_flags` face-domain integer attribute if missing.

- **Selection Tools**:
  - UI panel (`Carnivores > Selection Tools`) for selecting faces based on `.3df` flags.
  - Selection modes: `ANY` (OR), `ALL` (AND), `NONE` (NOT).
  - Actions: Select, Deselect, or Invert matched faces.

- **Validation and Error Handling**:
  - Comprehensive validation during import to ensure file integrity (e.g., checks for valid vertex/face counts, texture sizes, bone hierarchies).
  - Reports warnings for issues like high vertex counts or non-zero reserved fields.
  - Displays import warnings in a modal dialog.

- **Performance**:
  - Uses NumPy for efficient data processing.
  - Includes timing decorators to log operation performance.

## Installation

1. **Download the Add-on**:
   - Clone or download this repository as a ZIP file.
   - Ensure all files are in a single directory (e.g., `CarnivoresIO/`).

2. **Install in Blender**:
   - Open Blender (version 4.0 or higher).
   - Go to `Edit > Preferences > Add-ons > Install...`.
   - Select the `__init__.py` file or the zipped folder containing the add-on.
   - Enable the add-on by checking the box next to `Import-Export: CarnivoresIO`.

3. **Verify Installation**:
   - Check the `File > Import` and `File > Export` menus for `Carnivores 3DF (.3df)` options.
   - In the 3D Viewport, look for the `Carnivores` tab in the Sidebar (`N` key).

## Usage

### Importing a `.3df` File
1. Go to `File > Import > Carnivores 3DF (.3df)`.
2. Select one or more `.3df` files.
3. Adjust import options in the operator panel:
   - **Scale**: Adjusts model size (default: 0.01).
   - **Import Textures**: Imports ARGB1555 textures as images.
   - **Create Materials**: Generates materials for the mesh and world.
   - **Smooth Faces**: Enables face smoothing (default: True).
   - **Bone Import Type**: Choose `None`, `Armature`, or `Hooks` (default: Hooks).
   - **Run Validations**: Enables file validation (default: False).
   - **Axis Conversion**: Set forward and up axes for coordinate system alignment.
4. Click `Import` to load the model into a new collection.

### Exporting a `.3df` File
1. Select one or more mesh objects in Blender.
2. Go to `File > Export > Carnivores 3DF (.3df)`.
3. Choose a file path and adjust export options:
   - **Export Multiple Files**: Exports each selected mesh to a separate file (uses object names).
   - **Scale**: Scales vertex coordinates (default: 100).
   - **Export Textures**: Exports textures if a valid image is found (width must be 256 pixels).
   - **Flip U/V**: Flips UV coordinates for compatibility with certain tools.
   - **Axis Conversion**: Set forward and up axes.
4. Click `Export` to save the `.3df` file(s).

### Managing Face Flags
1. Select a mesh object in Object or Edit Mode.
2. In the 3D Viewport Sidebar (`N` key), go to the `Carnivores` tab > `3DF Face Flags` panel.
3. If no `3df_flags` attribute exists, click `Create '3df_flags'` to add it.
4. Use the buttons next to each flag (e.g., Double Side, Transparent) to:
   - **Checkmark**: Set the flag.
   - **X**: Clear the flag.
   - **Arrow**: Toggle the flag.
5. In Edit Mode, flags apply only to selected faces; in Object Mode, they apply to all faces.
6. Use `Clear All Flags` to reset all flags to 0.

### Selecting Faces by Flags
1. In the `Carnivores` tab, go to the `Selection Tools` panel.
2. Expand the `Flag Selection` section and toggle desired flags (e.g., `Double Side`, `Transparent`).
3. Choose a **Mode**:
   - `Has Any (OR)`: Select faces with at least one chosen flag.
   - `Has All (AND)`: Select faces with all chosen flags.
   - `Has None (NOT)`: Select faces with none of the chosen flags.
4. Choose an **Action**: `Select`, `Deselect`, or `Invert`.
5. Click `Apply` to update the face selection.

## File Format Support

The add-on supports the `.3df` file format as specified in the provided documentation. Key details:
- **Header**: 16 bytes (vertex, face, bone counts, texture size).
- **Faces**: 64 bytes each, including vertex indices, UVs, flags, and reserved fields.
- **Vertices**: 16 bytes each, with coordinates, bone owner, and hide flag.
- **Bones**: 48 bytes each, with name, position, parent index, and hide flag.
- **Texture**: ARGB1555 format, 256-pixel width, variable height.

For a detailed specification, refer to the `.3df File Format Specification` in the code comments or documentation.

## Dependencies

- **Blender**: Version 4.0 or higher (not yet tested or developed with older blender versions in mind).
- **Python Libraries**: `numpy` (included with Blender’s Python).
- **Blender Modules**: `bpy`, `bpy_extras.io_utils`, `mathutils`, `bmesh`.

## Project Structure

The add-on is organized into several Python modules:
- `__init__.py`: Registers the add-on, defines UI properties, and integrates import/export menu options.
- `operators.py`: Import/export operators and utilities for creating `3df_flags` and modal messages, 
UI panels and operators for face flag management and face selection, toggling flags and clearing selections.
- `utils.py`: Core utility functions for mesh creation, bone handling, UVs, and materials, world shaders, face selection, and texture conversion.
- `parse_3df.py`: Parsing logic for `.3df` files with validation.
- `export_3df.py`: Export logic for `.3df` files.
- `core/constants.py`: Constants like `FACE_FLAG_OPTIONS` and `TEXTURE_WIDTH`.
- `core/core.py`: NumPy dtype definitions for `.3df` file structure.

## Known Limitations

- **Texture Width**: Textures must be exactly 256 pixels wide.
- **Vertex/Face Limits**: If you choose validation it enforces a maximum of 2048 vertices/faces, with warnings above 1024 due to compatibility issues with some tools (e.g., AltEdit).
- **Bone Names**: Non-ASCII or overly long bone names are cleaned or truncated during import.
- **Edit Mode**: Some operations (e.g., flag modification) temporarily switch to Object Mode to ensure data consistency.
- **Alpha Bits**: Texture alpha is not fully supported in import/export due to limited in-game use.

## License
Licensed under GNU GPL v3 (see the LICENSE file). See the `LICENSE` file for details.

## Credits

- **Author**: Tibor Harsányi (Strider)
- **Support**: Community-driven, report issues or suggestions on the repository.

## Contact

For support or inquiries, please open an issue on the project’s GitHub repository or contact the author via the Carnivores Saga discord group.
