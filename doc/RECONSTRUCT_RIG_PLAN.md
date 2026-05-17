# Reconstruct Rig from Owners — Implementation Plan

## Goal
Improve the `.car` rig reconstruction pipeline so it is:
- faithful to imported owner data,
- more stable on symmetric creatures,
- deterministic and debuggable,
- safe to export back to Carnivores formats.

## Current Baseline
- Import `.car`
- Convert file owners to vertex groups
- Run reconstruction from vertex groups
- Infer hierarchy with a symmetry-aware MST
- Build armature and parent mesh

## What We Are Tracking

### Phase 1 — Data Fidelity
- [x] Preserve imported owner indices on the mesh
- [x] Allow reconstruction to use the preserved owner map
- [ ] Add a debug view for source data vs. reconstructed data
- [ ] Persist reconstruction metadata for round-trip/export diagnostics

### Phase 2 — Hierarchy Quality
- [x] Replace hardcoded root choice with a scored root selector
- [x] Use mesh-centered symmetry scoring instead of fixed X=0 only
- [ ] Add edge scoring beyond distance + symmetry penalty
- [ ] Add optional manual root override in the UI
- [ ] Add a preview/confirm step before armature creation

### Phase 3 — Bone Orientation
- [ ] Improve leaf tail placement using local geometry
- [ ] Derive bone roll from group direction / PCA
- [ ] Add left/right naming hints when obvious
- [ ] Handle disconnected clusters explicitly

### Phase 4 — UX / Debugging
- [ ] Show the selected root, centroid set, and parent map in the rig debug report
- [ ] Highlight empty or degenerate groups
- [ ] Warn when vertex groups were edited away from the imported owner data
- [ ] Add a one-click “reset to imported owners” recovery path
- [x] Expose pre-reconstruct smoothing controls in the Rigging Utilities panel

## Current Implementation Notes
- `.car` import now stores a normalized point-domain owner cache for reconstruction, plus a source-owner cache for debugging.
- Reconstruction can read the normalized owner cache directly, even if vertex groups are missing or edited.
- Root selection now prefers known names like `floor`, otherwise uses geometric centrality.
- MST symmetry checks now use the model’s own X-center instead of assuming the object is centered at X=0.

## File Touch Points
- `parsers/parse_car.py` — preserve owner data during import
- `operators/io.py` — write the preserved owner caches into mesh attributes
- `utils/animation.py` — reconstruction helpers and hierarchy scoring
- `operators/animation.py` — operator polling / UI gating
- `doc/SYSTEMS.md` — user-facing system description

## Acceptance Criteria
- Reconstruction works from imported `.car` data without relying on edited weights.
- Single-bone / sparse-owner models still reconstruct sensibly.
- Left/right limb pairing is less likely to cross over.
- The resulting armature is deterministic across repeated runs.

## Progress Log
- 2026-05-17: Plan created.
- 2026-05-17: Owner preservation and root selection improvements started.

## Next Implementation Step
1. Finish UI/debug visibility for the reconstruction source.
2. Add richer edge scoring and a preview/override flow.
3. Improve bone roll and leaf tail direction.
4. Validate against several `.car` creatures with asymmetric and symmetric rigs.
