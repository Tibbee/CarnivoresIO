# CarnivoresIO Documentation

Consolidated technical documentation for the CarnivoresIO Blender add-on, covering file formats, core systems, development guidelines, and reference specifications.

## Quick Links

| Document | Description |
|----------|-------------|
| [Formats](FORMATS.md) | Binary format specifications (`.3df`, `.car`, `.3dn`), map/resource files, coordinate conversion math |
| [Systems](SYSTEMS.md) | Core algorithms: skeleton reconstruction, vertex animation analysis, NLA sound synchronization |
| [Development](DEVELOPMENT.md) | Developer notes, Blender 5.0 migration guide, future roadmap (rig reconstruction, visualization, animation), known issues |
| [Reference](reference.md) | Canonical constants, face flag definitions, engine limits, validation rules |
| [Improvements](IMPROVEMENTS.md) | Implementation roadmap for audio system correctness, lifecycle, deferred workflow enhancements, and import/export robustness and data integrity |
| [UI Usability Roadmap](UI_USABILITY_ROADMAP.md) | Prioritized implementation and LLM handoff plan for model health, reports, dialogs, face flags, animation/audio, rigging, and onboarding UX |
| [Rig Reconstruction Plan](RIG_RECONSTRUCTION_PLAN.md) | Staged implementation plan for owner correctness, topology-aware rigs, previews, reconciliation, and motion-assisted reconstruction |
| [Rig Reconstruction Handoff](RIG_RECONSTRUCTION_HANDOFF.md) | Copyable fresh-session prompt, current committed state (Phases 0-7), latest `dilo2b` findings, and immediate next actions |
| [Performance Handoff](PERFORMANCE_HANDOFF.md) | Copyable fresh-session prompt, measured baselines, committed fast paths, and open performance/audio items |
| [Rig Test Checklist](RIG_TEST_CHECKLIST.md) | Manual smoke tests, component-policy checks, known experimental limitations, and reporting guidance |
| [Performance Analysis](PERFORMANCE_ANALYSIS.md) | Top-down cost analysis of every function, operator, and subsystem by execution frequency |
| [Audio Test Checklist](AUDIO_TEST_CHECKLIST.md) | Manual verification checklist for audio import/export and focused playback testing |
| [Phase 5 Fix Continuation](PHASE5_FIX_CONTINUATION.md) | Historical record of the Phase 5 lifecycle fixes; superseded by the Plan and Handoff |
| [Test Suite](../tests/README.md) | How to run the automated Blender test suite (118 tests / 11 files) |

## Project Context

This add-on supports Blender 4.2+. Format support is **not symmetric** — import coverage is narrower than export:

| Format | Import | Export | Notes |
|--------|--------|--------|-------|
| `.3df` | Yes | Yes (single + batch) | Static models; textures, bones/hooks, face flags |
| `.car` | Yes | Yes | Vertex animations (shape keys), embedded sounds, owner/rig data |
| `.3dn` | No | Yes | Static mobile/HD model export only; no importer is planned yet |
| `.vtl` | No | Yes | Standalone vertex animation export only; animations are round-tripped through `.car` |

Build instructions and coding conventions are documented in the repository root and [AGENTS.md](../AGENTS.md). The complete user-facing feature list, including the same coverage table, lives in the root [README](../README.md).

## Documentation Structure

- **Single source of truth**: Face flags, engine limits, and core constants are defined once in [Reference](reference.md) and linked from other documents
- **Separation of concerns**: Format specs (FORMATS), algorithms (SYSTEMS), development plans (DEVELOPMENT, IMPROVEMENTS), and performance data (PERFORMANCE_ANALYSIS) each live in their own file
- **Flat directory**: All documentation is at the `doc/` root with no nested subdirectories
