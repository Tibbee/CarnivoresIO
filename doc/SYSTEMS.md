# CarnivoresIO Core Systems

Documents the sophisticated algorithms bridging Blender's modern animation system with the Carnivores engine's legacy vertex-animation workflow.

> **Note**: Face flag definitions are in [Reference](reference.md#face-flags-16-bit-bitfield). Engine limits are in [Reference](reference.md#structural-validation-and-compatibility-limits).

---

## Table of Contents
1. [Skeleton Reconstruction](#skeleton-reconstruction-car-models)
2. [Animation Analysis](#animation-analysis-carnivores-1-system)
3. [NLA Sound System](#nla-sound-system)

---

## Skeleton Reconstruction (.car Models)

### Context
Carnivores `.car` files use **Vertex Animation (Shape Keys)**. While every vertex is assigned to a "Bone Owner" (index), the files do **not** store bone positions, rotations, or hierarchy (parent-child relationships). This system reconstructs a functional Blender Armature from that metadata and caches the imported owner map on the mesh for later reconstruction.

### Pipeline Overview

The reconstruction pipeline (`reconstruct_armature` in `utils/animation.py`) proceeds through these stages:

```
1. Owner Data Retrieval     → mesh attribute (carnivores_owner_index) or vertex groups
2. Optional Weight Smoothing → Laplacian smoothing on vertex groups
3. Centroid Calculation      → per-bone-group weighted mean
4. Degeneracy Pruning        → filter out empty groups (None centroids)
5. Disconnected Cluster Detection → BFS spatial clustering
6. Mirror Partner Detection   → adaptive symmetry matching
7. Root Selection            → scored centrality + name + symmetry heuristics
8. MST Hierarchy Inference   → Prim-like scored minimum spanning tree
9. Semantic Naming           → _L / _R suffixing
10. Armature Creation        → Blender edit-bones with PCA-derived leaf tails
```

---

### 1. Owner Cache Preservation

During `.car` import (`parsers/parse_car.py` → `handle_car_owners`), raw owners and their explicit compact mapping are stored on the mesh:

| Data | Type | Content |
|---|---|---|
| `carnivores_owner_index` | `int32` per vertex | Dense reconstruction group ID `0..G-1`; `-1` means unowned |
| `carnivores_owner_source` | `int32` per vertex | Raw, unchanged owner value from the file |
| `carnivores_owner_mapping` | JSON mesh property | Schema version and ordered raw ID/name for every compact group |

Non-negative raw owner IDs are sorted and compacted explicitly. The engine stores owners as signed 16-bit values: owner `0` is a valid group and negative values (normally `-1`) are unowned. Sparse IDs such as `0, 4, 9` therefore map to compact IDs `0, 1, 2` while preserving their exact source values and `CarBone_0`, `CarBone_4`, and `CarBone_9` names.

The reconstruction operator reads the cached compact attribute directly, bypassing potentially edited vertex groups. "Reset to Imported Owners" resolves names from mapping metadata before considering editable vertex-group order. Older meshes without metadata retain a compatibility fallback based on their source attribute.

**Files**: `parsers/parse_car.py`, `utils/rig_reconstruction.py`, `utils/animation.py` (`_get_reconstruction_owner_indices`, `_get_reconstruction_owner_source`)

---

### 2. Pre-Reconstruct Smoothing (Optional)

Before centroid calculation, the user may enable Laplacian vertex weight smoothing via the Rigging Utilities panel. This propagates weights across mesh topology to reduce noise from the original `.car` data without destroying the underlying owner cache.

```python
def smooth_vertex_weights(obj, iterations=3, factor=0.5, joints_only=False):
    # Uses BMesh topology for neighborhood queries
    # joints_only=True: skip vertices where self and all neighbors belong
    #   to the same single group (preserves uniform regions)
```

**File**: `utils/io.py` (`smooth_vertex_weights`)

---

### 3. Centroid Calculation with Degeneracy Pruning

Each bone's head is calculated as the mean position of all vertices assigned to that bone index:

$$\text{Centroid}_i = \frac{1}{|V_i|} \sum_{v \in V_i} \text{Position}(v)$$

- **Owner-cache path** (preferred): Uses `np.add.at(centroids, owners[valid], v_pos[valid])` for vectorized accumulation, then divides by vertex count per group.
- **Vertex group path** (fallback): Iterates mesh vertices and their group weights, doing a weighted sum per group.
- **Degeneracy pruning**: Groups with zero vertices return `None` instead of falling back to `mesh_mean`. This prevents "ghost bones" at the mesh center that would become spurious hubs in the MST.

**File**: `utils/animation.py` (`calculate_vertex_group_centroids`)

---

### 4. Disconnected Cluster Detection

Before hierarchy inference, centroids are clustered spatially to isolate detached groups (tongues, jaw flaps, fins) from the main skeleton.

**Algorithm**: BFS flood-fill on the centroid graph

```
1. Compute all pairwise Euclidean distances between centroids
2. Set threshold = 2.0 × std(pairwise_distances) — adaptive to model scale
3. BFS from each unlabeled centroid, connecting neighbors within threshold
4. Keep only the largest cluster (by centroid count) for the main bone tree
5. Excluded groups are stored in reconstruction metadata for diagnostics
```

This prevents the MST from forcibly attaching disconnected groups to the nearest spine bone, which would produce a wildly incorrect hierarchy.

**File**: `utils/animation.py` (`_detect_disconnected_clusters`)

---

### 5. Mirror Partner Detection

Identifies pairs of bones that are symmetric mirror partners around the mesh's X-center, using adaptive tolerances based on bounding box dimensions:

| Check | Tolerance |
|---|---|
| X-position match (opposite signs, sum near 0) | 8% of total X-width |
| Y and Z position match | 12% of max(Y-depth, Z-height) |

A bone is a mirror partner if there exists another centroid with opposite X (within tolerance) and matching YZ (within tolerance). The result is used in root selection to blacklist lateral bones from becoming the root.

**File**: `utils/animation.py` (`_find_mirror_partners`)

---

### 6. Root Selection (Scored Centrality)

Root selection uses a weighted scoring system — lower score = better root candidate:

| Factor | Weight |
|---|---|
| **Name priority** | `floor`, `root`, `pelvis`, `hips`, `spine` keywords instantly win (unless they're mirror-paired or have `_L` / `_R` suffix) |
| **Geometric centrality** | `sum(pairwise_distances) + 0.5 × distance_to_median` |
| **X-offset penalty** | `(abs(X − center_X) / width_X) × 5.0` — heavily penalizes lateral bones |
| **Weight density reward** | `(weight / max_weight) × 0.25 × exp(-X_offset / width_X)` — only rewards midline bones |
| **Mirror blacklist** | Any bone with a mirror partner gets `score = inf` |

The winner is `argmin(scores)`. An optional manual root override (`root_override_idx`) skips all scoring logic.

**File**: `utils/animation.py` (`select_root_bone`)

---

### 7. MST Hierarchy Inference (Scored Edge MST)

Since the file stores no parent-child information, the system infers hierarchy using a **greedy Prim-like Minimum Spanning Tree** where each candidate edge has a scored cost:

```python
def _score_reconstruction_edge(parent_idx, child_idx, positions, center_x,
                                x_margin, center_distances, body_axis,
                                group_weights=None):
    score = dist
    # Cross-body penalty (×50): prevents left↔right connections
    if cross_body_link:
        score *= 50.0
    # Centrality gradient: prefer parent more central than child
    if parent_radius > child_radius:
        score += (parent_radius - child_radius) * 0.75
    else:
        score -= min(child_radius - parent_radius, dist) * 0.10
    # Body-axis alignment (×0.90): prefer edges along main body axis
    score *= (1.0 - alignment * 0.10)
    # Weight bias: denser groups make better parents
    score *= (1.0 - (parent_weight / max_weight) * 0.05)
    score *= (1.0 + (child_weight / max_weight) * 0.02)
    return score
```

The **body axis** is computed via eigendecomposition of the centroid covariance matrix (PCA). The eigenvector with the largest eigenvalue defines the creature's main longitudinal axis:

```python
def _compute_reconstruction_body_axis(centroids):
    centered = positions - np.mean(positions, axis=0)
    cov = np.cov(centered, rowvar=False)
    eigvals, eigvecs = np.linalg.eigh(cov)
    return eigvecs[:, argmax(eigvals)]  # Principal component
```

**File**: `utils/animation.py` (`infer_hierarchy_mst`, `_score_reconstruction_edge`, `_compute_reconstruction_body_axis`)

---

### 8. Semantic Naming (_L / _R Suffixing)

After MST inference, a post-pass appends bilateral suffixes to generic bone names:

```python
def _apply_semantic_suffixes(obj, bone_names, centroids, center_x):
    # Adaptive margin: max(width_X × 0.03, 0.01) — 3% of width, min 1cm
    if X > center_x + margin:  → "{name}_L"
    if X < center_x - margin:  → "{name}_R"
```

- Only renames names that don't already end in `_L`, `_R`, `.L`, `.R`, ` left`, ` right`
- **Vertex groups on the mesh are renamed in sync** to preserve skinning weights
- Gated by a panel toggle: `Auto-detect Left/Right` (on by default)

**File**: `utils/animation.py` (`_apply_semantic_suffixes`)

---

### 9. Armature Creation with PCA Leaf Tail Placement

`create_armature` in `utils/io.py` creates Blender edit-bones with head positions at centroids, then sets tails:

#### Parent Bone Tails
- **Single child**: Tail points to child's head; `use_connect = True`
- **Multiple children**: Tail points to centroid of all child heads; `use_connect = False` on each child

#### Leaf Bone Tails (SVD / PCA)
For terminal bones (no children), the tail direction is derived from the local geometry of the owner-group vertices via **Singular Value Decomposition**:

```python
def _calculate_pca_direction(verts, fallback_dir):
    centered = verts - np.mean(verts, axis=0)
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    direction = vh[0]  # Principal component (longest axis)
    # Flip to point away from parent
    if direction.dot(fallback_dir) < 0:
        direction *= -1
    return direction.normalized()
```

- Requires ≥ 2 vertices in the group; falls back to parent-to-child direction otherwise
- Tail length = `max(child_distance × 0.5, global_median × 0.3)`
- **Safety clamp**: minimum bone length of 0.01 units

#### Model Forward Axis
The overall model forward direction is determined by comparing AABB dimensions: if X-span > Y-span, forward = X axis, else forward = Y axis.

**File**: `utils/io.py` (`create_armature`, `_calculate_pca_direction`)

---

### 10. Reconstruction Metadata

After armature creation, diagnostic metadata is stored on the armature object as custom properties:

| Custom Property | Content |
|---|---|
| `carnivores_reconstruct_root` | Name of the selected root bone |
| `carnivores_reconstruct_root_idx` | Original owner index of the root |
| `carnivores_reconstruct_parent_map` | Stringified dict mapping child indices → parent indices |
| `carnivores_reconstruct_skipped` | Comma-separated list of skipped (degenerate/disconnected) group indices |
| `carnivores_reconstruct_skipped_count` | Count of skipped groups |
| `carnivores_reconstruct_cluster_count` | Number of spatial clusters found |
| `carnivores_reconstruct_source` | `"stored owner attribute"` or `"vertex groups"` |

**File**: `utils/animation.py` (`reconstruct_armature`)

---

### User Workflow

1. Import `.car` file
2. Open `Carnivores` tab in N-Panel → `Carnivores Animation`
3. Optionally enable **Pre-Reconstruct Smoothing** in the Rigging Utilities box and tune the parameters
4. Optionally set a **Manual Root Override** (by index) for asymmetric creatures
5. Ensure **Auto-detect Left/Right** is enabled for semantic naming
6. Click **"Reconstruct Rig from Owners"**
   - Mesh auto-parented to new skeleton
   - Armature Modifier added
   - Vertex groups match bone names → immediately poseable
7. Inspect results via **"Show Rig Debug Report"** (shows root, centroids, parent map, skipped groups)

### Implementation Files

| File | Responsibilities |
|---|---|
| `utils/animation.py` | Owner cache access, centroid calc, clustering, mirror detection, root selection, MST, semantic naming, reconstruction orchestration |
| `utils/io.py` | Armature creation, PCA leaf tails, Laplacian weight smoothing, armature modifier assignment |
| `operators/animation.py` | UI panel, `CARNIVORES_OT_reconstruct_armature` operator, debug report |
| `parsers/parse_car.py` | Owner index preservation during import |

---

## Animation Analysis (Carnivores 1 System)

Comprehensive analysis of the Carnivores 1 animation engine: a hybrid **Vertex Animation (Morph Target)** system with procedural deformations and cross-fade blending.

### Core Data Structures (from `Hunt.h`)

#### `TAni` (Named Animation)
Used for character animations (dinos, weapons):
```c++
typedef struct _Animation {
  char aniName[32];    // Name (e.g., "run", "die")
  int aniKPS;          // Keyframes Per Second
  int FramesCount;     // Total vertex frames
  int AniTime;         // Duration in milliseconds
  short int* aniData;  // Raw vertex data (XYZ as shorts)
} TAni;
```

#### `TVTL` (Vertex Transform List)
Simplified `TAni` for generic objects (plants, flags) without named states:
```c++
typedef struct _VTLdata {  
  int aniKPS, FramesCount, AniTime;
  short int* aniData;
} TVTL;
```

#### `TCharacterInfo`
Shared resources for a character type:
```c++
typedef struct _TCharacterInfo {
  TAni Animation[64];  // Available animations
  int  Anifx[64];      // Sound mapping: Animation Index → Sound Index
} TCharacterInfo;
```

#### `TCharacter`
Active character instance:
```c++
typedef struct _TCharacter {
  int Phase;          // Current Animation Index
  int FTime;          // Current Frame Time (ms) within Phase
  
  // Blending State
  int PrevPhase;      // Previous Animation Index
  int PrevPFTime;     // Previous Frame Time at transition start
  int PPMorphTime;    // Time since transition started (0-256ms)
  
  // Procedural State
  float bend;         // Body bending (turning)
  float beta, gamma;  // Pitch/Roll banking
  float scale;        // Random size variation
} TCharacter;
```

### Animation Pipeline
Updates vertex positions *in-place* before rendering (handled by `CreateChMorphedModel`/`CreateMorphedObject` in `Characters.cpp`). Three stages:

#### 1. Vertex Interpolation (Linear)
Interpolates between keyframes for smooth motion regardless of framerate:
- `CurFrame` (integer) = start keyframe
- `SplineD` (fractional, 0-255) = interpolation weight `k2`
- `k1 = 1.0 - k2`
- Formula: `Vertex = Keyframe[i] * k1 + Keyframe[i+1] * k2`

#### 2. Phase Blending (Morphing)
Cross-fades between previous and current animation to prevent "popping":
- **Blend Duration**: `PMORPHTIME` (256ms)
- **Blend Factor**: `pmk1 = PPMorphTime / PMORPHTIME`
- **Formula**: `FinalVertex = (CurrentAnimVertex * pmk1) + (PreviousAnimVertex * (1.0 - pmk1))`
- Discards previous animation when `PPMorphTime >= PMORPHTIME`

#### 3. Procedural Deformation
Post-blend deformations for physical movement:
- **Bending**: Rotation around Y-axis based on Z-position (curved spine when turning)
- **Banking**: Entire model rotation to align with terrain slope (`beta` = pitch, `gamma` = roll)
- **Scaling**: All vertices multiplied by `cptr->scale` for size variety

### Sound Synchronization
- **Mapping**: `Anifx[AnimationIndex]` stores sound index
- **Trigger**: Checked in `ActivateCharacterFx` (`Characters.cpp`) on phase change
- **Spatial Audio**: Played via `AddVoice3d` at character's 3D position

### AI & State Management
- **FTime**: Accumulates `TimeDt` (delta time)
- **Looping**: `FTime %= AniTime`
- **Transitions**: AI logic (e.g., `AnimateRaptor`) dictates `Phase` changes
- **Morph Optimization**: Compatible phase switches (Walk→Run) scale `FTime` to match cycle position

### Rendering Integration
Renderer (Software/D3D/Glide) receives pre-transformed vertex array (`mptr->gVertex`) from `AnimateCharacters` loop. Decouples animation logic from rendering backend.

---

## NLA Sound System

Synchronizes sound playback with NLA animation strips from imported `.car` files.

### Key Challenges Solved
1. **Reliable Playback Detection**: Prevents sound during timeline scrubbing (only plays during active animation)
2. **Clean File Management**: Handles temporary sound files without cluttering project directories

---

### Part 1: Playback-Only Sound

#### Problem
Standard `bpy.context.screen.is_animation_playing` was unreliable: returned `True` during NLA Tweak Mode even without active playback.

#### Solution: Custom Playback State Machine
Three handler functions using a global `_is_real_playback` flag:

##### 1. `playback_started_handler` (On Switch)
Registered to `animation_playback_pre` (fires on playback start):
```python
_is_real_playback = False

def playback_started_handler(scene):
    global _is_real_playback
    _is_real_playback = True
```

##### 2. `playback_stopped_handler` (Off Switch + Cleanup)
Registered to `animation_playback_post` (fires on playback stop):
```python
def playback_stopped_handler(scene):
    global _is_real_playback, _playing_sounds
    _is_real_playback = False
    if _playing_sounds:
        for handle, _ in _playing_sounds.values():
            handle.stop()
        _playing_sounds.clear()
```

##### 3. `carnivores_nla_sound_handler` (Playback Logic)
Registered to `frame_change_post` (fires every frame change):
```python
def carnivores_nla_sound_handler(scene):
    global _playing_sounds, _is_real_playback
    if not _is_real_playback:  # Only runs during active playback
        return
    # ... find active strip + play sound logic
```

#### Registration
Managed in `__init__.py`:
```python
def register():
    # ... other registrations
    bpy.app.handlers.frame_change_post.append(operators.carnivores_nla_sound_handler)
    bpy.app.handlers.animation_playback_pre.append(operators.playback_started_handler)
    bpy.app.handlers.animation_playback_post.append(operators.playback_stopped_handler)

def unregister():
    # ... cleanup handlers
```

---

### Part 2: Temporary Sound File Management

#### Problem
`aud` module requires unpacked `.wav` files to play, but `unpack()` writes to `sounds/` folder and doesn't auto-delete. Deleting immediately breaks playback.

#### Solution: Deferred Cleanup on Unregister

##### 1. Track Temp Files
Global set in `operators.py`:
```python
_temp_sound_files = set()
```
In `import_car_sounds` (`utils.py`):
```python
if sound_block.packed_file:
    sound_block.unpack(method='USE_LOCAL')
    unpacked_filepath = bpy.path.abspath(sound_block.filepath)
    operators._temp_sound_files.add(unpacked_filepath)
    sound_block.pack()
```

##### 2. Cleanup on Unregister
In `__init__.py` `unregister()`:
```python
def unregister():
    # ... other cleanup
    for filepath in operators._temp_sound_files:
        if os.path.exists(filepath):
            os.remove(filepath)
    operators._temp_sound_files.clear()
```

### Result
- Sounds only play during active animation playback
- No temporary files left behind when addon is disabled/Blender closes
