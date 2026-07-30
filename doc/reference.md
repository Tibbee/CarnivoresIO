# CarnivoresIO Reference Specifications

Canonical reference for constants, bitfields, engine limits, and validation rules used across the CarnivoresIO add-on. All other documentation links to this file for repeated definitions.

## Core Constants

Defined in `core/constants.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `TEXTURE_WIDTH` | 256 | All Carnivores textures are fixed 256 pixels wide |
| `FACE_FLAG_OPTIONS` | Bitfield map | Maps flag names to bitmasks (see Face Flags below) |

## Structural Validation and Compatibility Limits

`parsers/validate.py` always performs non-destructive structural checks. Optional compatibility diagnostics report target-specific limits without preventing the addon from importing otherwise valid data.

| Target or rule | Value | Behavior |
|-------|-------|----------|
| Structural mesh limit | No fixed vertex/face count | Counts must fit the declared file sections and available memory |
| Legacy AltEdit | 1024 vertices/faces | Compatibility warning; not an addon or current-engine limit |
| Current C2 MEE standalone objects | 1024 records | Compatibility warning because the loader uses `gObj[1024]` |
| Current C2 MEE CAR animations/sounds | 64 each | Compatibility warning because `TCharacterInfo` uses fixed arrays |
| Texture rows | 256 × 2 bytes | Structural requirement for complete ARGB1555 rows |
| Current C2 MEE OpenGL texture buffer | 256 × 256 × 2 bytes | Any other declared size warns about incomplete initialization or overflow; the software loader supports variable-height rows |
| Bone name length | 32 bytes | Compatibility warning/cleanup during Blender object creation |
| Animation sound mapping | 64 entries | Fixed cross-reference table in the current format and engine |

Current-engine compatibility was source-verified against `Carnivores2MEE1.11` (`Hunt/Core/ModelTypes.h`, `Hunt/Core/GameTypes.h`, `Hunt/Core/GameState.h`, and `Hunt/Loaders/ModelLoader.cpp`).

## Face Flags (16-bit Bitfield)

Canonical definition for `.3df`, `.car`, and `.3dn` face `flags` field:

| Bit | Mask | Name | Description |
|-----|------|------|-------------|
| 0 | 0x0001 | `sfDoubleSide` | Face textured on both sides |
| 1 | 0x0002 | `sfDarkBack` | Dark back side |
| 2 | 0x0004 | `sfOpacity` | Transparent (alpha-blended) |
| 3 | 0x0008 | `sfTransparent` | Non-solid (bullets pass through) |
| 4 | 0x0010 | `sfMortal` | Marks target/hit zone |
| 5 | 0x0020 | `sfPhong` | Phong-shaded |
| 6 | 0x0040 | `sfEnvMap` | Environment-mapped |
| 7 | 0x0080 | `sfNeedVC` | Purpose unknown (legacy) |
| 8–14 | — | Unused | Reserved, always 0 |
| 15 | 0x8000 | `sfDark` | Dark front side |

Face flags are stored as a face-domain `INT` attribute named `3df_flags` on Blender meshes. Use helpers in `utils/flags.py` to read/write them.

## Coordinate System Summary

- **Carnivores model files**: `+Y` is up and `+Z` is model-forward.
- **Carnivores 2 runtime**: loading reflects file Z (`x, y, z` → `2x, 2y, -2z`).
- **Blender/add-on convention**: `+Z` is up and `+Y` is model-forward.
- **Default file-to-Blender mapping**: `(x, y, z)` → `(x, z, y)`, followed by the import scale.

Because the default mapping has determinant `-1`, face and UV corner order is reversed with it. Full matrix composition, engine-source evidence, winding handling, and reciprocal export logic are documented in [Formats: Coordinate & Axis Conversion](formats.md#coordinate--axis-conversion).

## Validation Warnings vs. Errors

Collected in `ParserContext.warnings` during parsing:

- **Structural errors** raise `ValueError`: truncated sections, negative counts, invalid face/parent indices, non-finite coordinates, cyclic hierarchies, and incomplete texture rows. Structural validation always runs.
- **Compatibility warnings** are optional: legacy AltEdit mesh counts, fixed current-engine arrays, unknown preserved flags, unusual UVs, and naming issues.
- Validation does not clamp indices, rewrite owners, break hierarchy cycles, or clip UVs. Repairs must be explicit operations rather than side effects of import validation.
