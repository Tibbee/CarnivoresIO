# CarnivoresIO Core Systems

The addon features several sophisticated algorithms that bridge the gap between Blender's modern animation system and the legacy vertex-animation engine of Carnivores.

### Rigging & Animation
- **[Skeleton Reconstruction](systems/skeleton_reconstruction.md)**: Details the custom MST-based (Minimum Spanning Tree) algorithm that infers a bone hierarchy from vertex-group metadata in `.car` files.
- **[Animation Analysis](systems/animation_analysis.md)**: A technical deep-dive into how vertex animations are parsed, decoded, and mapped to Blender shape keys.

### Audio & Interactivity
- **[NLA Sound System](systems/nla_sound_system.md)**: Explains the implementation of synchronized sound playback during animation scrubbing and playback in the Blender viewport.
