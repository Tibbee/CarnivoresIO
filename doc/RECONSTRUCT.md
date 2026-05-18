# Reconstruct Rig from Owners — Consolidated Status & Roadmap

## Goal

Improve the `.car` rig reconstruction pipeline so it is:
- faithful to imported owner data,
- more stable on symmetric creatures,
- deterministic and debuggable,
- safe to export back to Carnivores formats.

---

## Implementation Status

### Phase 1 — Data Fidelity & Stability
- [x] Preserve imported owner indices on the mesh (`carnivores_owner_index`)
- [x] Allow reconstruction to use the preserved owner map (bypass edited VGs)
- [x] Expose pre-reconstruct smoothing controls in the Rigging Utilities panel
- [x] **Degeneracy pruning** — prevent empty groups from falling back to `mesh_mean` and polluting the MST
- [x] **Disconnected cluster detection** — spatially isolate groups (tongue, jaw flaps) before hierarchy inference
- [x] Persist reconstruction metadata for round-trip/export diagnostics

### Phase 2 — Hierarchy Quality & User Control
- [x] Replace hardcoded root choice with a scored root selector
- [x] Use mesh-centered symmetry scoring instead of assuming X=0
- [x] Add edge scoring beyond distance + symmetry penalty (centrality + body-axis alignment)
- [x] Add optional manual root override in the UI
- [ ] Add a preview/confirm step (centroids + MST edges as temp mesh) before armature creation

### Phase 3 — Bone Orientation & Semantic Naming
- [x] Improve leaf tail placement using local group geometry / PCA
- [x] Derive bone roll from group direction / PCA (store on bone custom props)
- [x] Add left/right naming hints when obvious
- [ ] Handle disconnected clusters explicitly (hook or skip instead of bone chain)

### Phase 4 — UX / Debugging
- [x] Show selected root, centroid set, and parent map in the rig debug report
- [x] Highlight empty or degenerate groups
- [x] Warn when vertex groups were edited away from the imported owner data
- [x] Add a one-click "reset to imported owners" recovery path
- [ ] Round-trip reconciliation pass — validate that export reconstructs the same owner map

### Phase 5 — Hybrid Deformation (Post-Plan Follow-Up)
- [ ] Investigate hook/hybrid reconstruction for very local owner regions (tongue, fin, tail tip)
- [ ] Define heuristics for choosing hooks vs bones and where that mode lives in the UI
- [ ] Compare hook-based deformation against reconstructed armatures on the same `.car` samples

---

## Remaining Roadmap

**Rule:** *Stability first, then determinism, then quality, then new features.*

### Next: Preview / Confirm Step
Generate a temporary preview mesh showing centroids (points) and MST edges (lines) before creating the armature.

**Implementation sketch:**
```
reconstruct_armature(preview=True)
  → build preview mesh overlay (points at centroids, colored by depth from root;
     lines for MST edges, green = accepted, red = cross-body rejected)
  → user inspects in viewport
  → Confirm = delete preview mesh, run full armature creation
  → Cancel = delete preview mesh, abort
```

### Next: Handle Disconnected Clusters (Hook or Skip)
The current BFS cluster detection simply *excludes* disconnected groups from the main bone tree. A configurable policy is needed:

| Mode | Behavior |
|------|----------|
| Skip (default) | Exclude isolated groups from armature |
| Hooks | Create hook empties instead of bones for isolated groups |
| Force bones | Include all groups even if disconnected (warn user) |

### Next: Round-Trip Reconciliation
After creating the armature, validate that the reconstructed bone-to-vertex mapping matches the original owner map. Report drift in a Text datablock (extending the existing debug report).

```
def _validate_reconstruction(obj, arm_obj, original_owner_indices):
    # Re-query dominant group per vertex
    # Compare bone heads vs. centroids
    # Report mismatch vertices as drift
```

### Next: Hook / Bone Decision Matrix
Full heuristics for choosing deformation type per group:

| Condition | Suggested Type |
|-----------|---------------|
| Group vertex count < 5% of total | Hook or skip |
| Group is spatially isolated (> 2× body stddev from main cluster) | Hook |
| Group is a terminal in a single-bone chain | Bone |
| Group has no neighbors within 3× nearest-neighbor distance | Hook |

Gated by a panel option: Auto / Bones Only / Hooks Only.

---

## Investigation Notes (Why These Changes Are Needed)

### Degeneracy Pruning (Done)
`calculate_vertex_group_centroids` previously returned `mesh_mean` for empty groups, creating "ghost bones" at mesh center that became spurious hubs in the MST. Fixed: return `None` for empty groups and filter them out before MST.

### Disconnected Clusters (Done)
The MST was forced to connect all nodes, dragging disconnected groups (tongue, jaw flaps) into the nearest spine bone. Fixed: BFS spatial clustering with adaptive `2× std(pairwise_distance)` threshold. Only the largest cluster is kept for the bone tree.

### Bone Roll (Partial)
`create_armature` still doesn't set `bone.roll` (stays 0.0). This is hidden for `.car` export (vertex-animated, not skinned), but blocks skinned `.3df` export and makes manual posing harder. PCA-derived roll from owner-group geometry should be stored as a custom bone property.

### Leaf Tail Placement (Done)
Terminal bones now use SVD-based PCA of the owner-group vertices to derive tail direction, instead of only the parent-to-child vector. Falls back to parent direction if the group has < 2 vertices.

### Left / Right Semantic Naming (Done)
Post-MST pass appends `_L` / `_R` based on X-position relative to mesh center (adaptive margin: 3% of X-width). Vertex groups are renamed in sync to preserve skinning.

---

## Implementation Context (Preserved)

- `.car` import stores a normalized point-domain owner cache for reconstruction, plus a source-owner cache for debugging (`carnivores_owner_index`, `carnivores_owner_source` mesh attributes).
- Reconstruction can read the normalized owner cache directly, even if vertex groups are missing or edited.
- If pre-reconstruct smoothing is enabled, reconstruction uses the smoothed vertex groups as the source of truth.
- Root selection prefers known names like `floor`, then scored geometric centrality with mirror-partner blacklist and X-offset penalty.
- MST uses richer edge scoring (distance, symmetry, centrality, body-axis alignment, weight bias) instead of plain proximity.
- MST symmetry checks use the model's own X-center instead of assuming X=0.
- Reconstruction metadata (root index, parent map, skipped groups, cluster count) is stored on armature custom properties.

## File Touch Points

- `parsers/parse_car.py` — preserve owner data during import
- `operators/io.py` — write the preserved owner caches into mesh attributes
- `utils/animation.py` — reconstruction helpers and hierarchy scoring
- `utils/io.py` — armature creation, PCA leaf tails, Laplacian smoothing
- `operators/animation.py` — operator polling / UI gating
- `doc/SYSTEMS.md` — full algorithm documentation

## Acceptance Criteria

- Reconstruction works from imported `.car` data without relying on edited weights.
- Single-bone / sparse-owner models still reconstruct sensibly.
- Left/right limb pairing is less likely to cross over.
- The resulting armature is deterministic across repeated runs.
