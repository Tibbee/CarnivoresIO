# Skeleton Reconstruction System (.car Models)

## Context
Carnivores `.car` files use **Vertex Animation** (Shape Keys). While they assign every vertex to a "Bone Owner" (index), they do **not** store bone positions, rotations, or a hierarchy (parent-child relationships). 

This system reconstructs a functional Blender Armature from that metadata.

## The Algorithmic Solution

### 1. Weighted Centroid Detection
The "Head" of each bone is determined by calculating the **Weighted Center of Mass** of all vertices assigned to that bone index.
*   **Logic:** $\text{Centroid} = \frac{\sum (\text{Vertex Position} \times \text{Weight})}{\sum \text{Weight}}$
*   **Implementation:** Optimized via `numpy` in `utils/animation.py` (`calculate_vertex_group_centroids`) to handle high-poly meshes instantly.

### 2. Hierarchy Inference (MST)
Since the file doesn't store parents, the system "guesses" the skeleton structure using proximity.
*   **Algorithm:** A variation of **Prim's Algorithm** for finding a **Minimum Spanning Tree (MST)**.
*   **Logic:** 
    1.  Start with a Root bone (center-most).
    2.  Iteratively connect the nearest unconnected centroid to the existing tree.
*   **Result:** This correctly handles branching logic (e.g., two legs branching from one pelvis) which simple "chain" scripts fail to do.

### 3. Professional Bone Orientation
To make the armature "Blender-native" and easy to use:
*   **Heads:** Placed exactly at the group centroid.
*   **Tails (Parents):** Pointed directly at the head of their first child.
*   **Tails (Leaves):** Terminal bones (fingertips, tail tips) point away from their parent bone to provide a logical handle for rotation.
*   **Connectivity:** `use_connect` is enabled for single-child chains to create a clean visual skeleton.

## User Workflow
1.  **Import:** Use the standard `.car` importer.
2.  **Locate Tools:** Open the `Carnivores` tab in the N-Panel (Sidebar) under `Carnivores Animation`.
3.  **Execute:** Click **"Reconstruct Rig from Owners"**.
    *   The mesh is automatically parented to the new skeleton.
    *   An **Armature Modifier** is added.
    *   Because bone names match vertex group names, the model is immediately poseable.

## Implementation Files
*   `utils/animation.py`: Core logic for Centroids, MST Inference, and Armature creation.
*   `operators/animation.py`: UI Panel integration and the `CARNIVORES_OT_reconstruct_armature` operator.
*   `operators/__init__.py`: Global registration of the rigging operator.

## Future Enhancements
*   **Handedness Sync:** Ensure the reconstruction respects the "Flip Handedness" import toggle.
*   **IK Generation:** Add a secondary operator to automatically generate Inverse Kinematics constraints for reconstructed legs.
*   **Template Matching:** Provide "Dinosaur" or "Biped" presets to override the MST inference with known accurate hierarchies.
