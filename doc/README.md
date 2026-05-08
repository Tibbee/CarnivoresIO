# CarnivoresIO Documentation

Consolidated technical documentation for the CarnivoresIO Blender add-on, covering file formats, core systems, development guidelines, and reference specifications.

This documentation was compacted from 12+ scattered files into a flat 5-file structure for easier maintenance and navigation.

## Quick Links

| Document | Description |
|----------|-------------|
| [Formats](formats.md) | Binary format specifications (`.3df`, `.car`, `.3dn`), map/resource files, coordinate conversion math |
| [Systems](systems.md) | Core algorithms: skeleton reconstruction, vertex animation analysis, NLA sound synchronization |
| [Development](development.md) | Developer notes, Blender 5.0 migration guide, future roadmap, known issues |
| [Reference](reference.md) | Canonical constants, face flag definitions, engine limits, validation rules |

## Project Context

This add-on supports importing/exporting Carnivores engine formats (`.3df`, `.car`, `.3dn`) for Blender 4.2+. Build instructions and coding conventions are documented in the repository root.

## Documentation Structure Notes

- **Deduplication**: Face flags, engine limits, and core constants are defined once in [Reference](reference.md) and linked from other documents
- **Merged Content**: All former subdirectory content (formats/, systems/, development/) is consolidated into the corresponding top-level files
- **No Nested Dirs**: Flat structure eliminates unnecessary navigation overhead
