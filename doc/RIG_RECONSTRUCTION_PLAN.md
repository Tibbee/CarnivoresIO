# Rig Reconstruction Implementation Plan

> A staged plan for turning the current `.car` owner-centroid autorig into a deterministic, topology-aware, confidence-scored reconstruction workflow, with an optional animation-assisted path.

---

## 1. Purpose and Scope

CarnivoresIO has two different skeletal workflows:

- **Direct skeleton import (`.3df`)**: bone names, heads, and parents are stored in the file. No hierarchy inference is required. Improvements here concern armature construction quality, validation, roll, transforms, and leaf tails.
- **Owner-based reconstruction (`.car`)**: the file stores per-vertex owner IDs but no bone positions, hierarchy, rotations, or rest transforms. The addon must infer a useful Blender rig. Exact recovery from static owners is impossible, so the output must be treated as a scored rig proposal rather than source truth.

The extension currently has no `.3dn` importer. The `.3dn` format itself contains explicit bone records, so any future importer should follow the direct skeleton path rather than the `.car` reconstruction path.

This plan covers:

1. correctness fixes in owner parsing, validation, and mapping;
2. a pure, testable reconstruction core;
3. topology-based group adjacency and joint estimation;
4. stable hierarchy, root, symmetry, tail, and roll inference;
5. non-destructive weight generation;
6. armature lifecycle and transform correctness;
7. preview, diagnostics, and export reconciliation;
8. optional motion-assisted inference and skeletal animation fitting.

It does **not** promise exact recovery of an original skeleton that is absent from the file.

---

## 2. Current System Summary

The current `.car` path in `utils/animation.py::reconstruct_armature()` performs:

1. owner cache or vertex-group retrieval;
2. optional destructive Laplacian smoothing;
3. owner-group centroid calculation;
4. empty-group pruning;
5. distance-threshold cluster detection;
6. semantic side naming and mirror detection;
7. root scoring;
8. a Prim-like all-pairs scored spanning tree;
9. armature creation through `utils/io.py::create_armature()`;
10. Armature modifier assignment and diagnostic metadata storage.

This is useful as a quick geometric autorig. Its main limitations are:

- several owner IDs can be corrupted or conflated before reconstruction;
- centroid proximity is weaker evidence than mesh boundaries and animation motion;
- reconstruction can mutate weights on every run;
- skipped groups and export drift are not fully reconciled;
- generated armature transforms, roll, repeated execution, and failure cleanup need stronger handling;
- imported shape-key animation is not converted to bone animation.

---

## 3. Design Principles

All implementation work should follow these rules.

### 3.1 Preserve source truth

- `carnivores_owner_source` remains the unchanged raw per-vertex owner value read from the CAR file.
- Source attributes must never be rewritten by smoothing, reconstruction, semantic naming, or export preparation.
- Use `-1` internally for unowned/invalid vertices. Do not conflate unowned vertices with compact group zero.
- Store an explicit raw-owner-to-compact-group mapping. Never reconstruct this mapping from vertex-group order.

### 3.2 Separate analysis from Blender construction

- Geometry and hierarchy inference should operate on NumPy arrays and plain Python data structures in a new module.
- `bpy` extraction and armature creation remain thin adapters around the pure core.
- Analysis must not create bones, rename groups, change weights, alter selection, or switch modes.

### 3.3 Make uncertainty visible

- Produce confidence and reason codes for roots, edges, symmetry pairs, joints, skipped groups, and motion fits.
- Preserve manual overrides.
- Do not silently discard nonempty owner groups.

### 3.4 Be deterministic and scale invariant

- The same source mesh and settings must produce the same proposal on repeated runs.
- Uniformly scaling a model must not change its root or parent map.
- Tie-breaking must use stable compact/raw owner IDs rather than set or Blender iteration order.

### 3.5 Prefer evidence in this order

1. explicit source bone data, when available;
2. owner-boundary mesh topology;
3. animation-derived rigid motion and shared-pivot consistency;
4. region proximity and shape;
5. global centroid heuristics only as fallback.

### 3.6 Keep the legacy path available during migration

- Preserve the current centroid/MST algorithm as `LEGACY` until the replacement passes fixture and manual testing.
- New generated armatures must record the algorithm and metadata schema versions.
- Switch the default only after acceptance criteria are met.

---

## 4. Target Architecture

### 4.1 New pure analysis module

Create `utils/rig_reconstruction.py`. It may depend on NumPy and standard-library modules, but not `bpy` or `bmesh`.

Suggested data structures:

```python
@dataclass
class OwnerMapping:
    raw_per_vertex: np.ndarray       # int32, unchanged file values
    compact_per_vertex: np.ndarray   # int32, -1 or 0..G-1
    raw_by_compact: np.ndarray       # int32, shape (G,)
    compact_by_raw: dict[int, int]
    unowned_vertex_indices: np.ndarray

@dataclass
class RigGroup:
    compact_id: int
    raw_owner_id: int
    name: str
    vertex_indices: np.ndarray
    vertex_count: int
    centroid: np.ndarray
    bounds_min: np.ndarray
    bounds_max: np.ndarray
    principal_axes: np.ndarray
    principal_values: np.ndarray

@dataclass
class RigEdgeCandidate:
    group_a: int
    group_b: int
    boundary_edge_count: int
    boundary_joint: np.ndarray | None
    nearest_distance: float
    motion_joint: np.ndarray | None
    motion_residual: float | None
    cost_terms: dict[str, float]
    total_cost: float
    confidence: float
    reason_codes: tuple[str, ...]

@dataclass
class RigProposal:
    groups: list[RigGroup]
    parent_by_group: np.ndarray
    head_by_group: np.ndarray
    tail_by_group: np.ndarray
    roll_reference_by_group: np.ndarray
    root_groups: list[int]
    edge_candidates: list[RigEdgeCandidate]
    accepted_edges: list[tuple[int, int]]
    skipped_groups: list[int]
    warnings: list[str]
    settings: dict
    algorithm_version: int
```

NumPy arrays should be converted to lists only when serializing diagnostics. Do not store the complete proposal in Blender custom properties if it would create large `.blend` files.

### 4.2 Blender adapters

Keep these responsibilities outside the pure module:

- `utils/animation.py`
  - extract owner attributes and source names;
  - extract mesh coordinates/faces/edges and optional shape-key frames;
  - call the analysis core;
  - manage preview and apply workflows.
- `utils/io.py`
  - create/update armatures from an accepted `RigProposal`;
  - create vertex groups from generated weights;
  - assign modifiers and parenting safely;
  - collect exact export owners for reconciliation.
- `operators/animation.py`
  - settings, analyze/preview/apply/clear/reconcile operators;
  - reports and UI.

### 4.3 Versioned metadata

Store compact JSON-compatible metadata on the generated armature:

- `carnivores_rig_algorithm`: `LEGACY`, `TOPOLOGY`, or `MOTION`;
- `carnivores_rig_algorithm_version`;
- `carnivores_rig_metadata_version`;
- source mesh name or stable object identifier;
- source owner-attribute names;
- raw-owner-to-bone-name mapping;
- root raw owner IDs;
- accepted parent map in raw IDs;
- skipped raw IDs and reasons;
- settings and source geometry checksum;
- summary confidence and reconciliation counts.

Use JSON rather than `str(dict)`/`ast.literal_eval`. Keep detailed edge diagnostics in a text datablock or preview object if they are too large for custom properties.

---

## 5. Phase 0 — Characterization and Test Harness

### Goal

Freeze current behavior, expose known failures, and create tests before replacing algorithms.

### Tasks

1. Move pure helper coverage into an automated test layout:
   - `tests/test_owner_mapping.py`
   - `tests/test_rig_geometry.py`
   - `tests/test_rig_hierarchy.py`
   - `tests/test_rig_motion.py` later
2. Add Blender integration scripts:
   - `tests/blender/test_rig_operator.py`
   - `tests/blender/test_rig_roundtrip.py`
3. Generate synthetic fixtures in code; do not require proprietary game assets.
4. Add a manual checklist at `doc/RIG_TEST_CHECKLIST.md` for real CAR/3DF files.
5. Capture current debug reports for representative models before changing defaults.

### Required synthetic cases

- no vertices;
- all owners `-1`/unowned;
- one valid owner group;
- exactly two groups;
- sparse raw IDs such as `1, 4, 9`;
- owner `-1` mixed with valid zero and positive raw IDs;
- duplicate/co-located group centroids;
- symmetric biped and quadruped;
- no midline owner group;
- asymmetric creature;
- snake/long chain;
- main body plus disconnected accessory;
- two disconnected but equally sized components;
- reordered and deleted Blender vertex groups;
- model scales `0.01`, `1.0`, and `100.0`;
- non-identity mesh location, rotation, and scale;
- repeated reconstruction with identical settings.

### Acceptance

- Tests reproduce the known two-group clustering failure before its fix.
- Tests reproduce sparse-ID/local-index PCA mismatch.
- A validation-on CAR test demonstrates whether source owners survive parsing.
- Pure tests can run without launching Blender.

---

## 6. Phase 1 — Owner and Validation Correctness

### 6.1 Fix CAR vertex validation

`parsers/validate.py::validate_car_vertices()` must not call the zero-bone mutation branch of `validate_3df_vertices()`.

Refactor shared validation into:

- coordinate count/finite checks;
- hide-field warnings;
- optional owner-range validation when a real bone count exists.

CAR validation should:

- preserve every raw owner value;
- warn when no non-negative owner exists;
- report negative/unowned counts;
- report sparse/noncontiguous IDs;
- not invent a bone count or clamp owners.

### 6.2 Replace offset normalization with explicit compaction

Refactor `utils/io.py::handle_car_owners()` to return an `OwnerMapping` or equivalent arrays:

```text
raw non-negative IDs sorted deterministically → compact IDs 0..G-1
raw negative IDs (normally -1) → internal -1
```

If research or fixtures demonstrate that zero can be a valid CAR owner in supported files, add an explicit compatibility mode; do not silently guess per model.

Generated names should use raw IDs, for example `CarBone_1`, `CarBone_4`, and `CarBone_9`, while group storage uses compact IDs.

### 6.3 Persist mapping independently of vertex groups

Add object or mesh metadata containing:

- ordered raw IDs;
- compact IDs;
- generated source names;
- owner schema version.

`_build_reconstruction_bone_names()` and `Reset to Imported Owners` must use this metadata first. Existing Blender vertex-group indices are not a reliable owner mapping after users edit groups.

### 6.4 Backward compatibility

For old `.blend` files:

1. If both old owner attributes exist, derive a mapping from `carnivores_owner_source` and preserve the old normalized attribute until the user explicitly upgrades.
2. If only `carnivores_owner_index` exists, treat its nonnegative unique values as already compact and report that raw IDs are unavailable.
3. Never rewrite source attributes merely by opening a file.

### Acceptance

- Validation enabled and disabled produce identical owner arrays.
- Raw owners survive import exactly.
- Sparse raw IDs create exactly the number of unique positive groups, with no empty gaps.
- Raw zero does not share compact group zero.
- Reset-to-imported-owners restores the correct names and assignments after vertex groups are reordered or removed.

---

## 7. Phase 2 — Pure Reconstruction Core and Scale Normalization

**Implementation status:** Core mesh input validation, deterministic edge derivation, Blender-local extraction, characteristic-scale normalization, stable group geometry/PCA, topology-island reporting, and synthetic scale tests are implemented. Both `LEGACY` and the experimental `TOPOLOGY` adapter now consume the canonical owner analysis; Legacy remains the default.

### 7.1 Extract mesh analysis input

Build a Blender adapter that supplies:

- local-space vertex positions;
- triangle indices;
- unique mesh edges derived from polygons when Blender edge data is absent;
- compact owner per vertex;
- owner mapping and names;
- object/import axis metadata.

Validate all array lengths before analysis.

### 7.2 Compute a characteristic scale

Use a robust positive model scale such as:

1. median nonzero nearest-region distance, if available;
2. otherwise owned-vertex bounding-box diagonal;
3. otherwise `1.0`.

Normalize positions and all geometric distances for scoring. Keep output positions in original mesh-local coordinates.

Remove dimensional additions such as a fixed `5.0` from model-space distance scores. Fixed safety lengths should be relative to group/model extent with only a very small numerical epsilon.

### 7.3 Stable group geometry

For each owner group compute:

- centroid and optionally robust median center;
- vertex count;
- AABB and extent;
- SVD/PCA axes and singular-value ratios;
- topology islands within the group;
- confidence in the principal direction.

When PCA eigenvalues are nearly equal, mark direction confidence low and use hierarchy/body-axis fallbacks instead of unstable PCA output.

### 7.4 Preserve legacy mode

Move or wrap the current centroid, mirror, root, cluster, and MST helpers so they can be selected as `LEGACY`. Fix crashes and owner mapping in legacy mode, but avoid silently changing all heuristics while the topology path is being evaluated.

### Acceptance

- Core analysis imports no Blender modules.
- Parent maps and root selection are unchanged by uniform scale.
- All ties resolve by compact/raw owner ID.
- Co-located groups produce warnings and valid output rather than multiple accidental roots or an infinite-score stall.

---

## 8. Phase 3 — Topology-Aware Static Reconstruction

**Implementation status:** The experimental `TOPOLOGY` path builds cross-owner boundary candidates, robust boundary joints, topology components, and explicit `MULTI_ROOT`, `ATTACH_NEAREST`, `SKIP`, and reserved `HOOKS` policies. Topology algorithm v2 introduced anatomy constraints: deterministic bilateral pairing separates lateral components from a proximity-completed central backbone, each lateral component attaches once, and paired components prefer the same central owner. Algorithm v3 additionally orients laterally ambiguous midline leaf controls along signed Blender Y body flow. Algorithm v4 uses source owner order to choose among central candidates with at least two supported topology connections, avoiding tiny but graph-central control groups as roots while retaining scored fallback selection for endpoint-only structures. The pure `RigProposal` supplies explicit heads, tails, and roll references to Blender. Debug metadata records classification and accepted-edge evidence. Manual `dilo2b` testing confirmed seven correct mirror pairs, independent forelimbs, symmetric rear-limb attachment through `CarBone_30`, restoration of the `CarBone_30–35` tail chain, and one low-confidence `CarBone_9–10` attachment. Its original root is known to be `CarBone_30`; automatic selection of `CarBone_3` remains an inference failure. General body-axis inference and transactional lifecycle remain for later work.

### 8.1 Build the owner-region adjacency graph

For every mesh edge `(u, v)`:

- ignore it if either endpoint is unowned;
- if owners differ, increment the boundary count for that owner pair;
- collect the edge midpoint and endpoint positions.

For each owner pair calculate:

- boundary edge count;
- boundary length where available;
- robust boundary midpoint/median;
- spread of boundary samples;
- normalized support relative to group size.

This graph is the primary static evidence for articulation.

### 8.2 Handle disconnected geometry

Use connected components of the owner adjacency graph, augmented only when necessary by region-nearest-distance edges. Do not use standard deviation of all pairwise centroid distances.

For owner regions that are spatially close but have no shared topology:

- find nearest region points in bounded NumPy chunks or with a Blender-side KD-tree adapter;
- create a low-confidence proximity edge only if its normalized distance is below a setting;
- mark the edge reason as `PROXIMITY_FALLBACK`.

Select the main component by owned vertex count or total geometric support, not group count alone.

### 8.3 Disconnected component policy

Add a reconstruction setting:

- `SKIP`: preserve groups but create no controls; report affected vertices;
- `MULTI_ROOT`: include each component as a separate root in one armature;
- `ATTACH_NEAREST`: force a low-confidence connection to the main component;
- `HOOKS`: create hook controls for excluded components.

Keep the current behavior as the compatibility default initially. Consider `MULTI_ROOT` as the long-term fidelity default after testing.

### 8.4 Estimate joint positions from boundaries

For an accepted adjacent pair:

1. use a robust center of cross-owner boundary-edge midpoints;
2. reject extreme samples using median absolute deviation;
3. use nearest-region points when no boundary exists;
4. use the centroid midpoint only as the final fallback.

Record joint source and confidence.

### 8.5 Estimate symmetry and body axes

Create a scored axis model rather than assuming every model is a conventional X-symmetric creature.

Inputs may include:

- import axis/handedness metadata;
- full owned-vertex PCA;
- group-centroid PCA weighted by vertex count;
- mirrored region matching;
- user-selected side axis and inversion.

Return:

- longitudinal/body axis;
- up axis;
- lateral axis;
- symmetry-plane center;
- confidence for each.

Do not assign semantic sides when symmetry confidence is below a threshold.

### 8.6 Separate undirected structure from parent direction

Construct candidate edge costs from normalized, symmetric terms:

- topology support;
- boundary/nearest distance;
- joint confidence;
- cross-symmetry-plane penalty or rejection;
- body-axis consistency;
- region-size compatibility;
- optional motion evidence later.

First select an **undirected** minimum-cost structure inside each component. Then orient accepted edges away from the selected root. Avoid allowing directed parent preference to change which anatomical edge is selected.

A cubic Prim implementation is acceptable at expected bone counts. Optimize only if profiling demonstrates a need.

### 8.7 Root inference

Score root candidates using normalized evidence:

- proximity to symmetry plane;
- topology adjacency degree/support;
- weighted graph centrality;
- owner-region mass;
- position near the central body chain;
- non-synthetic semantic names;
- motion stability later.

Do not use lowest Y/Z as a generic root rule. Lateral mirror candidates may be strongly penalized, but if every candidate is lateral, fall back to the best finite candidate instead of `argmin` over all infinities.

Replace the raw integer UI override with a group selector showing source name and raw owner ID, while preserving `-1` automatic behavior for backward compatibility.

### Acceptance

- Two adjacent owner groups remain one component and produce one edge.
- A left limb cannot connect directly to its mirrored right limb when a supported torso path exists.
- Detached components follow the selected policy and are never silently lost.
- Joint heads are located at owner boundaries in synthetic fixtures.
- Root and hierarchy remain deterministic for symmetric ties and asymmetric models.

---

## 9. Phase 4 — Non-Destructive Weight Generation

**Implementation status:** Topology now rebuilds one-hot deform vertex groups from the canonical owner cache whenever smoothing is enabled, applies the existing Laplacian smoother to that fresh output, and leaves source owner attributes and hierarchy evidence unchanged. This makes repeated Topology smoothing non-cumulative. Legacy retains its compatibility behavior of using smoothed groups for centroid inference. A future pure NumPy weight generator can replace the current Blender/BMesh adapter.

### 9.1 Separate source owners from deform weights

Canonical source owners remain hard assignments. Reconstructed deform weights are generated output.

The reconstruction UI should provide:

- `Source weights`: exact one-hot owner assignment;
- `Smoothed deform weights`: generated from source on every analysis/apply;
- centroid source: canonical owners by default, with an explicit weighted-centroid option.

### 9.2 Replace cumulative smoothing

Implement array-based topology smoothing in the pure module or a dedicated NumPy utility:

1. initialize a `(vertex_count, group_count)` one-hot matrix;
2. derive vertex adjacency from mesh edges;
3. apply a bounded number of Laplacian passes;
4. implement joints-only masking from canonical owner boundaries;
5. remove tiny influences;
6. normalize every owned vertex to a sum of one;
7. enforce a configurable maximum influence count.

Every run starts from the canonical owner cache, so repeated reconstruction is idempotent.

The existing BMesh smoother may remain for explicit user editing, but it must not be the hidden source of reconstruction state.

### 9.3 Import behavior

Do not automatically interpret the CAR import smoothing option as “smooth again during reconstruction.” Either:

- generate smoothed groups once at import while preserving canonical source data; or
- defer reconstruction smoothing entirely to the rig tool.

Label these operations separately in the UI.

### Acceptance

- Running reconstruction twice with the same settings produces identical weights.
- Every owned vertex has normalized generated deform weights.
- Source owner attributes are unchanged.
- Reset restores exact one-hot groups regardless of previous smoothing.

---

## 10. Phase 5 — Armature Construction Quality and Lifecycle

### 10.1 Correct local/original owner mapping

Armature construction must receive explicit compact IDs and raw IDs. Never assume local proposal index `i` equals a source owner value.

PCA vertex selection, names, weights, metadata, and reconciliation must all use the explicit mapping.

### 10.2 Joint-based heads and tails

Use accepted joint positions as follows:

- child head: joint between parent and child;
- root head: robust root-region center or an inferred point opposite its continuation joint;
- chain parent tail: primary continuation-child joint;
- branch parent tail: primary continuation-child joint rather than all-child centroid;
- leaf tail: PCA direction and extent, aligned away from its parent;
- single root/prop tail: owner PCA and group extent rather than a fixed stub.

Select the continuation child using body-axis alignment, centrality, edge confidence, and optional semantic evidence. Other children remain unconnected.

### 10.3 Bone roll

After head/tail placement:

- derive a reference up or lateral vector from the axis model;
- project it onto the plane perpendicular to the bone direction;
- call the appropriate Blender roll-alignment API;
- mirror roll consistently for bilateral partners;
- fall back deterministically when the reference is parallel to the bone.

Add roll information to debug reports.

### 10.4 Name safety

- Only auto-suffix clearly synthetic names by default (`Bone_*`, `CarBone_*`).
- Preserve user/source names unless explicitly requested.
- Resolve ASCII cleaning, 32-byte export limits, duplicate names, and Blender auto-uniquification before creating bones.
- Rename corresponding generated vertex groups from the final resolved mapping, not by best-effort string lookup.
- Record final Blender name and export name separately.

### 10.5 Transform correctness

Create proposal positions in mesh-local space and set the armature object to the mesh’s world transform, or explicitly convert both into a documented common space.

Test:

- translated mesh;
- rotated mesh;
- unapplied scale;
- mesh already parented to another object.

Parenting and modifier assignment must preserve mesh world appearance.

### 10.6 Transactional construction

Wrap mode and selection changes in `try/finally`:

- preserve active object, selection, and mode;
- clean up a partially created armature/modifier on failure;
- restore context;
- return a structured success/failure result.

The operator must cancel when reconstruction returns no armature.

### 10.7 Existing generated rig policy

Add an apply policy:

- `UPDATE_GENERATED`: update the armature previously generated for this mesh;
- `REPLACE_GENERATED`: remove the old generated armature/modifier safely;
- `CREATE_NEW`: keep the old rig and create a new one;
- `CANCEL_IF_RIGGED`.

Do not overwrite unrelated user armatures automatically.

### 10.8 Skipped groups

Do not automatically delete nonempty skipped groups. Tag and report them. Offer a separate cleanup action after reconciliation.

### Acceptance

- Sparse/pruned owner IDs use the correct vertices for PCA and weights.
- Single-bone props receive a geometry-scaled useful bone.
- Branching torso/pelvis bones follow a continuation child.
- Mirrored limbs have consistent local axes.
- Non-identity object transforms produce aligned rigs.
- Failed construction leaves no orphan armature and restores Blender context.
- Repeated apply follows the selected lifecycle policy without accumulating modifiers.

---

## 11. Phase 6 — Preview, Confirmation, and Diagnostics

### 11.1 Split Analyze from Apply

Add operators:

- `Analyze Rig Proposal`;
- `Apply Rig Proposal`;
- `Clear Rig Preview`;
- `Validate Rig Round Trip`;
- retain `Reconstruct Rig from Owners` as a compatibility shortcut that analyzes and applies.

### 11.2 Preview representation

Create a temporary, clearly named collection containing a lightweight preview mesh or objects for:

- group centers;
- estimated joints;
- accepted edges;
- rejected high-value edges;
- disconnected components;
- roots and skipped groups.

Suggested colors:

- green: accepted high confidence;
- yellow: accepted fallback/low confidence;
- red: rejected cross-body or forced edge;
- magenta: skipped/unowned group;
- cyan: manual override.

Preview objects must be tagged and removable without affecting user objects.

### 11.3 Editable proposal

Minimum first version:

- root override;
- component policy;
- semantic naming toggle;
- side axis/inversion;
- force or reject selected edge through a compact list UI.

A later version may permit dragging joint controls before armature creation.

### 11.4 Debug report

Expand the text report to include:

- owner schema and raw-to-compact mapping;
- unowned vertex count;
- component sizes by vertices and groups;
- root candidate scores;
- accepted edges and individual score terms;
- joint source/confidence;
- symmetry pairs/confidence;
- final names and source IDs;
- skipped groups and affected vertices;
- smoothing source/settings;
- armature transform and roll;
- algorithm/settings/checksum;
- reconciliation results.

Avoid Unicode-only status characters if console/text portability becomes an issue.

### Acceptance

- Analyze does not mutate the mesh, groups, parenting, modifiers, or selection.
- Preview cleanup removes only generated preview data.
- A user can override a wrong root and edge before armature creation.
- Every low-confidence or forced decision is visible in the report.

---

## 12. Phase 7 — True Round-Trip Reconciliation

The existing dominant-group divergence report is retained as one diagnostic, but it is not sufficient.

### 12.1 Dry-run export mapping

Refactor `utils/io.py::collect_bones_and_owners()` so owner collection can run without writing a file and returns structured diagnostics:

- final bone order;
- final export names;
- per-vertex export owner;
- unmatched groups;
- unmatched vertices;
- fuzzy-name matches;
- fallback-to-root assignments.

Avoid fuzzy matching for generated rigs when explicit source-owner metadata is available.

### 12.2 Comparisons

Compare separately:

1. canonical compact source owner vs generated dominant deform bone;
2. canonical raw owner vs dry-run CAR export owner mapping;
3. all source groups vs generated armature bones;
4. skipped groups vs affected vertices;
5. current groups vs stored generated-weight checksum.

Do not omit vertices with no groups from mismatch counts.

### 12.3 Result levels

- **Pass**: one-to-one group mapping and no unexpected owner drift;
- **Expected drift**: smoothing changes dominant owner and user opted into it;
- **Warning**: skipped groups, unowned vertices, renamed collisions, or forced attachment;
- **Error**: missing deform bone, invalid hierarchy, unmatched owned vertices, or export mapping failure.

### Acceptance

- Exact one-hot reconstruction round-trips owners with zero unexpected drift.
- Smoothing drift is quantified separately from mapping errors.
- Skipped groups cannot silently export as root without a warning containing vertex counts.

---

## 13. Phase 8 — Motion-Assisted Rig Inference

This phase uses CAR shape-key frames as additional evidence. It should be optional and fall back to topology mode when animation evidence is insufficient.

### 13.1 Animation extraction

Read frame positions from imported key blocks matching the existing naming convention, grouped by source animation. Do not duplicate all animation data permanently.

Allow sampling controls:

- all frames for small files;
- uniformly sampled frames;
- motion-diverse frames selected by displacement;
- selected actions only.

### 13.2 Per-group rigid transform fitting

For every group and sampled frame:

1. take canonical rest vertices and animated vertices;
2. fit a best rigid transform using Kabsch/SVD;
3. reject reflection solutions;
4. record rotation, translation, RMS error, maximum error, rank, and vertex count.

Groups with fewer than three useful non-collinear points require constrained/fallback fitting and lower confidence.

### 13.3 Shared-pivot estimation

For candidate groups A and B, solve across frames for a rest-space point `j` satisfying approximately:

```text
R_A,f @ j + t_A,f == R_B,f @ j + t_B,f
```

Use robust least squares and a boundary-joint prior. Record:

- fitted pivot;
- residual;
- rank/conditioning;
- number and diversity of supporting frames.

Low shared-pivot residual strengthens an edge. Poor residual weakens it but does not automatically reject topology evidence because CAR owner regions may deform non-rigidly.

### 13.4 Motion-aware root and hierarchy

Add normalized motion terms:

- shared-pivot residual;
- group rigidity;
- relative-motion diversity;
- stability across animations;
- likely central/root motion.

Topology remains the primary candidate graph. Motion should score plausible edges, not reopen arbitrary all-pairs links without strong evidence.

### 13.5 Motion confidence report

Report:

- groups too deformable for skeletal approximation;
- animations with insufficient motion;
- edges supported or contradicted by motion;
- expected minimum reconstruction error.

### Acceptance

Synthetic two- and three-segment rigid animations must recover:

- the correct adjacency;
- pivots within a scale-relative tolerance;
- group transforms with low residual;
- the same hierarchy under frame resampling.

Non-rigid synthetic groups must be identified as low confidence rather than producing unstable pivots silently.

---

## 14. Phase 9 — Optional Skeletal Animation Conversion

This is a separate feature from static rig reconstruction and should not block the topology rig release.

### 14.1 Do not use centroid translation alone

Per-frame group centroids may assist diagnostics, but they do not recover orientation or hierarchical pose transforms. The conversion must use fitted rigid transforms.

### 14.2 Convert fitted transforms into pose space

For each frame:

1. calculate fitted world/armature-space group transforms;
2. convert child transforms relative to fitted parent transforms;
3. convert from rest matrices into Blender pose-bone transforms;
4. keyframe location/rotation using quaternions where appropriate;
5. maintain stable quaternion signs across frames.

### 14.3 Avoid double deformation

Provide explicit modes:

- keep original shape-key animation only;
- preview fitted skeletal animation on a duplicate mesh;
- convert to skeletal actions and mute/disable the corresponding shape-key action;
- keep both for comparison, but never enable both unknowingly.

### 14.4 Residual validation

Evaluate the armature-deformed mesh against original CAR frame positions and report:

- RMS and maximum error per frame;
- error per owner group;
- worst animation/frame;
- percentage of vertices exceeding a scale-relative tolerance.

Keep shape keys as source truth unless the user explicitly removes them.

### Acceptance

- A synthetic rigid chain converts with near-zero residual.
- Shape keys and armature actions are not simultaneously applied by default.
- Non-rigid source animation produces an explicit approximation warning and measured residual.

---

## 15. Direct `.3df` Armature Improvements

These improvements share construction code but do not use hierarchy reconstruction.

1. Preserve validated source heads and parent indices exactly.
2. Continue cycle detection, but report every repaired cycle and affected bone.
3. Use source hierarchy for continuation-child selection.
4. Use owner geometry for leaf and single-bone tails when available.
5. Calculate consistent roll using the same axis/roll system.
6. resolve duplicate/invalid names before bone creation and preserve export names separately;
7. apply the same transform, transaction, lifecycle, and context-restoration rules;
8. add direct-import/export reconciliation for names, positions, parents, and owners.

Do not route explicit 3DF hierarchy through topology or MST inference.

---

## 16. UI and Property Plan

Suggested object properties:

- `carnivores_rig_algorithm`: `LEGACY`, `TOPOLOGY`, `MOTION`;
- `carnivores_rig_component_policy`;
- `carnivores_rig_apply_policy`;
- `carnivores_rig_root_owner_raw` or a group selector;
- `carnivores_rig_side_axis` and `carnivores_rig_invert_sides`;
- `carnivores_rig_semantic_naming`;
- `carnivores_rig_smooth_weights`;
- smoothing iterations/factor/joints-only/max-influences;
- `carnivores_rig_use_weighted_centroids`;
- motion sampling and selected-action controls;
- preview confidence threshold.

Retain existing properties during migration and map them to the new settings. Remove them only in a versioned compatibility change.

Panel order:

1. source owner status and mapping health;
2. algorithm and component policy;
3. smoothing settings;
4. symmetry/root overrides;
5. Analyze / Apply / Clear Preview;
6. round-trip validation;
7. debug report;
8. advanced motion conversion controls, collapsed by default.

---

## 17. File-by-File Work Plan

### New files

- `utils/rig_reconstruction.py` — pure data model and static/motion inference;
- `tests/test_owner_mapping.py`;
- `tests/test_rig_geometry.py`;
- `tests/test_rig_hierarchy.py`;
- `tests/test_rig_motion.py`;
- `tests/blender/test_rig_operator.py`;
- `tests/blender/test_rig_roundtrip.py`;
- `doc/RIG_TEST_CHECKLIST.md`.

### Existing files

- `parsers/validate.py`
  - split shared vertex validation;
  - preserve CAR owners.
- `parsers/parse_car.py`
  - return/persist explicit owner mapping and warnings.
- `operators/io.py`
  - write raw and compact owner attributes plus mapping metadata;
  - stop duplicate import/reconstruction smoothing.
- `utils/io.py`
  - refactor `handle_car_owners()`;
  - add deterministic generated-weight writer;
  - refactor armature construction, roll, transforms, lifecycle;
  - refactor export owner collection into structured dry-run data.
- `utils/animation.py`
  - reduce current function to extraction/orchestration;
  - add analyze/apply/preview helpers;
  - extract shape-key frames for motion analysis;
  - preserve a callable legacy algorithm.
- `operators/animation.py`
  - add analyze/apply/preview/reconcile operators and reports;
  - check structured failures;
  - expose settings and group selectors.
- `operators/__init__.py`
  - register new operators.
- `__init__.py`
  - register/unregister new properties safely.
- `parsers/export_car.py`, `parsers/export_3df.py`, `parsers/export_3dn.py`
  - consume structured owner/bone collection diagnostics where relevant.
- `doc/SYSTEMS.md`
  - document implemented behavior, not planned behavior.
- `doc/DEVELOPMENT.md`
  - track milestone status and link to this plan.
- `doc/PERFORMANCE_ANALYSIS.md`
  - correct MST complexity description and add topology/motion costs after measurement.

All implementation logging must use `utils.logger`; do not add `print()` calls. Use `@timed` around analysis stages that are meaningful during manual profiling.

---

## 18. Testing Strategy

### 18.1 Pure unit tests

Test:

- raw-to-compact owner mapping;
- scale normalization;
- group geometry/PCA confidence;
- boundary graph creation;
- component policies;
- deterministic symmetry pairing;
- root scoring;
- undirected tree selection and orientation;
- boundary joint estimates;
- roll-reference calculations;
- Kabsch and pivot solving.

### 18.2 Blender integration tests

Test:

- attribute creation and migration;
- generated vertex groups and normalization;
- armature alignment under object transforms;
- modifier/parent lifecycle;
- mode and selection restoration;
- repeated apply policies;
- name collisions;
- dry-run export owner mapping;
- shape-key extraction and double-deformation prevention.

### 18.3 Manual real-asset matrix

For each available legally testable CAR model, record:

- owner count and raw ID range;
- components and skipped groups;
- selected root;
- hierarchy screenshot;
- pose test at major joints;
- symmetry/roll behavior;
- owner export drift;
- static and motion confidence;
- reconstruction duration.

Include asymmetric, quadruped, long-chain, flying, aquatic, and prop-like models where available.

### 18.4 Regression rules

A change cannot become the default if it:

- destroys or rewrites source owner data;
- changes output under uniform scale;
- changes output between identical repeated runs;
- silently drops nonempty groups;
- creates unmatched owned vertices without reporting them;
- leaves Blender in Edit Mode or creates orphan data on failure.

---

## 19. Performance and Memory Budgets

Static reconstruction is user-triggered and bone counts are small. Correctness takes priority over replacing the current Prim scan with a heap.

Targets:

- static analysis: comfortably below 250 ms for engine-limit meshes under normal settings;
- preview creation and armature apply: below 500 ms where Blender API operations permit;
- motion analysis: progress reporting and cancellation for long animation sets;
- no permanent duplicate copy of every animation frame beyond existing shape keys;
- nearest-region calculations must be chunked to bound temporary memory;
- detailed timing collected before optimization.

Do not add SciPy as a dependency. Use NumPy, Blender APIs, and standard-library code available in the extension environment.

---

## 20. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Static owners do not uniquely determine a skeleton | Call output a proposal, expose confidence and overrides |
| Mesh seams prevent owner-boundary adjacency | Add low-confidence nearest-region fallback |
| One owner appears in multiple disconnected mesh islands | Report islands; optionally split only with explicit user approval |
| Owner groups deform non-rigidly in CAR animation | Measure rigid-fit residual and retain shape keys as truth |
| Symmetry axis is ambiguous | Confidence threshold and manual axis/inversion controls |
| Sparse or malformed owner IDs | Explicit raw-to-compact mapping and validation warnings |
| Existing `.blend` files use old attributes/properties | Lazy, non-destructive migration and legacy algorithm mode |
| Blender operator failure leaves bad context/data | Transactional creation with cleanup and `try/finally` |
| New naming breaks export mappings | Explicit owner-to-final-name metadata; avoid fuzzy matching for generated rigs |
| Motion conversion double-deforms mesh | Explicit mutually exclusive preview/application modes |
| Heuristics become overfitted to one creature type | Synthetic morphology fixtures, normalized score terms, real-asset matrix |

---

## 21. Delivery Milestones

### Milestone A — Correct source data

Phases 0–1 complete.

Deliverables:

- tests;
- fixed CAR validation;
- explicit compact owner map;
- reliable reset behavior.

This should be released as a correctness patch before changing reconstruction defaults.

### Milestone B — Deterministic static analyzer

Phases 2–3 complete behind `TOPOLOGY` opt-in.

Deliverables:

- pure analysis module;
- topology graph;
- boundary joints;
- normalized hierarchy/root/symmetry;
- component policies.

### Milestone C — Production-quality armature

Phases 4–5 complete.

Deliverables:

- non-destructive smoothing;
- joint-based heads/tails;
- roll;
- transforms and lifecycle;
- safe naming.

After acceptance, make `TOPOLOGY` the default while retaining `LEGACY` for one compatibility cycle.

### Milestone D — Inspectable workflow

Phases 6–7 complete.

Deliverables:

- proposal preview and overrides;
- complete diagnostics;
- dry-run export reconciliation.

### Milestone E — Motion-assisted reconstruction

Phase 8 complete as optional `MOTION` mode.

Deliverables:

- rigid-fit and pivot evidence;
- confidence/residual report;
- topology fallback.

### Milestone F — Skeletal animation conversion

Phase 9 complete as an explicitly experimental feature until residual results are reliable across the real-asset matrix.

---

## 22. Definition of Done

The reconstruction redesign is complete when:

1. CAR owners survive validation and import exactly.
2. Raw, compact, vertex-group, armature-bone, and export IDs have explicit mappings.
3. Analysis is deterministic, scale invariant, and independently testable.
4. Topology boundaries, not all-pairs centroid distance, provide primary static connectivity and joints.
5. Every nonempty group is represented, deliberately skipped, or assigned another explicit policy with affected vertex counts.
6. Repeated reconstruction does not cumulatively mutate weights or accumulate generated rigs.
7. Generated bones have useful heads, tails, roll, names, and object-space alignment.
8. Analyze/preview does not mutate source data.
9. Export reconciliation reports exact and expected owner drift separately.
10. Motion mode reports rigid-fit and joint confidence rather than silently forcing poor results.
11. Skeletal animation conversion, if enabled, is measured against source shape keys and cannot double-deform by default.
12. `SYSTEMS.md`, the manual checklist, and the development roadmap match actual shipped behavior.
