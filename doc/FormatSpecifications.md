# `.3df` File Format Specification

### Header (16 bytes)

| Offset | Type   | Size | Name        | Description             |
| ------ | ------ | ---- | ----------- | ----------------------- |
| 0x0000 | uint32 | 4    | VCount      | Number of vertices      |
| 0x0004 | uint32 | 4    | FCount      | Number of faces         |
| 0x0008 | uint32 | 4    | BoneCount   | Number of bones         |
| 0x000C | uint32 | 4    | TextureSize | Texture length in bytes |

### Face (64 bytes each)

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

#### Face Flags (`flags` bitfield)

| Bit  | Mask   | Name          | Description                      |
| ---- | ------ | ------------- | -------------------------------- |
| 0    | 0x0001 | sfDoubleSide  | Face textured on both sides      |
| 1    | 0x0002 | sfDarkBack    | Dark back side                   |
| 2    | 0x0004 | sfOpacity     | Transparent                      |
| 3    | 0x0008 | sfTransparent | Non-solid (bullets pass through) |
| 4    | 0x0010 | sfMortal      | Marks as a target zone           |
| 5    | 0x0020 | sfPhong       | Phong-mapped                     |
| 6    | 0x0040 | sfEnvMap      | Environment-mapped               |
| 7    | 0x0080 | sfNeedVC      | Purpose unknown                  |
| 8–14 | —      | —             | Unused                           |
| 15   | 0x8000 | sfDark        | Dark front side                  |

### Vertex (16 bytes each)

| Offset | Type   | Size | Name  | Description                            |
| ------ | ------ | ---- | ----- | -------------------------------------- |
| 0x00   | float  | 4    | X     | X coordinate                           |
| 0x04   | float  | 4    | Y     | Y coordinate                           |
| 0x08   | float  | 4    | Z     | Z coordinate                           |
| 0x0C   | uint16 | 2    | owner | Bone index attached to                 |
| 0x0E   | uint16 | 2    | hide  | Hidden in Designer 2 (ignored in-game) |

### Bone (48 bytes each)

| Offset | Type      | Size | Name   | Description                            |
| ------ | --------- | ---- | ------ | -------------------------------------- |
| 0x00   | byte\[32] | 32   | name   | Bone name (ASCII string)               |
| 0x20   | float     | 4    | X      | X coordinate                           |
| 0x24   | float     | 4    | Y      | Y coordinate                           |
| 0x28   | float     | 4    | Z      | Z coordinate                           |
| 0x2C   | int16     | 2    | parent | Parent bone index (`-1` if none)       |
| 0x2E   | uint16    | 2    | hide   | Hidden in Designer 2 (ignored in-game) |

### Texture (variable size)

* **Size**: `TextureSize` (from header)
* **Format**: 16-bit TGA-style, ARGB1555
* **Width**: Always 256 pixels

# .CAR File Format Specification

## Header (52 bytes)

| Offset   | Type   | Size | Name         | Description |
|----------|--------|------|--------------|-------------|
| `0x0000` | byte   | 32   | **ModelName** | Texture name (often same as CAR filename, e.g., `"Rapt73"` in `ALLO.CAR`). Last 12 bytes usually `msc: #` where `#` is a number. See notes. |
| `0x0020` | uint32 | 4    | **AniCount**  | Number of animations |
| `0x0024` | uint32 | 4    | **SfxCount**  | Number of sounds |
| `0x0028` | uint32 | 4    | **VCount**    | Number of vertices |
| `0x002C` | uint32 | 4    | **FCount**    | Number of faces |
| `0x0030` | uint32 | 4    | **TextureSize** | Texture length in bytes |

---

## Face Data (64 bytes per face, repeated **FCount** times)

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
| uint16 | 2    | **flags**  | Bitfield (see below) |
| uint16 | 2    | **DMask**  | Unused in-game (possibly editor-specific) |
| uint32 | 4    | **Distant**| Unused in-game; set during face tree sorting |
| uint32 | 4    | **Next**   | Unused in-game; set during face tree sorting (points to next face) |
| uint32 | 4    | **group**  | Unused in-game (possibly editor-specific) |
| byte   | 12   | **reserv** | Reserved (`0x00`) |

### Face Flags (16-bit)

| Bit | Mask     | Name            | Description |
|-----|----------|------------------|-----------|
| 0   | `0x0001` | **sfDoubleSide** | Face textured on both sides |
| 1   | `0x0002` | **sfDarkBack**   | Dark back side |
| 2   | `0x0004` | **sfOpacity**    | Face is transparent |
| 3   | `0x0008` | **sfTransparent**| Non-solid (bullets pass through) |
| 4   | `0x0010` | **sfMortal**     | Target zone |
| 5   | `0x0020` | **sfPhong**      | Phong shading |
| 6   | `0x0040` | **sfEnvMap**     | Environment mapped |
| 7   | `0x0080` | **sfNeedVC**     | (Purpose unclear) |
| 8–14| —        | **unused**       | — |
| 15  | `0x8000` | **sfDark**       | Dark front side |

---

## Vertex Data (16 bytes per vertex, repeated **VCount** times)

| Type   | Size | Name       | Description |
|--------|------|------------|-------------|
| float  | 4    | **X**      | X coordinate |
| float  | 4    | **Y**      | Y coordinate |
| float  | 4    | **Z**      | Z coordinate |
| uint16 | 2    | **owner**  | Bone index vertex is attached to |
| uint16 | 2    | **hide**   | Hidden in Designer 2 (no in-game effect) |

---

## Texture Data

| Type   | Size           | Name       | Description |
|--------|----------------|------------|-------------|
| byte   | **TextureSize**| **texture**| 16-bit TGA-style ARGB1555, always **256 pixels wide** |

---

## Animation Data (repeated **AniCount** times)

> Size: `40 + (FramesCount × VCount × 6)` bytes per animation

| Type   | Size | Name           | Description |
|--------|------|----------------|-------------|
| byte   | 32   | **aniName**     | Name of source `.VTL` file |
| uint32 | 4    | **aniKPS**      | Keyframes per second |
| uint32 | 4    | **FramesCount** | Number of animation frames |
| byte   | `FramesCount × VCount × 6` | **aniData** | Per-frame vertex deltas |

### Per-Frame Vertex Delta (6 bytes per vertex per frame)

| Type  | Size | Name | Description |
|-------|------|------|-------------|
| int16 | 2    | X    | Delta X |
| int16 | 2    | Y    | Delta Y |
| int16 | 2    | Z    | Delta Z |

---

## Sound Data (repeated **SfxCount** times)

> Size: `36 + length` bytes per sound

| Type   | Size | Name     | Description |
|--------|------|----------|-------------|
| byte   | 32   | **name** | Sound name |
| uint32 | 4    | **length**| Data length in bytes |
| byte   | `length` | **data** | 16-bit mono PCM @ **22050 Hz** |

---

## Animation/Sound Cross-Reference Table (256 bytes total)

> 64 entries × 4 bytes each  
> Maps animation triggers to sound indices

| Type  | Size | Name          | Description |
|-------|------|---------------|-------------|
| int32 | 4    | **sound index**| `-1` = no sound mapped |

ModelName always consists of two parts: the first is the name of the model's texture, and the second is almost always msc: #.
In many models, the texture name has an additional character(s) after the first null character(s); for example, PAR2.CAR in Carnivores and Carnivores 2 has the name Par2\x00e.
While in most models, the msc: string starts at 24 bytes (and is thus 8 bytes long), it instead starts at 20 bytes in many Carnivores Ice Age models (making it 12 bytes long); for example, Bear.car has the value msc: 4\x00: 5. In some cases it has embedded null bytes, similar to the texture name; for example, WEAPON2.CAR in Carnivores and X_BOW.CAR in Carnivores 2 (both for the X-Bow) have the value msc: 4\x006. EXPLO.CAR has the hex value \xfa\x0f\x00\x00D\x02 instead of any "msc" string

Each entry in the animation/sound cross-reference table corresponds to an animation. Because of this, sounds are assigned to the animations sequentially, only one sound can be assigned to each animation, and the order of animations in the table cannot be changed

# .3DN File Format Specification

3DN is a slightly simplified version of the 3DF and CAR file formats from the Action Forms-developed *Carnivores* games, specifically used in *Carnivores: Dinosaur Hunter*. The 3DN files reorder faces and vertices, storing vertices first. The model data is stored in the 3DN file, while the texture, animations, and sounds are stored elsewhere. These files are typically located in the `\models` directory.

## Header (48/80 bytes)

| Offset   | Type     | Size | Name          | Description |
|----------|----------|------|---------------|-------------|
| `0x0000` | uint32   | 4    | **VCount**    | Number of vertices |
| `0x0004` | uint32   | 4    | **FCount**    | Number of faces |
| `0x0008` | uint32   | 4    | **BoneCount** | Number of bones |
| `0x000C` | byte[32] | 32   | **ModelName** | Model name (ASCII string) |
| `0x002C` | uint32   | 4    | —             | Unknown/Padding |
| `0x0030` | uint32   | 4    | **HasSprite** | Whether the model has a sprite (Boolean) |

### Sprite Data (optional)

If `HasSprite` is non-zero, the following 32 bytes are present:

| Offset   | Type     | Size | Name           | Description |
|----------|----------|------|----------------|-------------|
| `0x0034` | byte[32] | 32   | **SpriteName** | Sprite name (ASCII string) |

---

## Vertex Data (16 bytes per vertex, repeated **VCount** times)

| Type   | Size | Name      | Description |
|--------|------|-----------|-------------|
| float  | 4    | **X**     | X coordinate |
| float  | 4    | **Y**     | Y coordinate |
| float  | 4    | **Z**     | Z coordinate |
| int32  | 4    | **owner** | Bone index vertex is attached to (`-1` if none) |

---

## Face Data (52 bytes per face, repeated **FCount** times)

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
| uint16 | 2    | **flags**  | Bitfield (Usually null) |
| uint16 | 2    | **DMask**  | Unused in-game (possibly editor-specific) |
| uint32 | 4    | **Distant**| Index to previous (parent) face |
| uint32 | 4    | **Next**   | Index to next (child) face |
| uint32 | 4    | **group**  | Unused in-game (possibly editor-specific) |
| uint32 | 4    | **reserv[0]** | Reserved (`0x00`) |
| uint32 | 4    | **reserv[1]** | Reserved (`0x00`) |
| uint32 | 4    | **reserv[2]** | Reserved (`0x00`) |

---

## Bone Data (48 bytes per bone, repeated **BoneCount** times)

| Type     | Size | Name      | Description |
|----------|------|-----------|-------------|
| byte[32] | 32   | **name**  | Bone name (ASCII string) |
| float    | 4    | **X**     | X coordinate |
| float    | 4    | **Y**     | Y coordinate |
| float    | 4    | **Z**     | Z coordinate |
| int16    | 2    | **parent**| Parent bone index (`-1` if none) |
| uint16   | 2    | **hide**  | Hidden in Designer 2 (no in-game effect) |

---

## Notes

- The **owner** field in the vertex block is treated as an `int32` by the mobile games, rather than two separate `uint16` fields as used by the PC games and editors. There are no models with any non-null values in the second field, and no plans for editors to export to 3DN, so this is not an issue.
  
- The texture UV coordinates were reordered from `UUUVVV` to `UVUVUV` (pairs of UVs) by Tatem because the latter ordering is more convenient for modern hardware to handle. However, unlike typical modern implementations, which use normalized scalar values, 3DN files (and their CAR and 3DF predecessors) use pixel values for these fields.

