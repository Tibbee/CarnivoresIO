# Carnivores Engine Formats

This section covers the low-level binary structures and mathematical conversions required to interface with the Carnivores game engine.

### Data Specifications
Detailed breakdowns of the binary headers, face structures, and vertex data for the various engine formats.
- **[.3DF Specifications](formats/specifications.md)**: Static model format used for map objects and decorations.
- **[.CAR Specifications](formats/specifications.md#car-file-format-specification)**: Animated model format supporting vertex-based animations and embedded audio.
- **[Map Formats](formats/map_specifications.md)**: Technical details on the landscape and map layout files.

### Technical Mathematics
- **[Axis & Handedness Conversion](formats/axis_conversion.md)**: Explains the transition between Carnivores' left-handed coordinate system and Blender's right-handed system, including the X-flip and winding order logic.
