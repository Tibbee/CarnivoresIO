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
- Current branch: feat/rig-reconstruction-improvements.
- Current committed HEAD: a64d423 feat(rig): add pure geometry analyzer.
- Phase 3 / Topology v2 and the latest Legacy regression fix are uncommitted.
- Keep Legacy as the default and preserve its successful behavior.
- Do not touch or commit .commandcode/.
- User screenshots in doc/*.png are untracked test evidence; do not commit them
  unless the user explicitly requests it.
- Use D:/Portable/Git/.gitmessage when an atomic commit is eventually requested.

Current implementation:
- Signed/lossless CAR owner handling and non-destructive validation are committed.
- Legacy centroid-cluster filtering caused valid distal legs/tail groups to be
  skipped on `dilo2b`. It is now diagnostic-only by default; the explicit
  `Filter Detached Centroid Clusters` option restores pruning when enabled.
- Experimental Topology algorithm v2 is anatomy-constrained:
  - robust cross-owner boundary candidates;
  - deterministic bilateral pairing around imported X lateral axis;
  - central backbone construction with low-confidence proximity completion;
  - same-side lateral components attached to the central backbone exactly once;
  - mirrored components prefer the same central owner;
  - explicit disconnected policies;
  - proposal heads, tails, and roll references passed directly to Blender;
  - optional Semantic L/R suffixes shared with Legacy and synchronized with vertex groups;
  - optional Topology deform smoothing rebuilt from canonical one-hot owners on every run, so smoothing is non-cumulative and cannot alter hierarchy evidence;
  - accepted-edge reasons/confidence and anatomy classification in Debug Rig Info.
- Algorithm v2 specifically prevents lateral limbs from bridging central torso
  regions. A pure regression test covers this.

Latest real-asset result (`dilo2b`, Topology v2, Attach Nearest, root override -1):
- 35 groups, no skipped groups, no owner divergence.
- 21 central groups and these 7 correct mirror pairs:
  15↔18, 16↔19, 17↔20, 22↔26, 23↔27, 24↔28, 25↔29.
- Root selected: raw CarBone_3 / compact index 2.
- Corrected hierarchy highlights:
  4→15→16→17
  4→18→19→20
  3→21→2→1→30→31→32→33→34→35
  30→22→23→24→25
  30→26→27→28→29
- CarBone_9–10 is the only PROXIMITY_FALLBACK edge and correctly joins the
  formerly detached head component under Attach Nearest.
- CarBone_18 is no longer an ancestor of CarBone_21.
- The latest visual result is doc/blender_5KwvrdjiaJ.png.
- CarBone_3 was highlighted as the automatic root. Moving a root should move the
  whole connected mesh; determine whether the user objects to that behavior or
  only to its pivot location before changing root inference.

Immediate next work:
1. Ask for/interpret the pending pose checks, initially with smoothing disabled:
   - rotate CarBone_18: only its forelimb should move;
   - rotate CarBone_30: rear limbs and tail should move, forward torso should not;
   - optionally compare automatic root CarBone_3 with compact override index 29
     (raw CarBone_30) for pelvis-oriented pivot placement.
2. Do not change automatic root inference merely because the root moves the whole
   mesh; that is expected. Change it only if pivot placement is demonstrably poor.
3. If pose checks pass, test one asymmetric model and one additional symmetric
   model before committing Phase 3.
4. Re-run all Blender tests, registration, compileall, and git diff --check.
5. Keep the eventual Phase 3 commit atomic and exclude .commandcode/ and test PNGs.

Important implementation notes:
- Pure logic: utils/rig_reconstruction.py.
- Blender adapter/metadata: utils/animation.py.
- Armature creation with optional explicit tails/roll: utils/io.py.
- UI and Debug Rig Info: operators/animation.py and __init__.py.
- Pure hierarchy tests: tests/test_rig_hierarchy.py.
- Blender adapter tests: tests/test_rig_adapter.py.
- Root override is a compact zero-based index, not raw owner ID. On `dilo2b`,
  compact 29 corresponds to raw CarBone_30.
- Existing generated rigs are not replaced transactionally; remove old rigs before
  each manual run.

Verification at handoff:
- 32 automated tests pass under Blender 5.2, including Topology semantic naming and non-destructive generated smoothing.
- Addon register/unregister smoke test passes.
- compileall and git diff --check pass (only an LF→CRLF working-copy warning).
- No remote operations have occurred.

Start by inspecting git status/diff and confirming the pending user pose result.
Do not commit until real-asset testing is complete or the user explicitly asks.
```

## Local Git State at Handoff

- Branch: `feat/rig-reconstruction-improvements`
- HEAD: `a64d423 feat(rig): add pure geometry analyzer`
- Phase 3 and Topology v2 remain in the working tree.
- `.commandcode/` is untracked and must remain untouched.
- User test PNGs are untracked and are not release documentation by default.
- No remote operations were performed during this work.

## Verification Command

From Git Bash at the repository root:

```bash
WINPWD=$(pwd -W)
../../../../blender.exe --background --factory-startup --python-expr \
  "import sys,unittest; p=r'$WINPWD\\tests'; sys.path.insert(0,p); result=unittest.TextTestRunner(verbosity=1).run(unittest.defaultTestLoader.discover(p)); raise SystemExit(0 if result.wasSuccessful() else 1)"
python -m compileall -q core parsers utils operators tests
git diff --check
```

Registration smoke test:

```bash
WINPWD=$(pwd -W)
../../../../blender.exe --background --factory-startup --python-expr \
  "import sys; sys.path.insert(0,r'$WINPWD\\..'); import carnivores_io; carnivores_io.register(); carnivores_io.unregister(); print('register cycle OK')"
```
