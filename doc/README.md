# CarnivoresIO Documentation

Consolidated technical documentation for the CarnivoresIO Blender add-on, covering file formats, core systems, development guidelines, and reference specifications.

## Quick Links

| Document | Description |
|----------|-------------|
| [Formats](FORMATS.md) | Binary format specifications (`.3df`, `.car`, `.3dn`), map/resource files, coordinate conversion math |
| [Systems](SYSTEMS.md) | Core algorithms: skeleton reconstruction, vertex animation analysis, NLA sound synchronization |
| [Development](DEVELOPMENT.md) | Developer notes, Blender 5.0 migration guide, future roadmap (rig reconstruction, visualization, animation), known issues |
| [Reference](reference.md) | Canonical constants, face flag definitions, engine limits, validation rules |
| [Improvements](IMPROVEMENTS.md) | Implementation roadmap for audio system correctness, lifecycle, and deferred workflow enhancements |
| [UI Usability Roadmap](UI_USABILITY_ROADMAP.md) | Prioritized implementation and LLM handoff plan for model health, reports, dialogs, face flags, animation/audio, rigging, and onboarding UX |
| [Rig Reconstruction Plan](RIG_RECONSTRUCTION_PLAN.md) | Staged implementation plan for owner correctness, topology-aware rigs, previews, reconciliation, and motion-assisted reconstruction |
| [Rig Reconstruction Handoff](RIG_RECONSTRUCTION_HANDOFF.md) | Copyable fresh-session prompt, current committed state, latest `dilo2b` findings, and immediate next actions |
| [Performance Handoff](PERFORMANCE_HANDOFF.md) | Copyable fresh-session prompt, measured baselines, committed fast paths, and open performance/audio items |
| [Rig Test Checklist](RIG_TEST_CHECKLIST.md) | Manual smoke tests, component-policy checks, known experimental limitations, and reporting guidance |
| [Performance Analysis](PERFORMANCE_ANALYSIS.md) | Top-down cost analysis of every function, operator, and subsystem by execution frequency |
| [Audio Test Checklist](AUDIO_TEST_CHECKLIST.md) | Manual verification checklist for audio import/export and focused playback testing |

## Project Context

This add-on supports importing/exporting Carnivores engine formats (`.3df`, `.car`, `.3dn`) for Blender 4.2+. Build instructions and coding conventions are documented in the repository root and [AGENTS.md](../AGENTS.md).

## Documentation Structure

- **Single source of truth**: Face flags, engine limits, and core constants are defined once in [Reference](reference.md) and linked from other documents
- **Separation of concerns**: Format specs (FORMATS), algorithms (SYSTEMS), development plans (DEVELOPMENT, IMPROVEMENTS), and performance data (PERFORMANCE_ANALYSIS) each live in their own file
- **Flat directory**: All documentation is at the `doc/` root with no nested subdirectories
