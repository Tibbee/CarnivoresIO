# Carnivores Engine Formats

Covers low-level binary structures, map/resource files, and mathematical conversions for the Carnivores game engine.

> **Note**: Canonical face flag definitions, engine limits, and core constants are maintained in [Reference](reference.md) to avoid duplication.

---

## Table of Contents
1. [Model Format Specifications](#model-format-specifications) (.3df, .car, .3dn)
2. [Map & Resource Files](#map--resource-files) (.map, .rsc)
3. [Coordinate & Axis Conversion](#coordinate--axis-conversion)

---

## Model Format Specifications

### .3DF File Format Specification

#### Header (16 bytes)

| Offset | Type   | Size | Name        | Description             |
| ------ | ------ | ---- | ----------- | ----------------------- |
| 0x0000 | uint32 | 4    | VCount      | Number of vertices      |
| 0x0004 | uint32 | 4    | FCount      | Number of faces         |
| 0x0008 | uint32 | 4    | BoneCount   | Number of bones         |
| 0x000C | uint32 | 4    | TextureSize | Texture length in bytes |

#### Face (64 bytes each)

| Offset | Type      | Size | Name    | Description                       |
| ------ | --------- | ---- | ------- | --------------------------------- |
| 0x00   | uint32    | 4    | v1      | Vertex 1 index                    |
| 0x04   | uint32    | 4    | v2      | Vertex 2 index                    |
| 0x08   | uint32    | 4    | v3      | Vertex 3 index                    |
| 0x0C   | uint32    | 4    | tax     | v1 texture U coordinate           |
| 0x10   | uint32    | 4    | tbx     | v2 texture U coordinate           |
| 0x14   | uint32    | 4    | tcx     | v3 texture U coordinate           |
| 0x18   | uint32    | 4    | tay     | v1 texture V coordinate           |
| 0x1C   | uint32    | 4    | tby     | v2 texture V coordinate           |
| 0x20   | uint32    | 4    | tcy     | v3 texture V coordinate           |
| 0x24   | uint16    | 2    | flags   | Bitfield (see **Face Flags**)     |
| 0x26   | uint16    | 2    | DMask   | Unused (possibly editor-specific) |
| 0x28   | uint32    | 4    | Distant | Purpose unknown                   |
| 0x2C   | uint32    | 4    | Next    | Index to other faces?             |
| 0x30   | uint32    | 4    | group   | Unused (possibly editor-specific) |
| 0x34   | byte\[12] | 12   | reserv  | Reserved (unused, always 0x00)    |

> **Face Flags**: See [Reference: Face Flags](reference.md#face-flags-16-bit-bitfield) for the canonical 16-bit bitfield definition.

#### Vertex (16 bytes each)

| Offset | Type   | Size | Name  | Description                            |
| ------ | ------ | ---- | ----- | -------------------------------------- |
| 0x00   | float  | 4    | X     | X coordinate                           |
| 0x04   | float  | 4    | Y     | Y coordinate                           |
| 0x08   | float  | 4    | Z     | Z coordinate                           |
| 0x0C   | uint16 | 2    | owner | Bone index attached to                 |
| 0x0E   | uint16 | 2    | hide  | Hidden in Designer 2 (ignored in-game) |

#### Bone (48 bytes each)

| Offset | Type      | Size | Name   | Description                            |
| ------ | --------- | ---- | ------ | -------------------------------------- |
| 0x00   | byte\[32] | 32   | name   | Bone name (ASCII string)               |
| 0x20   | float     | 4    | X      | X coordinate                           |
| 0x24   | float     | 4    | Y      | Y coordinate                           |
| 0x28   | float     | 4    | Z      | Z coordinate                           |
| 0x2C   | int16     | 2    | parent | Parent bone index (`-1` if none)       |
| 0x2E   | uint16    | 2    | hide   | Hidden in Designer 2 (ignored in-game) |

#### Texture (variable size)

* **Size**: `TextureSize` (from header)
* **Format**: 16-bit TGA-style, ARGB1555
* **Width**: Always 256 pixels (see [Reference](reference.md#core-constants))

---

### .CAR File Format Specification

#### Header (52 bytes)

| Offset   | Type   | Size | Name         | Description |
|----------|--------|------|--------------|-------------|
| `0x0000` | byte   | 32   | **ModelName** | Texture name (often same as CAR filename, e.g., `"Rapt73"` in `ALLO.CAR`). Last 12 bytes usually `msc: #` where `#` is a number. See notes. |
| `0x0020` | uint32 | 4    | **AniCount**  | Number of animations |
| `0x0024` | uint32 | 4    | **SfxCount**  | Number of sounds |
| `0x0028` | uint32 | 4    | **VCount**    | Number of vertices |
| `0x002C` | uint32 | 4    | **FCount**    | Number of faces |
| `0x0030` | uint32 | 4    | **TextureSize** | Texture length in bytes |

---

#### Face Data (64 bytes per face, repeated **FCount** times)

| Type   | Size | Name       | Description |
|--------|------|------------|-------------|
| uint32 | 4    | **v1**     | Vertex 1 index |
| uint32 | 4    | **v2**     | Vertex 2 index |
| uint32 | 4    | **v3**     | Vertex 3 index |
| uint32 | 4    | **tax**    | Texture U coordinate for v1 |
| uint32 | 4    | **tbx**    | Texture U coordinate for v2 |
| uint32 | 4    | **tcx**    | Texture U coordinate for v3 |
| uint32 | 4    | **tay**    | Texture V coordinate for v1 |
| uint32 | 4    | **tby**    | Texture V coordinate for v2 |
| uint32 | 4    | **tcy**    | Texture V coordinate for v3 |
| uint16 | 2    | **flags**  | Bitfield (see [Reference](reference.md#face-flags-16-bit-bitfield)) |
| uint16 | 2    | **DMask**  | Unused in-game (possibly editor-specific) |
| uint32 | 4    | **Distant**| Unused in-game; set during face tree sorting |
| uint32 | 4    | **Next**   | Unused in-game; set during face tree sorting (points to next face) |
| uint32 | 4    | **group**  | Unused in-game (possibly editor-specific) |
| byte   | 12   | **reserv** | Reserved (`0x00`) |

#### Vertex Data (16 bytes per vertex, repeated **VCount** times)

| Type   | Size | Name       | Description |
|--------|------|------------|-------------|
| float  | 4    | **X**      | X coordinate |
| float  | 4    | **Y**      | Y coordinate |
| float  | 4    | **Z**      | Z coordinate |
| uint16 | 2    | **owner**  | Bone index vertex is attached to |
| uint16 | 2    | **hide**   | Hidden in Designer 2 (no in-game effect) |

#### Texture Data

| Type   | Size           | Name       | Description |
|--------|----------------|------------|-------------|
| byte   | **TextureSize**| **texture**| 16-bit TGA-style ARGB1555, always **256 pixels wide** |

#### Animation Data (repeated **AniCount** times)

> Size: `40 + (FramesCount × VCount × 6)` bytes per animation

| Type   | Size | Name           | Description |
|--------|------|----------------|-------------|
| byte   | 32   | **aniName**     | Name of source `.VTL` file |
| uint32 | 4    | **aniKPS**      | Keyframes per second |
| uint32 | 4    | **FramesCount** | Number of animation frames |
| byte   | `FramesCount × VCount × 6` | **aniData** | Per-frame vertex deltas |

##### Per-Frame Vertex Delta (6 bytes per vertex per frame)

| Type  | Size | Name | Description |
|-------|------|------|-------------|
| int16 | 2    | X    | Delta X |
| int16 | 2    | Y    | Delta Y |
| int16 | 2    | Z    | Delta Z |

#### Sound Data (repeated **SfxCount** times)

> Size: `36 + length` bytes per sound

| Type   | Size | Name     | Description |
|--------|------|----------|-------------|
| byte   | 32   | **name** | Sound name |
| uint32 | 4    | **length**| Data length in bytes |
| byte   | `length` | **data** | 16-bit mono PCM @ **22050 Hz** |

#### Animation/Sound Cross-Reference Table (256 bytes total)

> 64 entries × 4 bytes each  
> Maps animation triggers to sound indices

| Type  | Size | Name          | Description |
|-------|------|---------------|-------------|
| int32 | 4    | **sound index**| `-1` = no sound mapped |

**Notes**:
- ModelName consists of two parts: texture name + `msc: #` string (may have embedded nulls or variable offsets)
- Each table entry corresponds to an animation: 1 sound per animation, fixed order (see [Reference](reference.md#engine-limits--validation-rules))
- Sounds are assigned sequentially, order cannot be changed

---

### .3DN File Format Specification

3DN is a simplified version of 3DF/CAR used in *Carnivores: Dinosaur Hunter* (mobile). Model data is stored in 3DN, with texture/animation/sound elsewhere. Files are in `\models` directory.

#### Header (48/80 bytes)

| Offset   | Type     | Size | Name          | Description |
|----------|----------|------|---------------|-------------|
| `0x0000` | uint32   | 4    | **VCount**    | Number of vertices |
| `0x0004` | uint32   | 4    | **FCount**    | Number of faces |
| `0x0008` | uint32   | 4    | **BoneCount** | Number of bones |
| `0x000C` | byte[32] | 32   | **ModelName** | Model name (ASCII string) |
| `0x002C` | uint32   | 4    | —             | Unknown/Padding |
| `0x0030` | uint32   | 4    | **HasSprite** | Whether the model has a sprite (Boolean) |

##### Sprite Data (optional)
If `HasSprite` is non-zero, 32 bytes follow:

| Offset   | Type     | Size | Name           | Description |
|----------|----------|------|----------------|-------------|
| `0x0034` | byte[32] | 32   | **SpriteName** | Sprite name (ASCII string) |

#### Vertex Data (16 bytes per vertex, repeated **VCount** times)

| Type   | Size | Name      | Description |
|--------|------|-----------|-------------|
| float  | 4    | **X**     | X coordinate |
| float  | 4    | **Y**     | Y coordinate |
| float  | 4    | **Z**     | Z coordinate |
| int32  | 4    | **owner** | Bone index vertex is attached to (`-1` if none) |

> Note: `owner` is `int32` (not split uint16) in mobile 3DN files.

#### Face Data (52 bytes per face, repeated **FCount** times)

| Type   | Size | Name       | Description |
|--------|------|------------|-------------|
| uint32 | 4    | **v1**     | Vertex 1 index |
| uint32 | 4    | **v2**     | Vertex 2 index |
| uint32 | 4    | **v3**     | Vertex 3 index |
| int16  | 2    | **tax**    | Texture U coordinate for v1 |
| int16  | 2    | **tay**    | Texture V coordinate for v1 |
| int16  | 2    | **tbx**    | Texture U coordinate for v2 |
| int16  | 2    | **tby**    | Texture V coordinate for v2 |
| int16  | 2    | **tcx**    | Texture U coordinate for v3 |
| int16  | 2    | **tcy**    | Texture V coordinate for v3 |
| uint16 | 2    | **flags**  | Bitfield (usually null, see [Reference](reference.md#face-flags-16-bit-bitfield)) |
| uint16 | 2    | **DMask**  | Unused in-game (possibly editor-specific) |
| uint32 | 4    | **Distant**| Index to previous (parent) face |
| uint32 | 4    | **Next**   | Index to next (child) face |
| uint32 | 4    | **group**  | Unused in-game (possibly editor-specific) |
| uint32 | 4    | **reserv[0]** | Reserved (`0x00`) |
| uint32 | 4    | **reserv[1]** | Reserved (`0x00`) |
| uint32 | 4    | **reserv[2]** | Reserved (`0x00`) |

#### Bone Data (48 bytes per bone, repeated **BoneCount** times)

| Type     | Size | Name      | Description |
|----------|------|-----------|-------------|
| byte[32] | 32   | **name**  | Bone name (ASCII string) |
| float    | 4    | **X**     | X coordinate |
| float    | 4    | **Y**     | Y coordinate |
| float    | 4    | **Z**     | Z coordinate |
| int16    | 2    | **parent**| Parent bone index (`-1` if none) |
| uint16   | 2    | **hide**  | Hidden in Designer 2 (no in-game effect) |

**Notes**:
- UV coordinates reordered from `UUUVVV` to `UVUVUV` for modern hardware
- Uses pixel values (not normalized) for UV fields

---

## Map & Resource Files

### .map File Format

Located in `HUNTDAT\AREAS` subfolder (Carnivores 1/2/Ice Age). Divided into segments:

#### Carnivores 1 Structure

| Start      | End        | Dimensions       | Contents     |
|------------|------------|------------------|--------------|
| 00000000  | 0003FFFF  | 512×512×8        | Surface     |
| 00040000  | 0007FFFF  | 512×512×8        | Textures    |
| 00080000  | 000BFFFF  | 512×512×8        | Textures2   |
| 000B0000  | 000FFFFF  | 512×512×8        | Plants      |
| 00100000  | 0013FFFF  | 512×512×8        | Flags       |
| 00140000  | 0017FFFF  | 512×512×8        | Shadows     |
| 00180000  | 001BFFFF  | 512×512×8        | Height      |
| 001C0000  | 001FFFFF  | 512×512×8        | Structures  |
| 00200000  | 0020FFFF  | 256×256×8        | Fog         |
| 00210000  | 0021FFFF  | 256×256×8        | Sounds      |

##### Segment Details (Carnivores 1)
- **Surface**: 512×512 heightmap (1 byte/unit = 0.5m, max 127.5m). 4m per cell → 2048×2048m area (~4km²)
- **Textures**: 512×512 primary texture index (references .RSC file). Two textures blended per cell.
- **Textures2**: 512×512 secondary texture index. Diagonal split:
  ```
  +--------------+
  |\             |
  | \ Texture 2  |
  |  \           |
  |   \          |
  |    \Texture 1|
  |     \        |
  |      \       |
  |       \      |
  |        \     |
  |Texture 1\    |
  |          \   |
  |           \  |
  |            \ |
  |             \|
  +--------------+
  ```
- **Plants**: 512×512 vegetation/object index (trees, rocks, etc.)
- **Flags**: 512×512 bitfield:
  | Bit | Function |
  |-----|----------|
  | 7   | Undulating effect (water/quicksand/lava) |
  | 6   | Diagonal split direction (0=T2\T1, 1=T1/T2) |
  | 5–2 | Unknown |
  | 1–0 | Texture rotation: 00=none, 01=90°CW, 10=180°, 11=90°CCW |
- **Shadows**: 512×512 brightness (lower = brighter)
- **Height**: 512×512 water bottom height. Underwater trigger: Surface - Height = 48
- **Structures**: 512×512 non-terrain/vegetation objects
- **Fog**: 256×256 fog type index (8×8m per cell)
- **Sounds**: 256×256 ambient sound index

#### Carnivores 2 / Ice Age Structure

| Start      | End        | Dimensions         | Contents              |
|------------|------------|--------------------|-----------------------|
| 00000000  | 000FFFFF  | 1024×1024×8        | Height                |
| 00100000  | 002FFFFF  | 1024×1024×16       | Textures              |
| 00300000  | 004FFFFF  | 1024×1024×16       | Textures2             |
| 00500000  | 005FFFFF  | 1024×1024×8        | Plants                |
| 00600000  | 007FFFFF  | 1024×1024×16       | Flags                 |
| 00800000  | 008FFFFF  | 1024×1024×8        | Dawn Shadows          |
| 00900000  | 009FFFFF  | 1024×1024×8        | Day Shadows           |
| 00A00000  | 00AFFFFF  | 1024×1024×8        | Night Shadows         |
| 00B00000  | 00BFFFFF  | 1024×1024×8        | Water                 |
| 00C00000  | 00CFFFFF  | 1024×1024×8        | Structures            |
| 00D00000  | 00D3FFFF  | 512×512×8          | Sounds                |
| 00D40000  | 00D7FFFF  | 512×512×8          | Fog                   |

##### Segment Details (Carnivores 2/Ice Age)
- **Height**: 1024×1024 (same scaling → 4096×4096m area ~16km²)
- **Textures/Textures2**: 1024×1024 2-byte indices (supports >256 textures)
- **Dawn/Day/Night Shadows**: Separate 1024×1024 brightness maps
- **Water**: 1024×1024 liquid surface index
- **Flags**: 1024×1024 2-byte values (bits TBD)

---

### .rsc Resource Files

Complement .map files. Carnivores 1 has 32-byte header; Carnivores 2/Ice Age have 80-byte header.

#### Data Types
- **byte**: 1 byte (0–255)
- **word**: 2-byte little-endian (0–65535)
- **short**: 2-byte signed little-endian
- **long**: 4-byte signed little-endian
- **single**: 4-byte IEEE float

#### Carnivores 1 Header (32 bytes)

| Field              | Type | Description                     |
|--------------------|------|---------------------------------|
| num_textures       | long | Number of textures              |
| num_objects        | long | Number of objects               |
| atm_light_R        | long | Atmospheric lighting red        |
| atm_light_G        | long | Atmospheric lighting green      |
| atm_light_B        | long | Atmospheric lighting blue       |
| unknown_R          | long | Unknown                         |
| unknown_G          | long | Unknown                         |
| unknown_B          | long | Unknown                         |

#### Carnivores 2/Ice Age Header (80 bytes)

| Field               | Type | Description                          |
|---------------------|------|--------------------------------------|
| num_textures        | long | Number of textures                   |
| num_objects         | long | Number of objects                    |
| dawn_atm_light_R/G/B| long | Dawn atmospheric lighting RGB        |
| day_atm_light_R/G/B | long | Day atmospheric lighting RGB         |
| night_atm_light_R/G/B| long| Night atmospheric lighting RGB       |
| unknown_dawn_R/G/B  | long | Unknown                              |
| unknown_day_R/G/B   | long | Unknown                              |
| unknown_night_R/G/B | long | Unknown                              |

##### Common Blocks (Both Versions)
- **Textures**: `word texture[128][128]` per texture
- **Objects**: Header (unknown fields, point/triangle/bone counts, texture length) → Triangles (64 bytes each) → Points (16 bytes each) → Bones (48 bytes each) → Texture (ARGB1555, 256px wide)
- **Sky**: Carnivores 1: 256×256 16-bit + 128×128 8-bit clouds. Carnivores 2/Ice Age: 3× 256×256 16-bit (dawn/day/night) + clouds
- **Fog**: Count + per-fog RGBA, altitude, poison flag, distance, density
- **Sounds**: Random/ambient sounds (16-bit mono PCM 22050Hz) + usage table
- **Water** (Carnivores 2/Ice Age only): Count + per-water texture index, level, opacity, unknown

---

## Coordinate & Axis Conversion

### Background
Carnivores uses left-handed coordinates (X: left, Y: up, -Z: forward). Blender uses right-handed (X: left, Y: forward, Z: up). Without adjustment, exported models appear mirrored in-game.

### Solution: X-Axis Flip + Winding Fix
1. **X-Flip**: Apply `mathutils.Matrix.Scale(-1, 4, (1, 0, 0))` to mirror across YZ plane, swapping chirality.
   - Controlled by user-toggleable `flip_handedness` BoolProperty in import/export operators.
2. **Winding Order Fix**:
   - Blender exports CCW winding (right-handed front-face)
   - Post-flip, winding becomes CW (invisible front-faces in left-handed engine)
   - Fix: Reverse vertex index order (`faces_arr['v'][:, ::-1]`) and UV arrays when `flip_handedness` is enabled

### Implementation References
- **Operators**: `flip_handedness` prop in `CARNIVORES_OT_import_3df`/`CARNIVORES_OT_export_3df` (operators/), matrix composition in `execute()`
- **Export**: `parsers/export_3df.py` reverses `v`, `all_us`, `all_vs` arrays after collecting face data
- **Import**: `parsers/parse_3df.py` reverses arrays in `parse_3df_faces()` for round-trip consistency

### Testing Notes
- **Round-Trip**: Export axis gizmo with `flip_handedness=True` + `axis_forward='-X'` + `axis_up='Z'`, re-import → no mirroring/culling
- **Edge Cases**: Reversal happens after V-flip (`1.0 - all_vs`) but before optional `flip_u/v`
- **Performance**: O(n) on faces (negligible for <2048 limit)
