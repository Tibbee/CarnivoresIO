# Rig Reconstruction Test Checklist

Use duplicate `.blend` files or disposable imports while the **Topology** algorithm is experimental. Keep **Legacy** available for side-by-side comparison.

## Preparation

1. Import a `.car` model with **Compatibility Checks** enabled.
2. Select the imported mesh and open **Animation → Rigging Utilities**.
3. Confirm **Reset to Imported Owners** restores `CarBone_<raw owner>` groups.
4. Save the file before applying either reconstruction algorithm.

## Topology Smoke Test

1. Set **Reconstruction Algorithm** to **Topology (Experimental)**. For Legacy comparisons, leave **Filter Detached Centroid Clusters** disabled unless testing that compatibility option explicitly.
2. Set **Disconnected Components** to **Multiple Roots**.
3. Leave **Root Override Index** at `-1`.
4. Run **Reconstruct Rig from Owners**.
5. Confirm:
   - an armature is created without errors;
   - owner `0`, when present, has a `CarBone_0` bone;
   - limb child heads are near visible owner-region boundaries;
   - detached geometry remains represented as an additional root;
   - no left limb is directly parented to its right-side counterpart when a torso boundary path exists.
6. Run **Debug Rig Info** and record the algorithm version, anatomy classification, mirror pairs, accepted-edge reasons, roots, hierarchy, skipped groups, and mean edge confidence.

## `dilo2b` Reference Regression

Topology v2 with **Attach Nearest**, automatic root (`-1`), and smoothing disabled should report:

- 21 central groups and 7 mirror pairs: `15↔18`, `16↔19`, `17↔20`, `22↔26`, `23↔27`, `24↔28`, `25↔29`;
- `CarBone_18 → 19 → 20` without `CarBone_21` below the forelimb;
- `CarBone_21` attached directly to `CarBone_3`;
- rear chains `30 → 22 → 23 → 24 → 25` and `30 → 26 → 27 → 28 → 29`;
- central tail chain `1 → 30 → 31 → 32 → 33 → 34 → 35`;
- only `CarBone_9–10` marked as `PROXIMITY_FALLBACK`;
- all 35 groups represented and no owner-cache divergence.

Pending pose checks:

1. rotating `CarBone_18` moves only its forelimb;
2. rotating `CarBone_30` moves the rear limbs and tail but not the forward torso;
3. compare automatic `CarBone_3` root pivot with override compact index `29` (`CarBone_30`).

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

## Known Experimental Limitations

- Existing generated rigs are not yet replaced transactionally; remove old test rigs before rerunning.
- Topology supplies explicit heads, tails, and PCA roll references, but final roll quality still requires broader visual testing.
- Bilateral X-axis classification is active; general body-axis inference and low-symmetry fallback need broader asymmetric-model testing.
- The reserved Hooks policy does not create hook objects yet.
- Topology structure always uses canonical imported owners. Optional smoothing rebuilds deform groups from that cache on every run, then smooths generated weights without changing topology analysis or source attributes.

## Report

For a failure, capture:

- model name and owner IDs;
- selected algorithm, component policy, and root override;
- **Debug Rig Info** output;
- a screenshot showing owner boundaries and generated bones;
- whether Legacy behaves differently;
- Blender version and addon commit.
