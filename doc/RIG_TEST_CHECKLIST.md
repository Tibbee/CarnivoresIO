# Rig Reconstruction Test Checklist

Use duplicate `.blend` files or disposable imports while the **Topology** algorithm is experimental. Keep **Legacy** available for side-by-side comparison.

## Preparation

1. Import a `.car` model with **Compatibility Checks** enabled.
2. Select the imported mesh and open the dedicated **Carnivores Rig** panel.
3. Confirm **Reset to Imported Owners** restores `CarBone_<raw owner>` groups.
4. Save the file before applying either reconstruction algorithm.

## Topology Proposal Workflow

1. Set **Reconstruction Algorithm** to **Topology (Experimental)**. Keep **Legacy** available for comparison.
2. Set **Disconnected Components** to **Multiple Roots** and choose **Automatic** in the searchable Root selector.
3. Confirm **Side Axis** is **X** and **Invert Side** is disabled for the normal Carnivores orientation. Test **Y**, **Z**, and inversion on a model whose bilateral direction is known.
4. Run **Analyze**. Confirm that no armature, modifier, parenting, vertex-group rename, selection, or mode change occurs.
5. Confirm the tagged preview shows owner centers, roots, accepted edges, low-confidence edges, rejected/forced edges, and skipped groups without changing unrelated scene objects.
6. Inspect **Generate Rig Report** / **Validate** output and record the checksum, algorithm version, mirror pairs, accepted-edge reasons, roots, hierarchy, skipped groups, and confidence values.
7. Change selected edge rows between **Auto**, **Force**, and **Reject**, or change root/component/side/naming/weight settings, then run **Analyze** again. Confirm the proposal settings and preview reflect those decisions and forced cycles are reported rather than applied.
8. Run **Apply**. Confirm an armature is created without errors, the preview is cleared, and **Validate** reports a valid proposal/armature structure.
9. Edit a mesh vertex or owner cache after Analyze and run **Apply**. Confirm the stale proposal is rejected before any armature or modifier change.
10. Run **Clear** and confirm only Carnivores proposal metadata and tagged preview data are removed; unrelated collections and objects remain.

The existing **Reconstruct Rig** button remains the compatibility shortcut. Use it for Legacy comparisons and for direct Topology reconstruction when a preview is not required.

## `dilo2b` Reference Regression

Topology v4 with **Attach Nearest**, automatic root (`-1`), and smoothing disabled should report:

- 21 central groups and 7 mirror pairs: `15↔18`, `16↔19`, `17↔20`, `22↔26`, `23↔27`, `24↔28`, `25↔29`;
- `CarBone_18 → 19 → 20` without `CarBone_21` below the forelimb;
- `CarBone_21` attached directly to `CarBone_3`;
- rear chains `30 → 22 → 23 → 24 → 25` and `30 → 26 → 27 → 28 → 29`;
- central tail chain `1 → 30 → 31 → 32 → 33 → 34 → 35`;
- only `CarBone_9–10` marked as `PROXIMITY_FALLBACK`;
- all 35 groups represented and no owner-cache divergence.

Confirmed pose checks:

1. rotating `CarBone_18` moves only its forelimb;
2. rotating `CarBone_30` moves the rear limbs and tail but not the forward torso.

Earlier scoring selected `CarBone_3`; Topology v4 should automatically select source-ordered backbone candidate `CarBone_1`. `CarBone_30` (override compact index `29`) remains a useful pelvis-root comparison. Recheck both pose isolation tests after the automatic-root change.

## Component Policies

Re-import or remove the generated rig before each run.

- **Multiple Roots:** every nonempty topology component should remain in the armature.
- **Attach Nearest:** detached components should become one hierarchy through low-confidence proximity edges.
- **Skip Detached:** only the component with the most owned vertices should be rigged; skipped raw IDs must appear in diagnostics.
- **Reserve for Hooks:** currently behaves like Skip Detached and reports that hook creation is reserved for a later adapter.

## Regression Matrix

Check at least one model for each applicable case:

- one owner group;
- exactly two adjacent groups;
- sparse owner IDs including owner `0`;
- symmetric biped or quadruped;
- asymmetric creature;
- long chain or tail;
- disconnected accessory;
- one owner split across disconnected mesh islands;
- translated, rotated, or unapplied-scale mesh;
- repeated reconstruction with identical settings.

## Phase 7 Round-Trip Reconciliation

1. Run **Validate Rig Proposal** after applying a generated rig and record the explicit result level: `PASS`, `EXPECTED_DRIFT`, `WARNING`, or `ERROR`.
2. Confirm the report separates canonical compact-owner versus dominant deform drift from canonical raw-owner versus dry-run CAR export drift.
3. Confirm final export bone order, Blender names, export names, raw IDs, compact IDs, parent indices, and affected vertex counts are present in the structured report.
4. With exact one-hot generated groups, confirm `PASS` and zero unexpected export drift.
5. Enable smoothing and confirm dominant deform changes are quantified separately; smoothing-only drift is `EXPECTED_DRIFT` when no mapping failure is present.
6. Create a skipped detached owner component and confirm affected vertices are counted. Any skipped vertices that would fall back to export root `0` must be reported as `ERROR`, never silently accepted.
7. Include unowned vertices and vertices with no deform groups in the report; confirm they are counted separately from unmatched owned vertices.
8. Rename generated bones or create ASCII/truncation collisions and confirm explicit generated metadata is preferred, collisions are reported, and owner weights remain attached to their compact IDs.
9. Confirm `.001` fuzzy matches, forced/rejected edge decisions, proximity attachments, fallback assignments, and generated-weight checksum status are visible in the report.
10. Run a temporary CAR export/parity check and confirm the dry-run owner array and final bone names/order match the serialized CAR records.

## Known Experimental Limitations

- Topology supplies explicit heads, tails, and PCA roll references, but final roll quality still requires broader visual testing.
- Side-axis and inversion controls are available, but low-symmetry/asymmetric-model fallback needs broader manual testing.
- The reserved Hooks policy does not create hook objects yet; it preserves and reports skipped groups for a later adapter.
- Proposal payloads are stored in an owned Text datablock and are rejected when the source checksum or apply settings change.
- Topology structure always uses canonical imported owners. Optional smoothing rebuilds deform groups from that cache on every run, then smooths generated weights without changing topology analysis or source attributes.

## Report

For a failure, capture:

- model name and owner IDs;
- selected algorithm, component policy, and root override;
- **Debug Rig Info** output;
- a screenshot showing owner boundaries and generated bones;
- whether Legacy behaves differently;
- Blender version and addon commit.
