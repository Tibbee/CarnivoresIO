# CarnivoresIO Core Systems

Documents the sophisticated algorithms bridging Blender's modern animation system with the Carnivores engine's legacy vertex-animation workflow.

> **Note**: Face flag definitions are in [Reference](reference.md#face-flags-16-bit-bitfield). Engine limits are in [Reference](reference.md#engine-limits--validation-rules).

---

## Table of Contents
1. [Skeleton Reconstruction](#skeleton-reconstruction-car-models)
2. [Animation Analysis](#animation-analysis-carnivores-1-system)
3. [NLA Sound System](#nla-sound-system)

---

## Skeleton Reconstruction (.car Models)

### Context
Carnivores `.car` files use **Vertex Animation (Shape Keys)**. While every vertex is assigned to a "Bone Owner" (index), the files do **not** store bone positions, rotations, or hierarchy (parent-child relationships). This system reconstructs a functional Blender Armature from that metadata.

### Algorithmic Solution

#### 1. Weighted Centroid Detection
The "Head" of each bone is calculated as the **Weighted Center of Mass** of all vertices assigned to that bone index.
- **Logic**: $\text{Centroid} = \frac{\sum (\text{Vertex Position} \times \text{Weight})}{\sum \text{Weight}}$
- **Implementation**: Optimized via `numpy` in `utils/animation.py` (`calculate_vertex_group_centroids`)

#### 2. Hierarchy Inference (MST)
Since the file doesn't store parents, the system infers structure using proximity:
- **Algorithm**: Variation of **Prim's Algorithm** for Minimum Spanning Tree (MST)
- **Logic**:
  1. Start with root bone (center-most)
  2. Iteratively connect nearest unconnected centroid to existing tree
- **Result**: Correctly handles branching (e.g., two legs from pelvis) unlike simple chain scripts

#### 3. Professional Bone Orientation
- **Heads**: Placed at group centroid
- **Tails (Parents)**: Point to first child's head
- **Tails (Leaves)**: Terminal bones (fingertips, tail tips) point away from parent for intuitive rotation
- **Connectivity**: `use_connect` enabled for single-child chains

### User Workflow
1. Import `.car` file
2. Open `Carnivores` tab in N-Panel → `Carnivores Animation`
3. Click **"Reconstruct Rig from Owners"**
   - Mesh auto-parented to new skeleton
   - Armature Modifier added
   - Vertex groups match bone names → immediately poseable

### Implementation Files
- `utils/animation.py`: Centroids, MST inference, armature creation
- `operators/animation.py`: UI panel + `CARNIVORES_OT_reconstruct_armature` operator

### Future Enhancements
- Handedness sync with "Flip Handedness" import toggle
- Automatic IK constraint generation for legs
- Template presets (Dinosaur/Biped) to override MST with known hierarchies

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
