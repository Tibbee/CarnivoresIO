# CarnivoresIO Reference Specifications

Canonical reference for constants, bitfields, engine limits, and validation rules used across the CarnivoresIO add-on. All other documentation links to this file for repeated definitions.

## Core Constants

Defined in `core/constants.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| `TEXTURE_WIDTH` | 256 | All Carnivores textures are fixed 256 pixels wide |
| `FACE_FLAG_OPTIONS` | Bitfield map | Maps flag names to bitmasks (see Face Flags below) |

## Engine Limits & Validation Rules

Enforced by `parsers/validate.py`:

| Limit | Value | Behavior |
|-------|-------|----------|
| Max vertices (warning) | 1024 | Triggers AltEdit compatibility warning |
| Max vertices/faces (hard error) | 2048 | Fatal error on parse/export |
| Texture size | 256 × 2 × height bytes | Must be multiple of 512 (ARGB1555 format) |
| Bone name length | 32 bytes | ASCII-only, truncated on import |
| Animation sound mapping | 64 entries | 1 sound per animation, fixed order in cross-reference table |

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

- **Carnivores**: Left-handed (X: left, Y: up, -Z: forward)
- **Blender**: Right-handed (X: left, Y: forward, Z: up)

Full conversion logic (X-flip, winding order fixes) is documented in [Formats: Coordinate & Axis Conversion](formats.md#coordinate--axis-conversion).

## Validation Warnings vs. Errors

Collected in `ParserContext.warnings` (list of strings) during parse/export:
- **Warnings**: Non-fatal issues (vertex count >1024, unknown header fields, bone name cleanup)
- **Errors**: Fatal issues (vertex count >2048, cyclic bone hierarchies, invalid texture dimensions) raise `ValueError`
