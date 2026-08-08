# Rig Reconstruction Session Handoff

Copy the prompt below into a fresh coding-agent session. Read this file together with [`RIG_RECONSTRUCTION_PLAN.md`](RIG_RECONSTRUCTION_PLAN.md), [`RIG_TEST_CHECKLIST.md`](RIG_TEST_CHECKLIST.md), and the repository [`AGENTS.md`](../AGENTS.md) before changing code.

## Sendoff Prompt

```text
Continue the CarnivoresIO `.car` rig-reconstruction work in
D:/Portable/Blender/portable/extensions/user/carnivores_io.

Read AGENTS.md, doc/RIG_RECONSTRUCTION_HANDOFF.md,
doc/RIG_RECONSTRUCTION_PLAN.md, doc/RIG_TEST_CHECKLIST.md, and the relevant
sections of doc/SYSTEMS.md completely before editing.

Constraints:
- Work locally only. Do not fetch, pull, push, or modify remote branches.
- Current branch: main.
- Current committed HEAD: c037643 feat(rig): add round-trip reconciliation.
- Phases 0-7 are committed and merged: owner mapping (0-1), pure geometry
  analysis (2), anatomy-aware Topology v2-v4 (3), weight generation (4),
  armature lifecycle (5), proposal workflow (6), round-trip reconciliation (7).
- Keep Legacy as the default and preserve its successful behavior.
- Do not touch or commit .commandcode/.
- User screenshots in doc/*.png are untracked test evidence; do not commit them
  unless the user explicitly requests it.
- Use D:/Portable/Git/.gitmessage when an atomic commit is eventually requested.

Current implementation (all committed):
- Signed/lossless CAR owner handling and non-destructive validation are committed.
- Legacy centroid-cluster filtering caused valid distal legs/tail groups to be
  skipped on `dilo2b`. It is now diagnostic-only by default; the explicit
  `Filter Detached Centroid Clusters` option restores pruning when enabled.
- Topology algorithm v2 is anatomy-constrained:
  - robust cross-owner boundary candidates;
  - deterministic bilateral pairing around imported X lateral axis;
  - central backbone construction with low-confidence proximity completion;
  - same-side lateral components attached to the central backbone exactly once;
  - mirrored components prefer the same central owner;
  - explicit disconnected policies (Multiple Roots / Attach Nearest / Skip /
    Reserve for Hooks);
  - proposal heads, tails, and roll references passed directly to Blender;
  - optional Semantic L/R suffixes shared with Legacy and synchronized with vertex groups;
  - optional Topology deform smoothing rebuilt from canonical one-hot owners on every run, so smoothing is non-cumulative and cannot alter hierarchy evidence;
  - accepted-edge reasons/confidence and anatomy classification in Debug Rig Info.
- Algorithm v2 specifically prevents lateral limbs from bridging central torso
  regions. A pure regression test covers this.
- Topology v3 orients laterally ambiguous midline leaf controls along signed
  Blender Y body flow; v4 prefers the earliest raw owner among central candidates
  with at least two real topology-boundary connections, avoiding tiny but
  graph-central control groups as roots while retaining scored fallback selection
  for endpoint-only structures.
- Phase 5 lifecycle (d4d9112): transactional armature construction with context
  snapshot/restore and rollback, explicit rig policies (CREATE_NEW /
  REPLACE_GENERATED / CANCEL), explicit `Remove Skipped Owner Groups` cleanup
  that only removes recorded groups, deterministic roll alignment, and separate
  Blender names vs ASCII/31-byte export names with a serialized name map.
- Phase 6 proposal workflow (ed93a20): `Analyze Rig Proposal`, `Apply Rig
  Proposal`, `Clear Rig Preview`, `Validate Rig Round Trip` operators; analysis
  is non-mutating with scoped previews; proposals are persisted with source
  checksums and settings fingerprints so stale/forged proposals are rejected
  before any armature change. `Reconstruct Rig from Owners` remains the direct
  compatibility shortcut.
- Phase 7 round-trip reconciliation (c037643): a shared non-writing export-owner
  dry run used by `.3DF`, `.CAR`, and `.3DN` mapping; canonical raw/compact
  owners, generated dominant deform groups, final export owners, source groups,
  hierarchy, and generated-weight checksums are reconciled with explicit
  `PASS`, `EXPECTED_DRIFT` (smoothing-only drift), `WARNING`, and `ERROR`
  levels. Fuzzy matching is disabled when explicit source-owner metadata exists.

Latest real-asset reference (`dilo2b`, Topology v4, Attach Nearest, automatic
root, smoothing disabled) — see RIG_TEST_CHECKLIST.md for the full regression:
- 35 groups, no skipped groups, no owner-cache divergence; 21 central groups
  and 7 correct mirror pairs (15↔18, 16↔19, 17↔20, 22↔26, 23↔27, 24↔28, 25↔29);
- forelimb chains 4→15→16→17 and 4→18→19→20 with `CarBone_21` attached directly
  to `CarBone_3`, not below the forelimb;
- rear chains 30→22→23→24→25 and 30→26→27→28→29, central tail chain
  1→30→31→32→33→34→35;
- only `CarBone_9–10` is `PROXIMITY_FALLBACK` (joins the formerly detached head
  component under Attach Nearest).
- Confirmed pose checks: rotating `CarBone_18` moves only its forelimb;
  rotating `CarBone_30` moves rear limbs and tail but not the forward torso.
- Root: earlier scoring selected `CarBone_3`; Topology v4 should automatically
  select the source-ordered backbone candidate `CarBone_1`. `CarBone_30`
  (override compact index 29) remains a useful pelvis-root comparison. The
  pose isolation checks must be re-run under the v4 automatic root.

Immediate next work:
1. Re-run the `dilo2b` reference regression under the current HEAD, confirm the
   automatic root is `CarBone_1` (not `CarBone_3`), and recheck both pose
   isolation tests (CarBone_18 forelimb-only; CarBone_30 rear+tail) after the
   automatic-root change.
2. If pose checks pass, test one asymmetric model and one additional symmetric
   model. Follow-up fixes from this testing become an atomic commit.
3. Phase 7 follow-up: run a temporary CAR export/re-import parity check on a
   real asset and confirm the dry-run owner array and final bone names/order
   match the serialized CAR records (checklist item 10).
4. Do not start Phase 8 (motion-assisted inference) or Phase 9 (skeletal
   animation conversion); both are planned but not started.
5. Re-run all 101 Blender tests, the registration smoke test, compileall, and
   git diff --check. Keep any commit atomic and exclude .commandcode/ and test
   PNGs.

Important implementation notes:
- Pure logic: utils/rig_reconstruction.py.
- Blender adapter/metadata: utils/animation.py.
- Armature creation, lifecycle policies, context snapshot/restore: utils/io.py.
- UI and Debug Rig Info: operators/animation.py and __init__.py.
- Generated armature metadata keys: carnivores_rig_algorithm,
  carnivores_rig_algorithm_version, carnivores_rig_metadata_version,
  carnivores_reconstruct_source_id, carnivores_reconstruct_source_mesh,
  carnivores_reconstruct_owner_mapping, carnivores_reconstruct_bone_name_map.
- Pure hierarchy/geometry tests: tests/test_rig_hierarchy.py,
  tests/test_rig_geometry.py; Blender adapter tests: tests/test_rig_adapter.py;
  reconciliation tests: tests/test_rig_reconciliation.py.
- Root override is a compact zero-based index, not raw owner ID. On `dilo2b`,
  compact 29 corresponds to raw CarBone_30.
- Generated rigs are protected by lifecycle policies: CREATE_NEW preserves the
  old generated armature, REPLACE_GENERATED only deletes the old rig after a
  successful swap, and CANCEL (or an unrelated user armature) refuses the
  operation without changes.

Verification at handoff:
- 101 automated tests pass under Blender 5.2 (10 files under tests/).
- Addon register/unregister smoke test passes.
- compileall and git diff --check pass.
- No remote operations have occurred.

Start by inspecting git status/diff and re-running the dilo2b reference
regression. Do not commit until real-asset testing is complete or the user
explicitly asks.
```

## Local Git State at Handoff

- Branch: `main`
- HEAD: `c037643 feat(rig): add round-trip reconciliation`
- Phases 0-7 are committed and merged into `main` (Phase 5: `d4d9112`, Phase 6: `ed93a20`, Phase 7: `c037643`).
- `.commandcode/` is untracked and must remain untouched.
- User test PNGs are untracked and are not release documentation by default.
- No remote operations were performed during this work.

## Verification Command

From Git Bash at the repository root (same command as `tests/README.md`; it
registers the addon because operator tests call `bpy.ops.carnivores.*`):

```bash
WINPWD=$(pwd -W)
../../../../blender.exe --background --factory-startup --python-expr \
  "import sys,unittest; sys.path.insert(0,r'$WINPWD\\..'); import carnivores_io; carnivores_io.register(); p=r'$WINPWD'; sys.path.insert(0,p); result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.discover(p)); carnivores_io.unregister(); raise SystemExit(0 if result.wasSuccessful() else 1)"
python -m compileall -q core parsers utils operators tests
git diff --check
```

Registration smoke test:

```bash
WINPWD=$(pwd -W)
../../../../blender.exe --background --factory-startup --python-expr \
  "import sys; sys.path.insert(0,r'$WINPWD\\..'); import carnivores_io; carnivores_io.register(); carnivores_io.unregister(); print('register cycle OK')"
```
