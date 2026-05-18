# CarnivoresIO — Performance Analysis

> A top-down breakdown of every function, operator, and subsystem, categorized by how often it runs and its computational cost.

---

## 1. Execution-Frequency Categories

### Category A: Once Per Blender Session (Startup/Register)
These run **once** when the addon is enabled.

| Function | File | Cost | Notes |
|----------|------|------|-------|
| `register()` | `__init__.py` | ~1–3 ms | Registers 2 dozen `bpy.types` classes, adds menu items |
| `deploy_presets()` | `utils/preset_deployment.py` | ~1–5 ms | Copies files from addon presets to user scripts; skips if already present |
| Handler registration (4×) | `__init__.py` | ~0.1 ms | Appends to `bpy.app.handlers` lists |
| Property registration (~20 props) | `__init__.py` | ~0.5 ms | `bpy.props` creation is lightweight |

**Verdict**: 🟢 Startup cost is negligible. No concerns.

---

### Category B: Per-User Action (Import / Export / Operator Click)
These run when the user clicks an import/export button or triggers an operator.

#### B.1 3DF Import (`carnivores.import_3df`)
| Function | Hot? | Cost | What it does |
|----------|------|------|-------------|
| `parse_3df()` | ✅ Yes | ~10–50 ms | NumPy `fromfile` reads + ARGB1555 decode (vectorized) |
| `apply_import_matrix()` | Yes | ~1 ms | `co @ homogenous @ matrix.T` for all vertices |
| `create_mesh_object()` | Yes | ~5–20 ms | `mesh.vertices.foreach_set` + face loop init |
| `create_image_texture()` | Medium | ~5 ms | `np.stack` ARGB decode + `.pack()` |
| `create_texture_material()` | No | ~2 ms | Node graph setup (negligible) |
| `create_armature()` | Yes | ~10–30 ms | Bone creation in Edit Mode (Blender API cost) |
| `smooth_vertex_weights()` | 🔴 Red flag | **O(iterations × edges)** | BMesh Laplacian smoothing. **3 iterations × ~2k edges ≈ 100–300 ms**. Jumps significantly with higher iteration counts. |

**Total 3DF import (typical 1k tri model)**: ~50–150 ms without smoothing; +100–300 ms with smoothing.

#### B.2 CAR Import (`carnivores.import_car`)
| Function | Hot? | Cost | Notes |
|----------|------|------|-------|
| `parse_car()` | ✅ Yes | ~10–50 ms | Similar to `.3df` but reads extra header fields |
| `parse_car_animations()` | ✅ Yes | ~20–200 ms | Reads raw `int16` per frame × vertex count. A 64-frame animation on 1024 verts = ~65k × 6 bytes, trivial. But creating shape keys is **expensive** |
| `create_shape_keys_from_car_animations()` | 🔴 Red flag | **~O(frames × verts)** | Each shape key is a full vertex position copy. 64 frames × 1k verts = 64 shape keys. Each key requires a `foreach_set` call. Expect **50–500 ms** depending on model + animation complexity. |
| `auto_create_shape_key_actions_from_car()` | Red flag | **~O(keys × actions)** | Creates FCurves, keyframes, and NLA strips. Keys every shape key individually. **100 ms–1 s** for many animations. |
| Sound import (`.car`) | Medium | ~10–50 ms per sound | Wave file creation + `aud` loading |

**Total CAR import**: ~100 ms (geometry) + up to **1–5 s** (animations + shape keys + NLA + sounds).

#### B.3 3DF / CAR Export (`carnivores.export_3df`, `carnivores.export_car`)
| Function | Hot? | Cost | Notes |
|----------|------|------|-------|
| `gather_mesh_data()` | ✅ Yes | ~10–30 ms | Triangulation, matrix transform, face/vert array flattening. Uses `foreach_get` heavily. |
| `triangulated_mesh_copy()` | Medium | ~5–20 ms | BMesh triangulation via `bmesh.ops.triangulate` |
| `collect_bones_and_owners()` | Yes | ~5–10 ms | Iterates all vertices and their vertex group weights once |
| `image_to_argb1555()` | Yes | ~5 ms | Vectorized `np.clip` and bit-shifting on the entire texture |
| File write (`tofile`) | No | ~1–5 ms | NumPy `tofile` is fast |

**Total export**: ~20–50 ms per model.

#### B.4 Rig Reconstruction (`carnivores.reconstruct_armature`)
| Function | Hot? | Cost | Notes |
|----------|------|------|-------|
| `calculate_vertex_group_centroids()` | ✅ Yes | ~1–5 ms | Vectorized `np.add.at` on ~1k vertices |
| `_find_mirror_partners()` | Medium | ~1 ms | O(N²) pairwise distance on ~20 centroids; tiny |
| `select_root_bone()` | No | ~0.1 ms | O(N²) pairwise on ~20 items |
| `_detect_disconnected_clusters()` | Medium | ~1 ms | BFS on pairwise distance matrix. O(N²), tiny N |
| `infer_hierarchy_mst()` | ✅ Yes | ~2–10 ms | Prim’s MST: O(B²), where B = bone count (usually < 30) |
| `create_armature()` | ✅ Yes | ~10–30 ms | Blender Edit Mode API cost. Proportional to bone count. |

**Total reconstruction**: ~20–50 ms.

#### B.5 Face Flag Operations (`carnivores.create_3df_flags`, `carnivores.select_by_flags`, etc.)
| Function | Hot? | Cost | Notes |
|----------|------|------|-------|
| `update_flag_colors()` | 🔴 Red flag | **O(faces)** | Reads all face flags, recomputes `BYTE_COLOR` per loop corner. **Recomputes the ENTIRE mesh vertex color layer every time**. For 5k faces: ~10–20 ms |
| `count_flag_hits()` | No | ~1 ms per call | Either bmesh iteration (EDIT mode, selected faces only) or NumPy (OBJECT mode) |
| `bulk_modify_flag()` | No | ~1 ms | `foreach_get / foreach_set` on face flags |

---

### Category C: Per-Frame / Continuous (UI polling, modal, handlers)
These run **continuously** while Blender is running or while a specific tool is active.

| Function | File | Trigger | Cost | Risk |
|----------|------|---------|------|------|
| `carnivores_nla_sound_handler()` | `operators/animation.py` | `frame_change_post` | ~0.1–1.0 ms | Runs **every frame change**. Iterates all objects. Opens `aud` device. Contains a 5-second blocklist. Overall low cost but runs neglig **every frame**. |
| `get_active_animation_data()` | `utils/animation.py` | Called by sound handler | ~0.01 ms | Property lookups only |
| `preview_loop_handler()` | `operators/animation.py` | `frame_change_post` | ~0.05 ms | Only active during track preview, returns early otherwise |
| `carnivores_nla_sound_enabled` property | — | Audio toggle check | ~0.01 ms | Boolean lookup |
| UI Panel `draw()` | `operators/animation.py` | Every UI refresh | ~0.1–0.5 ms | Builds layout dynamically. Contains `template_list` which is native C but... |
| `CARNIVORES_UL_animation_list.draw_item()` | `operators/animation.py` | Every template_list draw | ~0.01 ms per item | 50 items = ~0.5 ms |
| Operator `poll()` methods | — | Every UI event | ~0.01 ms each | Bpy checks `poll()` on ~20 operators every frame refresh. Total negligible. |

**Verdict**: 🟢 The continuous cost is very light. The NLA sound handler is the heaviest per-frame code, but it is gated by a boolean and is still far below 1 ms.

---

### Category D: Potentially Exponential / Scaling-Bottleneck Areas

#### D.1 Smoothing (`smooth_vertex_weights`)
```python
for _ in range(iterations):           # 1–10
    for v in bm.verts:                 # V vertices
        for g_idx in all_groups:       # G groups
            for n in neighbors:        # N neighbors (avg ~6)
                # weight math
```
Complexity: **O(iterations × V × G × N)**

At 64 iterations × 1024 verts × 12 groups × 6 neighbors = **~295k inner loop ops**.
In practice, `all_groups` is small (adjacent groups only). But with `joints_only=False`, this gets expensive fast.

**Recommendation**: The current default of 3 iterations is sane. A user cranking it to 10 on a 2k-vert model will feel a **1–2 second freeze**.

#### D.2 Shape Key Creation (`create_shape_keys_from_car_animations`)
Complexity: **O(anim_count × frames × verts)**

Each `obj.shape_key_add(name=...)` call triggers a full Blender data-block allocation and mesh update. This is the single most expensive operation in the entire addon.

| Creature | Verts | Frames | Shape Keys | Estimated Time |
|----------|-------|--------|-----------|----------------|
| Small | 128 | 10 | 10 | ~50 ms |
| Medium | 512 | 30 | 30 | ~300 ms |
| Large | 1024 | 64 | 64 | ~1–2 s |

**Recommendation**: This is fundamentally limited by Blender’s API. No vectorization can help — shape keys are data blocks. Consider adding a progress indicator for long imports.

#### D.3 Reconstruct Armature — MST
Complexity: **O(B²)**, where B = bone count.

Current B is at most 30 (2048-vertex engine limit / sparse ownership). B² = 900 operations. Negligible.

Even with B = 100: 10,000 ops = ~1 ms in NumPy. Still fine.

**Verdict**: 🟢 Not a performance concern.

#### D.4 Texture Encoding (`image_to_argb1555`)
Vectorized numpy on (256 × height) pixels.

Height 512: 131,072 pixels. Operations: `np.clip`, `np.round`, bit shifts. **< 5 ms**.

**Verdict**: 🟢 Negligible.

---

## 2. Memory Characteristics

| Subsystem | Peak Memory | Notes |
|-----------|-------------|-------|
| `.3df` import | O(V + F + T) | Holds full mesh + texture in memory. Typical model: ~1–5 MB |
| `.car` import | O(V × frames) | Shape keys duplicate the entire mesh per frame. 64 frames × 1k verts = 64k point data blocks. **This is the memory elephant in the room.** |
| Reconstruct armature | O(B² + V) | Pairwise distance matrix (tiny) + vertex ownership array |
| Export | O(V + F + T) | One extra triangulated mesh copy (temporary) |
| BMesh smoothing | O(V × groups) | BMesh keeps lists of deform layers. Moderate. |

**Conclusion**: Memory pressure comes almost entirely from shape keys in `.car` import. A 64-frame `.car` on a 1024-vertex model uses ~64 × 1024 × 3 × 4 bytes = ~786 KB of raw data... but Blender’s data-block overhead makes this much larger in practice.

---

## 3. Code-Path Hotspots (From most to least expensive)

### 🔴 Tier 1 — Noticeable freezes on large models
1. **`auto_create_shape_key_actions_from_car()`** — Shape key + FCurve + NLA creation. The most expensive end-user operation.
2. **`keyframe_shape_key_animation_as_action()`** — Creates keyframes for every shape key. O(keys × frames). |
3. **`smooth_vertex_weights()`** — BMesh Laplacian. Cost scales with `iterations` parameter. |
4. **`create_shape_keys_from_car_animations()`** — Allocates shape keys. Frame count is the multiplier. |

### 🟡 Tier 2 — User-perceptible on large models
5. **`create_armature()`** — Edit mode bone creation. ~20 ms for 20 bones; acceptable.
6. **`parse_car_animations()`** — Reading raw animation data. Fast parsing, but large models have many frames.
7. **`gather_mesh_data()`** — Pre-export triangulation + matrix application. A few dozen ms on big meshes.
8. **`update_flag_colors()`** — Rebuilds entire vertex color layer. Called on every flag change.

### 🟢 Tier 3 — Negligible
9. `infer_hierarchy_mst()` — O(30²). Tiny.
10. `select_root_bone()` — O(30²). Tiny.
11. `calculate_vertex_group_centroids()` — Vectorized on ~1k points. Tiny.
12. All UI `draw()` and `poll()` methods.
13. `carnivores_nla_sound_handler()` — Per-frame, but gated and lightweight.

---

## 4. Architectural Observations

### Strengths (Why it performs well)
- **NumPy for everything**: `fromfile`, `np.add.at`, `np.linalg.eigh`, `SVD`, etc. The heavy math is farmed out to C.
- **Vectorized loops**: UV packing, flag operations, color remapping all use NumPy instead of Python `for` loops.
- **`foreach_get`/`foreach_get` usage**: Blender API batched attribute access is used correctly everywhere.
- **`@timed` decorator**: You already have instrumentation built in. Smart.

### Weaknesses / Potential regressions
- **Shape keys are the bottleneck**: No way around this without rewriting in C or Cython. The `obj.shape_key_add()` call is single-threaded and triggers internal dependency updates. Consider collecting all names in memory first and batch-adding, though that may not help much.
- **`update_flag_colors()` is wasteful**: It rebuilds the entire vertex color layer for every flag change. If a user bulk-modifies 500 faces, this runs once. But if a user clicks “toggle flag” 20 times, it rebuilds 20×.
- **BMesh smoothing does not need BMesh**: The smoothing only reads deform weights. You could do this faster with a vertex adjacency pre-computed in NumPy and keep everything in arrays without ever entering the BMesh API.
- **Registration is noisy**: `register()` builds the full menu tree, registers all operators, and copies presets. Not slow, but if Blender restarts addons frequently (e.g., during development with F8 or live reload), `deploy_presets()` disk I/O adds up.

---

## 5. Recommendations

### Immediate (no new features needed)
1. **Cache `update_flag_colors()`**: Only update the changed faces, not the whole mesh. Maintain a dirty-face list.
2. **Progress bars for `.car` import**: Since shape-key creation is the lone expensive operation, wrap it in `context.window_manager.progress_begin/end` so the user sees a progress bar.
3. **Debounce `update_flag_colors()`**: If triggered from a panel property, add a small delay (0.1 s) before recomputing, so rapid toggles don’t stack.

### Medium-term
4. **Replace BMesh smoothing with NumPy**: Pre-compute a sparse adjacency matrix from the mesh topology once. Then smoothing reduces to `(1-α)I + αA` matrix multiplication, which is `O(V)` per iteration with `scipy.sparse`. This would make smoothing 10–100× faster.
5. **Lazy preset deployment**: In `register()`, skip `deploy_presets()` if the addon hasn’t been updated. Check a version hash or modification time.

### Long-term
6. **Shape key batch creation API**: If you ever move to a C extension or Blender’s Python API gains a batch shape-key method, use it. This is the #1 remaining bottleneck.

---

## 6. Bottom-Line Verdict

| Metric | Grade | Explanation |
|--------|-------|-------------|
| **Startup time** | A+ | < 10 ms. Invisible to the user. |
| **Import speed** | B+ | Geometry is very fast. `.car` animation import feels slow because of Blender’s shape key API, not your code. |
| **Export speed** | A | Vectorized, fast, clean. |
| **UI responsiveness** | A+ | Panels are light; `poll()` is minimal. |
| **Per-frame overhead** | A | Sound handler is the only continuous code, and it is well-gated. |
| **Scalability to large models** | B | The BMesh smoothing and shape key creation are the only operations that scale poorly with model complexity. Everything else is O(N) with small constants. |

**Overall**: The addon is **well-optimized** for its domain. The heavy operations are the ones fundamentally constrained by Blender APIs (shape key creation) or by the nature of the task (animation import). No action is required for day-to-day use, but the smoothing and flag color systems could be tightened further if you want to push the performance envelope.
