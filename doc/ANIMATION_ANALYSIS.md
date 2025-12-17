# Carnivores 1 Animation System Analysis

This document provides a comprehensive analysis of the animation system used in the Carnivores 1 game source code. The system is a hybrid **Vertex Animation (Morph Target)** engine enhanced with **Procedural Deformations** and **Cross-Fade Blending**.

## 1. Core Data Structures

The animation system relies on several key structures defined in `Hunt.h`:

### `TAni` (Named Animation)
Used for Character animations (dinos, player weapon).
```c++
typedef struct _Animation {
  char aniName[32];    // Name (e.g., "run", "die")
  int aniKPS;          // Keyframes Per Second
  int FramesCount;     // Total number of vertex frames
  int AniTime;         // Duration in milliseconds
  short int* aniData;  // Raw vertex data (XYZ stored as shorts)
} TAni;
```

### `TVTL` (Vertex Transform List)
A simplified version of `TAni` used for generic animated objects (plants, flags) that don't require named states or complex blending.
```c++
typedef struct _VTLdata {  
  int aniKPS, FramesCount, AniTime;
  short int* aniData;
} TVTL;
```

### `TCharacterInfo`
Holds the shared resources for a character type (e.g., "T-Rex").
```c++
typedef struct _TCharacterInfo {
  // ...
  TAni Animation[64]; // Array of available animations
  int  Anifx[64];     // Sound mapping: Animation Index -> Sound Index
  // ...
} TCharacterInfo;
```

### `TCharacter`
Represents an active instance of a character.
```c++
typedef struct _TCharacter  {
  // ...
  int Phase;          // Current Animation Index
  int FTime;          // Current Frame Time (ms) within the Phase
  
  // Blending State
  int PrevPhase;      // Previous Animation Index
  int PrevPFTime;     // Previous Frame Time when transition started
  int PPMorphTime;    // Time elapsed since transition started (0-256 ms)
  
  // Procedural State
  float bend;         // Body bending factor (turning)
  float beta, gamma;  // Pitch/Roll banking
  float scale;        // Random size variation
  // ...
} TCharacter;
```

## 2. Animation Loading

Animations are loaded in `Resources.cpp`.
1.  **Header Reading**: Reads `FramesCount` and `aniKPS`.
2.  **Time Calculation**: `AniTime` is derived: `(FramesCount * 1000) / aniKPS`.
3.  **Memory Allocation**: Heap memory is allocated for `aniData`. Size = `VCount * FramesCount * 6` (3 coords * 2 bytes).
4.  **Vertex Loading**: Raw vertex snapshots for every frame are read into `aniData`.
5.  **Sound Mapping**: The `Anifx` array is read, mapping specific animations to sound effects (see Section 6).

## 3. The Animation Pipeline

The animation pipeline updates the vertex positions of a model *in-place* before rendering. This is handled by `CreateChMorphedModel` (for characters) and `CreateMorphedObject` (for objects) in `Characters.cpp`.

The pipeline consists of three stages:
1.  **Vertex Interpolation** (Frame-to-Frame)
2.  **Phase Blending** (Animation-to-Animation)
3.  **Procedural Deformation** (Bending & Scaling)

### 3.1 Vertex Interpolation (Linear)
The system interpolates between the current keyframe and the next keyframe to ensure smooth motion regardless of framerate.

*   **Calculation**:
    *   `CurFrame` (Integer part) determines the start keyframe.
    *   `SplineD` (Fractional part, 0-255) is the interpolation weight `k2`.
    *   `k1 = 1.0 - k2`.
    *   `Vertex = Keyframe[i] * k1 + Keyframe[i+1] * k2`.

### 3.2 Phase Blending (Morphing)
To prevent "popping" when switching states (e.g., Stand -> Run), the system cross-fades between the *previous animation* and the *current animation*.

*   **Constants**: `PMORPHTIME` (256ms) defines the blend duration.
*   **Logic**:
    *   When `Phase` changes, `PrevPhase` captures the old state, and `PPMorphTime` is reset to 0.
    *   `PPMorphTime` increments by `TimeDt` each frame.
    *   **Blend Factor (`pmk1`)**: `PPMorphTime / PMORPHTIME`.
    *   **Formula**:
        ```
        FinalVertex = (CurrentAnimVertex * pmk1) + (PreviousAnimVertex * (1.0 - pmk1))
        ```
    *   Once `PPMorphTime >= PMORPHTIME`, the previous animation is discarded, and only the current one is computed.

### 3.3 Procedural Deformation
After interpolation and blending, the model is procedurally deformed to simulate physical movement like turning or terrain alignment.

*   **Bending**:
    *   Calculated based on the character's turning speed (`rspeed`) and accumulated into `bend`.
    *   Vertices are rotated around the Y-axis based on their Z-position, creating a "curved" spine effect when the dino turns.
*   **Banking (Beta/Gamma)**:
    *   The entire model is rotated to align with the terrain slope (`beta` for pitch, `gamma` for roll).
*   **Scaling**:
    *   All vertices are multiplied by `cptr->scale`, allowing for size variety within the same species.

## 4. Sound Synchronization

Sound effects are synchronized with animation phases using the `Anifx` array in `TCharacterInfo`.

*   **Mapping**: `Anifx[AnimationIndex]` stores the index of the sound to play.
*   **Triggering**: 
    *   Checked in `ActivateCharacterFx` (in `Characters.cpp`).
    *   Triggered when a character enters a new phase (`Phase != PrevPhase`).
    *   Can also be triggered explicitly by AI logic (e.g., footsteps, screams).
*   **Spatial Audio**: Sounds are played using `AddVoice3d` at the character's 3D position.

## 5. AI & State Management

Animation phases are driven by the character's AI state machine (e.g., `AnimateRaptor`, `AnimateTRex`).

*   **FTime Management**: `FTime` (Frame Time) accumulates `TimeDt` (delta time).
*   **Looping**: `FTime %= AniTime` handles seamless looping.
*   **Transitions**: AI logic dictates `Phase` changes (e.g., if `velocity > X` switch to `RUN`).
*   **Morph Optimization**: When switching compatible phases (e.g., Walk -> Run), `FTime` is often scaled to match the *cycle position* of the new animation, ensuring the feet alignment stays consistent during the cross-fade.

```c++
// Example Sync Logic
if (MORPHP)
    if (_Phase<=3 && cptr->Phase<=3) // If both are movement phases
        cptr->FTime = _FTime * NewAniDuration / OldAniDuration + 64;
```

## 6. Rendering Integration

The renderer (Software, D3D, or Glide) does **not** perform vertex transformations. It receives the fully transformed, blended, and deformed vertex array (`mptr->gVertex`) which is updated every game tick by the `AnimateCharacters` loop.

This decouples the animation logic from the rendering backend, allowing the complex software vertex manipulation described above to work identically across all renderers.