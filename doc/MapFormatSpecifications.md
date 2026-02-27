# Carnivores Saga - The .map Files

## Structure of the .map Files

These files are located in the HUNTDAT\AREAS subfolder in Carnivores, Carnivores 2, and Carnivores Ice Age, although the format used in the first game differs from the later two. The files are divided into several segments.

### For Carnivores

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

### Segment Descriptions (Carnivores)

**Surface**  
A 512×512 matrix of 1-byte values (8 bits, range 0–255) indicating terrain height. Each unit corresponds to half a meter, so the maximum height is 127.5 m at value 255. Each grid cell corresponds to 4 meters, resulting in a 2048 m × 2048 m area (~4 km²). Water surface is always flat.

**Textures**  
A 512×512 matrix of 1-byte values, each an index to a texture (stored in the .RSC file) applied as the primary texture to a grid cell. Two textures are blended per cell.

**Textures2**  
A 512×512 matrix of 1-byte values indexing the secondary texture for each cell.

Cell diagonal split:

```
+--------------+
|\             |
| \            |
|  \           |
|   \          |
|    \Texture 1|
|     \        |
|      \       |
|       \      |
|        \     |
|Texture 2\    |
|          \   |
|           \  |
|            \ |
|             \|
+--------------+
```

**Plants** (referred to as Objects in some sections)  
A 512×512 matrix of 1-byte values indexing vegetation objects (trees, bushes, cacti, rocks, etc.) from the .RSC file.

**Flags**  
A 512×512 matrix of 1-byte values containing bit flags for texture handling:

| Bit | Function |
|-----|----------|
| 7   | Undulating effect (0 = none, 1 = present, e.g., water, quicksand, lava) |
| 6   | Diagonal split direction (0 = T2\T1, 1 = T1/T2) |
| 5–2 | To be determined |
| 1–0 | Texture rotation:<br>00 = no rotation<br>01 = 90° clockwise<br>10 = 180°<br>11 = 90° counter-clockwise |

**Shadows**  
A 512×512 matrix of 1-byte values controlling terrain darkness (lower value = brighter).

**Height**  
A 512×512 matrix of 1-byte values for water body bottoms. Underwater effect triggers when the difference from Surface is exactly 48.

**Structures**  
A 512×512 matrix of 1-byte values placing non-terrain/non-vegetation structures (usually layered on top).

**Fog**  
A 256×256 matrix of 1-byte values (each cell covers 8 m × 8 m). Values index fog types from the .RSC file.

**Sounds**  
A 256×256 matrix of 1-byte values indexing ambient sounds from the .RSC file.

### For Carnivores 2 and Ice Age

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

### Segment Descriptions (Carnivores 2 / Ice Age)

**Height**  
A 1024×1024 matrix of 1-byte values (0–255) for terrain height. Same scaling as Carnivores 1, but results in a 4096 m × 4096 m area (~16 km²).

**Textures**  
A 1024×1024 matrix of 2-byte values indexing primary textures (supports >256 textures).

**Textures2**  
A 1024×1024 matrix of 2-byte values indexing secondary textures.

Diagonal split same as above.

**Plants**  
A 1024×1024 matrix of 1-byte values indexing vegetation/rocks.

**Flags**  
A 1024×1024 matrix of 2-byte values. All bits currently marked as "To be determined."

**Dawn / Day / Night Shadows**  
Separate 1024×1024 1-byte matrices controlling brightness per time of day (higher value = brighter).

**Water**  
A 1024×1024 matrix of 1-byte values indicating liquid surfaces above sea level.

**Structures**  
A 1024×1024 matrix of 1-byte values for additional placeable objects.

**Sounds** / **Fog**  
512×512 matrices (each cell covers 8 m × 8 m) indexing sounds and fog types from the .RSC file.

# Carnivores Saga - The .rsc Files

## Structure of the .rsc Files

These files complement the .map files. Carnivores 1 uses a 32-byte header; Carnivores 2 and Ice Age use an 80-byte header. Carnivores 1 has one sky bitmap; later games have three (dawn/day/night). Carnivores 2/Ice Age add a water block at the end.

### Data Types Used

- **byte**: 1 byte (0–255)
- **word**: 2 bytes, little-endian (0–65535)
- **short**: 2 bytes, little-endian signed (-32768–32767)
- **long**: 4 bytes, little-endian signed
- **single**: 4 bytes, IEEE single-precision float

### For Carnivores

#### Header Block (32 bytes)

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

#### Textures Block
`word texture[128][128]` repeated for each texture.

#### Objects Block
Repeated for each object.

**Object Header Sub-block**

| Field      | Type | Description                 |
|------------|------|-----------------------------|
| Ob1–Ob16   | long | Unknown                     |
| num_points | long | Number of points            |
| num_triang | long | Number of triangles         |
| num_bones  | long | Number of bones             |
| long_tex   | long | Length of texture block     |

**Triangles Sub-block**  
`num_triang` × 64-byte entries (vertex indices, UV coords, parent triangle, unknowns).

**Points Sub-block**  
`num_points` × 16-byte entries: X/Y/Z (single), bone index (long).

**Bones Sub-block**  
Groups of 48-byte bone entries: name[32], X/Y/Z (single), parent (short), unknown (short).

**Texture Sub-block**  
Raw 16-bit TGA-style data, always 256 pixels wide, variable height.

#### Sky Block
`word sky_bmp[256][256]` + `byte clouds_bmp[128][128]` (8-bit grayscale).

#### Fog Block
`long num_fogs` followed by per-fog data: RGBA[4], altitude (single), poison flag (long), min distance (single), density (single).

#### Sounds Block
- Random sounds: count, then length + data chunks (16-bit mono PCM, 22050 Hz)
- Ambient sounds: same structure
- Usage table: 16 entries with index + unknowns, plus final counts

### For Carnivores 2 and Ice Age

#### Header Block (80 bytes)

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

Textures and Objects blocks are identical to Carnivores 1, with an added optional **Sprite Sub-block** per object: `word sprite_bmp[128][128]`.

#### Sky Block
Three 256×256 16-bit bitmaps (dawn, day, night) + clouds shadowmap.

Fog and Sounds blocks same as Carnivores 1.

#### Water Block (final block)
`long num_water` followed by per-water entries: texture index (long), level (long), opacity (single), unknown (long).
