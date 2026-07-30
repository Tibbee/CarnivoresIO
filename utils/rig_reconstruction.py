"""Pure data structures and algorithms used by CAR rig reconstruction.

This module intentionally has no Blender dependencies so owner handling and future
rig proposal analysis can be tested outside Blender.
"""

from dataclasses import dataclass
import heapq
import json

import numpy as np


OWNER_MAPPING_SCHEMA_VERSION = 2
OWNER_MAPPING_PROPERTY = "carnivores_owner_mapping"
UNOWNED_COMPACT_ID = -1


@dataclass(frozen=True)
class MeshAnalysisInput:
    """Validated, Blender-independent mesh data for reconstruction analysis."""

    vertices: np.ndarray
    triangles: np.ndarray
    edges: np.ndarray
    compact_owners: np.ndarray
    raw_by_compact: np.ndarray
    group_names: tuple[str, ...]


@dataclass(frozen=True)
class RigGroup:
    """Stable geometric measurements for one nonempty owner region."""

    compact_id: int
    raw_owner_id: int
    name: str
    vertex_indices: np.ndarray
    vertex_count: int
    centroid: np.ndarray
    median_center: np.ndarray
    bounds_min: np.ndarray
    bounds_max: np.ndarray
    principal_axes: np.ndarray
    principal_values: np.ndarray
    principal_direction_confidence: float
    topology_islands: tuple[np.ndarray, ...]


@dataclass(frozen=True)
class RigGeometryAnalysis:
    """Scale-normalized group geometry; positions remain in source local space."""

    mesh: MeshAnalysisInput
    groups: tuple[RigGroup, ...]
    characteristic_scale: float
    normalization_origin: np.ndarray
    normalized_group_centroids: np.ndarray
    warnings: tuple[str, ...]
    algorithm_version: int = 1


@dataclass(frozen=True)
class RigEdgeCandidate:
    """Scored undirected relationship between two owner regions."""

    group_a: int
    group_b: int
    boundary_edge_count: int
    boundary_joint: np.ndarray
    nearest_distance: float
    cost_terms: dict[str, float]
    total_cost: float
    confidence: float
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class RigProposal:
    """Deterministic static rig proposal indexed by compact owner group."""

    groups: tuple[RigGroup, ...]
    parent_by_group: np.ndarray
    head_by_group: np.ndarray
    tail_by_group: np.ndarray
    roll_reference_by_group: np.ndarray
    root_groups: tuple[int, ...]
    edge_candidates: tuple[RigEdgeCandidate, ...]
    accepted_edges: tuple[tuple[int, int], ...]
    skipped_groups: tuple[int, ...]
    warnings: tuple[str, ...]
    settings: dict
    algorithm_version: int = 1


@dataclass(frozen=True)
class OwnerMapping:
    """Lossless raw CAR owners plus their dense reconstruction indices."""

    raw_per_vertex: np.ndarray
    compact_per_vertex: np.ndarray
    raw_by_compact: np.ndarray
    compact_by_raw: dict
    unowned_vertex_indices: np.ndarray

    @property
    def group_count(self):
        return int(self.raw_by_compact.size)

    @property
    def bone_names(self):
        return [f"CarBone_{int(raw_id)}" for raw_id in self.raw_by_compact]


def build_owner_mapping(raw_owners):
    """Build a deterministic positive-raw-ID to dense-ID mapping.

    CAR owners are signed shorts: zero and positive values are valid owner
    groups, while negative values are unowned. The raw array is copied and never
    modified.
    """
    raw = np.asarray(raw_owners, dtype=np.int32).reshape(-1).copy()
    owned_mask = raw >= 0
    raw_by_compact = np.unique(raw[owned_mask]).astype(np.int32, copy=False)

    compact = np.full(raw.shape, UNOWNED_COMPACT_ID, dtype=np.int32)
    if raw_by_compact.size:
        compact[owned_mask] = np.searchsorted(raw_by_compact, raw[owned_mask]).astype(np.int32)

    compact_by_raw = {
        int(raw_id): compact_id
        for compact_id, raw_id in enumerate(raw_by_compact.tolist())
    }
    return OwnerMapping(
        raw_per_vertex=raw,
        compact_per_vertex=compact,
        raw_by_compact=raw_by_compact,
        compact_by_raw=compact_by_raw,
        unowned_vertex_indices=np.flatnonzero(~owned_mask).astype(np.int32),
    )


def owner_mapping_to_metadata(mapping):
    """Return compact JSON metadata suitable for a Blender ID property."""
    payload = {
        "schema_version": OWNER_MAPPING_SCHEMA_VERSION,
        "negative_is_unowned": True,
        "raw_by_compact": [int(value) for value in mapping.raw_by_compact],
        "bone_names": mapping.bone_names,
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _as_index_array(values, columns, name):
    array = np.asarray(values, dtype=np.int64)
    if array.size == 0:
        return np.empty((0, columns), dtype=np.int32)
    if array.ndim != 2 or array.shape[1] != columns:
        raise ValueError(f"{name} must have shape (N, {columns}); got {array.shape}.")
    return array.astype(np.int32, copy=True)


def derive_unique_edges(triangles, vertex_count):
    """Return sorted unique undirected edges derived from triangle indices."""
    triangles = _as_index_array(triangles, 3, "Triangles")
    if triangles.size == 0:
        return np.empty((0, 2), dtype=np.int32)
    if np.any(triangles < 0) or np.any(triangles >= vertex_count):
        raise ValueError("Triangle indices reference vertices outside the mesh.")

    edges = np.concatenate(
        (triangles[:, (0, 1)], triangles[:, (1, 2)], triangles[:, (2, 0)]),
        axis=0,
    )
    edges.sort(axis=1)
    edges = edges[edges[:, 0] != edges[:, 1]]
    return np.unique(edges, axis=0).astype(np.int32, copy=False)


def build_mesh_analysis_input(
    vertices,
    compact_owners,
    raw_by_compact,
    *,
    triangles=None,
    edges=None,
    group_names=None,
):
    """Validate and copy mesh arrays into a deterministic pure-analysis input."""
    vertices = np.asarray(vertices, dtype=np.float64)
    if vertices.size == 0:
        vertices = np.empty((0, 3), dtype=np.float64)
    if vertices.ndim != 2 or vertices.shape[1] != 3:
        raise ValueError(f"Vertices must have shape (N, 3); got {vertices.shape}.")
    vertices = vertices.copy()
    if not np.isfinite(vertices).all():
        raise ValueError("Vertex coordinates contain NaN or infinite values.")

    owners = np.asarray(compact_owners, dtype=np.int32).reshape(-1).copy()
    if owners.size != vertices.shape[0]:
        raise ValueError(
            f"Owner count {owners.size} does not match vertex count {vertices.shape[0]}."
        )

    raw_ids = np.asarray(raw_by_compact, dtype=np.int32).reshape(-1).copy()
    if np.any(raw_ids < 0) or np.unique(raw_ids).size != raw_ids.size:
        raise ValueError("Raw owner IDs must be unique non-negative values.")
    if np.any(owners < UNOWNED_COMPACT_ID) or np.any(owners >= raw_ids.size):
        raise ValueError("Compact owners must be -1 or valid compact group indices.")

    triangles = _as_index_array(
        [] if triangles is None else triangles, 3, "Triangles"
    )
    if triangles.size and (np.any(triangles < 0) or np.any(triangles >= len(vertices))):
        raise ValueError("Triangle indices reference vertices outside the mesh.")

    if edges is None:
        edges = derive_unique_edges(triangles, len(vertices))
    else:
        edges = _as_index_array(edges, 2, "Edges")
        if edges.size and (np.any(edges < 0) or np.any(edges >= len(vertices))):
            raise ValueError("Edge indices reference vertices outside the mesh.")
        edges.sort(axis=1)
        edges = edges[edges[:, 0] != edges[:, 1]]
        edges = np.unique(edges, axis=0).astype(np.int32, copy=False)

    if group_names is None:
        names = tuple(f"CarBone_{int(raw_id)}" for raw_id in raw_ids)
    else:
        names = tuple(str(name) for name in group_names)
        if len(names) != raw_ids.size:
            raise ValueError("Group-name count does not match raw owner mapping.")

    return MeshAnalysisInput(
        vertices=vertices,
        triangles=triangles,
        edges=edges,
        compact_owners=owners,
        raw_by_compact=raw_ids,
        group_names=names,
    )


def _stable_principal_axes(points):
    centered = points - np.mean(points, axis=0)
    if len(points) < 2 or not np.any(centered):
        return np.identity(3), np.zeros(3), 0.0

    _, singular_values, axes = np.linalg.svd(centered, full_matrices=True)
    values = np.zeros(3, dtype=np.float64)
    values[:singular_values.size] = singular_values ** 2 / max(len(points) - 1, 1)

    # SVD signs are arbitrary. Canonicalize each axis by making its largest
    # absolute component positive, with NumPy's first-index tie break.
    for axis in axes[:2]:
        pivot = int(np.argmax(np.abs(axis)))
        if axis[pivot] < 0:
            axis *= -1.0
    # Keep a right-handed orthonormal frame after sign canonicalization.
    axes[2] = np.cross(axes[0], axes[1])
    axes[2] /= max(float(np.linalg.norm(axes[2])), np.finfo(np.float64).eps)

    epsilon = np.finfo(np.float64).eps
    confidence = float(max(0.0, values[0] - values[1]) / max(values[0], epsilon))
    return axes.astype(np.float64, copy=False), values, confidence


def _group_topology_islands(vertex_indices, edges):
    if vertex_indices.size == 0:
        return ()
    members = set(int(index) for index in vertex_indices)
    adjacency = {index: [] for index in members}
    for first, second in edges:
        first, second = int(first), int(second)
        if first in members and second in members:
            adjacency[first].append(second)
            adjacency[second].append(first)

    islands = []
    unseen = set(members)
    while unseen:
        start = min(unseen)
        stack = [start]
        unseen.remove(start)
        island = []
        while stack:
            current = stack.pop()
            island.append(current)
            for neighbor in sorted(adjacency[current], reverse=True):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        islands.append(np.asarray(sorted(island), dtype=np.int32))
    return tuple(islands)


def _minimum_region_distance(first, second, chunk_size=512):
    best_squared = np.inf
    for first_offset in range(0, len(first), chunk_size):
        first_chunk = first[first_offset:first_offset + chunk_size]
        for second_offset in range(0, len(second), chunk_size):
            second_chunk = second[second_offset:second_offset + chunk_size]
            distances = first_chunk[:, None, :] - second_chunk[None, :, :]
            squared = np.einsum("ijk,ijk->ij", distances, distances)
            best_squared = min(best_squared, float(np.min(squared)))
    return float(np.sqrt(best_squared))


def _characteristic_scale(vertices, owners, group_indices):
    nearest_by_group = np.full(len(group_indices), np.inf, dtype=np.float64)
    for first_index, first_indices in enumerate(group_indices):
        for second_index in range(first_index + 1, len(group_indices)):
            distance = _minimum_region_distance(
                vertices[first_indices], vertices[group_indices[second_index]]
            )
            if distance > 0.0:
                nearest_by_group[first_index] = min(nearest_by_group[first_index], distance)
                nearest_by_group[second_index] = min(nearest_by_group[second_index], distance)

    nearest = nearest_by_group[np.isfinite(nearest_by_group)]
    if nearest.size:
        scale = float(np.median(nearest))
        if scale > np.finfo(np.float64).eps:
            return scale

    owned = vertices[owners >= 0]
    if owned.size:
        diagonal = float(np.linalg.norm(np.ptp(owned, axis=0)))
        if diagonal > np.finfo(np.float64).eps:
            return diagonal
    return 1.0


def analyze_rig_geometry(mesh):
    """Compute deterministic owner-group geometry without inferring hierarchy."""
    if not isinstance(mesh, MeshAnalysisInput):
        raise TypeError("analyze_rig_geometry expects MeshAnalysisInput.")

    warnings = []
    group_indices = [
        np.flatnonzero(mesh.compact_owners == compact_id).astype(np.int32)
        for compact_id in range(mesh.raw_by_compact.size)
    ]
    characteristic_scale = _characteristic_scale(
        mesh.vertices, mesh.compact_owners, [indices for indices in group_indices if indices.size]
    )

    groups = []
    for compact_id, indices in enumerate(group_indices):
        if indices.size == 0:
            warnings.append(
                f"Owner group {compact_id} (raw {int(mesh.raw_by_compact[compact_id])}) has no vertices."
            )
            continue
        points = mesh.vertices[indices]
        axes, values, direction_confidence = _stable_principal_axes(points)
        if direction_confidence < 0.1:
            warnings.append(
                f"Owner group {compact_id} has low principal-direction confidence."
            )
        islands = _group_topology_islands(indices, mesh.edges)
        if len(islands) > 1:
            warnings.append(
                f"Owner group {compact_id} contains {len(islands)} disconnected topology islands."
            )
        groups.append(
            RigGroup(
                compact_id=compact_id,
                raw_owner_id=int(mesh.raw_by_compact[compact_id]),
                name=mesh.group_names[compact_id],
                vertex_indices=indices,
                vertex_count=int(indices.size),
                centroid=np.mean(points, axis=0),
                median_center=np.median(points, axis=0),
                bounds_min=np.min(points, axis=0),
                bounds_max=np.max(points, axis=0),
                principal_axes=axes,
                principal_values=values,
                principal_direction_confidence=direction_confidence,
                topology_islands=islands,
            )
        )

    if not groups:
        warnings.append("Mesh has no nonempty owned vertex groups.")
    owned_points = mesh.vertices[mesh.compact_owners >= 0]
    normalization_origin = (
        np.median(owned_points, axis=0)
        if owned_points.size else np.zeros(3, dtype=np.float64)
    )
    normalized_centroids = (
        (np.asarray([group.centroid for group in groups], dtype=np.float64) - normalization_origin)
        / characteristic_scale
        if groups else np.empty((0, 3), dtype=np.float64)
    )
    return RigGeometryAnalysis(
        mesh=mesh,
        groups=tuple(groups),
        characteristic_scale=characteristic_scale,
        normalization_origin=normalization_origin,
        normalized_group_centroids=normalized_centroids,
        warnings=tuple(warnings),
    )


def _robust_joint(samples):
    samples = np.asarray(samples, dtype=np.float64)
    center = np.median(samples, axis=0)
    if len(samples) < 3:
        return center, 0.0
    radial = np.linalg.norm(samples - center, axis=1)
    median_radius = float(np.median(radial))
    mad = float(np.median(np.abs(radial - median_radius)))
    if mad > np.finfo(np.float64).eps:
        keep = radial <= median_radius + 3.0 * 1.4826 * mad
        if np.any(keep):
            samples = samples[keep]
            center = np.median(samples, axis=0)
            radial = np.linalg.norm(samples - center, axis=1)
    return center, float(np.median(radial)) if radial.size else 0.0


def build_topology_edge_candidates(analysis):
    """Build primary edge candidates from cross-owner mesh boundaries."""
    mesh = analysis.mesh
    boundary_samples = {}
    boundary_lengths = {}
    for first, second in mesh.edges:
        owner_a = int(mesh.compact_owners[first])
        owner_b = int(mesh.compact_owners[second])
        if owner_a < 0 or owner_b < 0 or owner_a == owner_b:
            continue
        pair = (min(owner_a, owner_b), max(owner_a, owner_b))
        first_pos, second_pos = mesh.vertices[first], mesh.vertices[second]
        boundary_samples.setdefault(pair, []).append((first_pos + second_pos) * 0.5)
        boundary_lengths.setdefault(pair, []).append(float(np.linalg.norm(first_pos - second_pos)))

    group_by_id = {group.compact_id: group for group in analysis.groups}
    candidates = []
    for (group_a, group_b), samples in sorted(boundary_samples.items()):
        if group_a not in group_by_id or group_b not in group_by_id:
            continue
        joint, spread = _robust_joint(samples)
        edge_lengths = np.asarray(boundary_lengths[(group_a, group_b)], dtype=np.float64)
        nearest_distance = float(np.median(edge_lengths))
        normalized_distance = nearest_distance / analysis.characteristic_scale
        normalized_spread = spread / analysis.characteristic_scale
        group_support = min(
            group_by_id[group_a].vertex_count, group_by_id[group_b].vertex_count
        )
        support = len(samples) / max(group_support, 1)
        confidence = float(
            np.clip((1.0 - np.exp(-len(samples) / 3.0)) / (1.0 + normalized_spread), 0.0, 1.0)
        )
        cost_terms = {
            "normalized_boundary_distance": normalized_distance,
            "normalized_boundary_spread": normalized_spread,
            "boundary_support": float(support),
        }
        total_cost = (
            normalized_distance / max(0.25 + confidence, 0.25)
            + normalized_spread * 0.25
            + 1.0 / (1.0 + support) * 0.05
        )
        candidates.append(
            RigEdgeCandidate(
                group_a=group_a,
                group_b=group_b,
                boundary_edge_count=len(samples),
                boundary_joint=joint,
                nearest_distance=nearest_distance,
                cost_terms=cost_terms,
                total_cost=float(total_cost),
                confidence=confidence,
                reason_codes=("TOPOLOGY_BOUNDARY",),
            )
        )
    return tuple(candidates)


def _components(group_ids, candidates):
    adjacency = {group_id: [] for group_id in group_ids}
    for candidate in candidates:
        if candidate.group_a in adjacency and candidate.group_b in adjacency:
            adjacency[candidate.group_a].append(candidate.group_b)
            adjacency[candidate.group_b].append(candidate.group_a)
    components = []
    unseen = set(group_ids)
    while unseen:
        start = min(unseen)
        stack = [start]
        unseen.remove(start)
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in sorted(adjacency[current], reverse=True):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        components.append(tuple(sorted(component)))
    return tuple(components)


def _nearest_region_pair(first_group, second_group, vertices, chunk_size=512):
    first_points = vertices[first_group.vertex_indices]
    second_points = vertices[second_group.vertex_indices]
    best = (np.inf, -1, -1)
    for first_offset in range(0, len(first_points), chunk_size):
        first_chunk = first_points[first_offset:first_offset + chunk_size]
        for second_offset in range(0, len(second_points), chunk_size):
            second_chunk = second_points[second_offset:second_offset + chunk_size]
            delta = first_chunk[:, None, :] - second_chunk[None, :, :]
            squared = np.einsum("ijk,ijk->ij", delta, delta)
            flat_index = int(np.argmin(squared))
            local_first, local_second = np.unravel_index(flat_index, squared.shape)
            candidate = (
                float(squared[local_first, local_second]),
                first_offset + int(local_first),
                second_offset + int(local_second),
            )
            if candidate < best:
                best = candidate
    distance = float(np.sqrt(best[0]))
    first_pos = first_points[best[1]]
    second_pos = second_points[best[2]]
    return distance, (first_pos + second_pos) * 0.5


def _proximity_candidates_for_components(analysis, components):
    group_by_id = {group.compact_id: group for group in analysis.groups}
    candidates = []
    for first_component_index, first_component in enumerate(components):
        for second_component in components[first_component_index + 1:]:
            best = None
            for group_a in first_component:
                for group_b in second_component:
                    distance, joint = _nearest_region_pair(
                        group_by_id[group_a], group_by_id[group_b], analysis.mesh.vertices
                    )
                    key = (distance, group_a, group_b)
                    if best is None or key < best[0]:
                        best = (key, joint)
            (distance, group_a, group_b), joint = best
            normalized_distance = distance / analysis.characteristic_scale
            confidence = float(np.clip(np.exp(-normalized_distance) * 0.35, 0.0, 0.35))
            candidates.append(
                RigEdgeCandidate(
                    group_a=group_a,
                    group_b=group_b,
                    boundary_edge_count=0,
                    boundary_joint=joint,
                    nearest_distance=distance,
                    cost_terms={"normalized_nearest_distance": normalized_distance},
                    total_cost=float(100.0 + normalized_distance),
                    confidence=confidence,
                    reason_codes=("PROXIMITY_FALLBACK",),
                )
            )
    return tuple(candidates)


def _mirror_partner_map(analysis, group_ids):
    """Return deterministic bilateral pairs around the imported X mid-plane."""
    if len(group_ids) < 2:
        return {}, set(group_ids)
    group_by_id = {group.compact_id: group for group in analysis.groups}
    owned = analysis.mesh.vertices[analysis.mesh.compact_owners >= 0]
    center_x = float(np.median(owned[:, 0])) if owned.size else 0.0
    positions = np.asarray([group_by_id[group_id].centroid for group_id in group_ids])
    ranges = np.maximum(np.ptp(positions, axis=0), analysis.characteristic_scale)
    side_margin = max(float(ranges[0]) * 0.04, analysis.characteristic_scale * 0.2)

    positive = [group_id for group_id in group_ids if group_by_id[group_id].centroid[0] - center_x > side_margin]
    negative = [group_id for group_id in group_ids if group_by_id[group_id].centroid[0] - center_x < -side_margin]
    possible = []
    for first in positive:
        first_group = group_by_id[first]
        reflected = first_group.centroid.copy()
        reflected[0] = 2.0 * center_x - reflected[0]
        for second in negative:
            second_group = group_by_id[second]
            delta = np.abs(reflected - second_group.centroid)
            score = (
                delta[0] / max(float(ranges[0]) * 0.08, analysis.characteristic_scale)
                + delta[1] / max(float(ranges[1]) * 0.08, analysis.characteristic_scale)
                + delta[2] / max(float(ranges[2]) * 0.08, analysis.characteristic_scale)
                + abs(np.log(max(first_group.vertex_count, 1) / max(second_group.vertex_count, 1))) * 0.25
            )
            if score <= 2.5:
                possible.append((float(score), first_group.raw_owner_id, second_group.raw_owner_id, first, second))

    partners = {}
    for _score, _first_raw, _second_raw, first, second in sorted(possible):
        if first in partners or second in partners:
            continue
        partners[first] = second
        partners[second] = first
    central = set(group_ids) - set(partners)
    return partners, central


def _proximity_candidate(analysis, first_id, second_id, reason="CENTRAL_PROXIMITY"):
    group_by_id = {group.compact_id: group for group in analysis.groups}
    first_id, second_id = sorted((int(first_id), int(second_id)))
    distance, joint = _nearest_region_pair(
        group_by_id[first_id], group_by_id[second_id], analysis.mesh.vertices
    )
    normalized = distance / analysis.characteristic_scale
    centroid_distance = float(np.linalg.norm(
        group_by_id[first_id].centroid - group_by_id[second_id].centroid
    )) / analysis.characteristic_scale
    confidence = float(np.clip(np.exp(-normalized) * 0.25, 0.0, 0.25))
    return RigEdgeCandidate(
        group_a=first_id,
        group_b=second_id,
        boundary_edge_count=0,
        boundary_joint=joint,
        nearest_distance=distance,
        cost_terms={
            "normalized_nearest_distance": normalized,
            "normalized_centroid_distance": centroid_distance,
        },
        total_cost=float(10.0 + centroid_distance + normalized * 0.25),
        confidence=confidence,
        reason_codes=(reason,),
    )


def _minimum_spanning_forest(group_ids, candidates):
    parent = {group_id: group_id for group_id in group_ids}

    def find(group_id):
        while parent[group_id] != group_id:
            parent[group_id] = parent[parent[group_id]]
            group_id = parent[group_id]
        return group_id

    accepted = []
    ordered = sorted(
        candidates,
        key=lambda edge: (edge.total_cost, edge.group_a, edge.group_b),
    )
    for edge in ordered:
        if edge.group_a not in parent or edge.group_b not in parent:
            continue
        first_root, second_root = find(edge.group_a), find(edge.group_b)
        if first_root == second_root:
            continue
        parent[second_root] = first_root
        accepted.append(edge)
    return tuple(accepted)


def _extend_spanning_forest(group_ids, accepted, candidates):
    parent = {group_id: group_id for group_id in group_ids}

    def find(group_id):
        while parent[group_id] != group_id:
            parent[group_id] = parent[parent[group_id]]
            group_id = parent[group_id]
        return group_id

    result = list(accepted)
    for edge in accepted:
        first_root, second_root = find(edge.group_a), find(edge.group_b)
        if first_root != second_root:
            parent[second_root] = first_root
    for edge in sorted(candidates, key=lambda item: (item.total_cost, item.group_a, item.group_b)):
        first_root, second_root = find(edge.group_a), find(edge.group_b)
        if first_root == second_root:
            continue
        parent[second_root] = first_root
        result.append(edge)
    return tuple(result)


def _anatomical_spanning_forest(analysis, group_ids, topology_candidates):
    """Build a central backbone, then attach each bilateral limb exactly once."""
    group_ids = tuple(sorted(group_ids))
    group_by_id = {group.compact_id: group for group in analysis.groups}
    partners, central_ids = _mirror_partner_map(analysis, group_ids)
    topology_components = _components(group_ids, topology_candidates)
    accepted = []
    synthetic = []
    warnings = []

    for topology_component in topology_components:
        component_set = set(topology_component)
        component_central = sorted(component_set & central_ids)
        component_topology = [
            edge for edge in topology_candidates
            if edge.group_a in component_set and edge.group_b in component_set
        ]
        if not component_central:
            accepted.extend(_minimum_spanning_forest(topology_component, component_topology))
            continue

        # Topological central links remain primary. Complete central proximity
        # links only fill gaps where surface ownership routes through a limb.
        central_candidates = [
            edge for edge in component_topology
            if edge.group_a in central_ids and edge.group_b in central_ids
        ]
        for first_index, first in enumerate(component_central):
            for second in component_central[first_index + 1:]:
                fallback = _proximity_candidate(analysis, first, second)
                central_candidates.append(fallback)
                synthetic.append(fallback)
        accepted.extend(_minimum_spanning_forest(component_central, central_candidates))

        lateral_ids = sorted(component_set - set(component_central))
        lateral_internal = [
            edge for edge in component_topology
            if edge.group_a in lateral_ids
            and edge.group_b in lateral_ids
            and np.sign(group_by_id[edge.group_a].centroid[0] - analysis.normalization_origin[0])
            == np.sign(group_by_id[edge.group_b].centroid[0] - analysis.normalization_origin[0])
        ]
        lateral_components = list(_components(lateral_ids, lateral_internal)) if lateral_ids else []
        component_index = {
            group_id: index
            for index, lateral_component in enumerate(lateral_components)
            for group_id in lateral_component
        }
        for lateral_component in lateral_components:
            accepted.extend(_minimum_spanning_forest(lateral_component, lateral_internal))

        attachment_by_component = {}
        for index, lateral_component in enumerate(lateral_components):
            lateral_set = set(lateral_component)
            attachment_by_component[index] = [
                edge for edge in component_topology
                if (
                    edge.group_a in lateral_set and edge.group_b in component_central
                ) or (
                    edge.group_b in lateral_set and edge.group_a in component_central
                )
            ]

        processed = set()
        for index, lateral_component in enumerate(lateral_components):
            if index in processed:
                continue
            mirrored_indices = {
                component_index[partners[group_id]]
                for group_id in lateral_component
                if group_id in partners and partners[group_id] in component_index
            }
            mirror_index = min(mirrored_indices) if len(mirrored_indices) == 1 else None
            if mirror_index is not None and mirror_index != index and mirror_index not in processed:
                first_edges = attachment_by_component[index]
                second_edges = attachment_by_component[mirror_index]
                first_by_central = {}
                second_by_central = {}
                for edge in first_edges:
                    central = edge.group_b if edge.group_b in component_central else edge.group_a
                    first_by_central.setdefault(central, []).append(edge)
                for edge in second_edges:
                    central = edge.group_b if edge.group_b in component_central else edge.group_a
                    second_by_central.setdefault(central, []).append(edge)
                common = sorted(set(first_by_central) & set(second_by_central))
                if common:
                    choices = []
                    for central in common:
                        first_edge = min(first_by_central[central], key=lambda edge: (edge.total_cost, edge.group_a, edge.group_b))
                        second_edge = min(second_by_central[central], key=lambda edge: (edge.total_cost, edge.group_a, edge.group_b))
                        choices.append((first_edge.total_cost + second_edge.total_cost, group_by_id[central].raw_owner_id, first_edge, second_edge))
                    _cost, _raw, first_edge, second_edge = min(choices, key=lambda item: (item[0], item[1]))
                    accepted.extend((first_edge, second_edge))
                    processed.update((index, mirror_index))
                    continue

            edges = attachment_by_component[index]
            if edges:
                accepted.append(min(edges, key=lambda edge: (edge.total_cost, edge.group_a, edge.group_b)))
            else:
                nearest = min(
                    (_proximity_candidate(analysis, lateral, central, "LIMB_PROXIMITY")
                     for lateral in lateral_component for central in component_central),
                    key=lambda edge: (edge.total_cost, edge.group_a, edge.group_b),
                )
                accepted.append(nearest)
                synthetic.append(nearest)
                warnings.append(
                    f"Lateral owner component {lateral_component} required a proximity attachment."
                )
            processed.add(index)

    accepted = tuple(sorted(
        {(edge.group_a, edge.group_b): edge for edge in accepted}.values(),
        key=lambda edge: (edge.group_a, edge.group_b),
    ))
    return accepted, tuple(synthetic), partners, central_ids, tuple(warnings), topology_components


def _select_component_root(component, accepted, group_by_id, scale, override, allowed_roots=None):
    if override in component:
        return override
    adjacency = {group_id: [] for group_id in component}
    for edge in accepted:
        if edge.group_a in adjacency and edge.group_b in adjacency:
            weight = max(float(edge.total_cost), np.finfo(np.float64).eps)
            adjacency[edge.group_a].append((edge.group_b, weight, edge.confidence))
            adjacency[edge.group_b].append((edge.group_a, weight, edge.confidence))
    positions = np.asarray([group_by_id[group_id].centroid for group_id in component])
    masses = np.asarray([group_by_id[group_id].vertex_count for group_id in component], dtype=np.float64)
    allowed = set(component) if allowed_roots is None else set(component) & set(allowed_roots)
    if not allowed:
        allowed = set(component)
    scores = []
    for local_index, group_id in enumerate(component):
        if group_id not in allowed:
            continue
        shortest = {candidate: np.inf for candidate in component}
        shortest[group_id] = 0.0
        queue = [(0.0, group_id)]
        while queue:
            distance, current = heapq.heappop(queue)
            if distance != shortest[current]:
                continue
            for neighbor, weight, _confidence in adjacency[current]:
                candidate_distance = distance + weight
                if candidate_distance < shortest[neighbor]:
                    shortest[neighbor] = candidate_distance
                    heapq.heappush(queue, (candidate_distance, neighbor))
        graph_centrality = sum(shortest.values()) / max(len(component) - 1, 1)
        geometric_centrality = float(
            np.linalg.norm(positions - positions[local_index], axis=1).sum()
        ) / max(scale * max(len(component) - 1, 1), np.finfo(np.float64).eps)
        degree = len(adjacency[group_id]) / max(len(component) - 1, 1)
        support = sum(edge[2] for edge in adjacency[group_id]) / max(len(component) - 1, 1)
        mass = masses[local_index] / max(float(np.max(masses)), 1.0)
        score = graph_centrality + geometric_centrality * 0.1 - degree * 0.4 - support * 0.2 - mass * 0.25
        scores.append((score, group_by_id[group_id].raw_owner_id, group_id))
    return min(scores)[2]


def _orient_forest(group_ids, accepted, group_by_id, scale, root_override, allowed_roots=None):
    adjacency = {group_id: [] for group_id in group_ids}
    edge_by_pair = {}
    for edge in accepted:
        adjacency[edge.group_a].append(edge.group_b)
        adjacency[edge.group_b].append(edge.group_a)
        edge_by_pair[(edge.group_a, edge.group_b)] = edge
        edge_by_pair[(edge.group_b, edge.group_a)] = edge

    components = _components(group_ids, accepted)
    parents = {group_id: -1 for group_id in group_ids}
    roots = []
    for component in components:
        root = _select_component_root(
            component, accepted, group_by_id, scale, root_override, allowed_roots
        )
        roots.append(root)
        queue = [root]
        visited = {root}
        while queue:
            current = queue.pop(0)
            neighbors = sorted(
                (neighbor for neighbor in adjacency[current] if neighbor not in visited),
                key=lambda neighbor: (
                    edge_by_pair[(current, neighbor)].total_cost,
                    group_by_id[neighbor].raw_owner_id,
                ),
            )
            for neighbor in neighbors:
                visited.add(neighbor)
                parents[neighbor] = current
                queue.append(neighbor)
    return parents, tuple(roots), edge_by_pair


def build_topology_rig_proposal(
    analysis,
    *,
    disconnected_policy="MULTI_ROOT",
    root_override=-1,
):
    """Build a deterministic topology-first static rig proposal."""
    policy = str(disconnected_policy).upper()
    if policy not in {"MULTI_ROOT", "ATTACH_NEAREST", "SKIP", "HOOKS"}:
        raise ValueError(f"Unknown disconnected component policy: {disconnected_policy}")

    warnings = list(analysis.warnings)
    group_by_id = {group.compact_id: group for group in analysis.groups}
    group_ids = tuple(sorted(group_by_id))
    topology_candidates = build_topology_edge_candidates(analysis)
    components = _components(group_ids, topology_candidates)
    skipped = set(range(analysis.mesh.raw_by_compact.size)) - set(group_ids)
    active_ids = group_ids

    if len(components) > 1:
        warnings.append(f"Owner topology contains {len(components)} disconnected components.")
        if policy in {"SKIP", "HOOKS"}:
            main_component = max(
                components,
                key=lambda component: (
                    sum(group_by_id[group_id].vertex_count for group_id in component),
                    -min(group_by_id[group_id].raw_owner_id for group_id in component),
                ),
            )
            active_ids = tuple(main_component)
            skipped.update(set(group_ids) - set(active_ids))
            if policy == "HOOKS":
                warnings.append("HOOKS policy preserves detached groups for a later Blender hook adapter.")

    active_topology = tuple(
        candidate for candidate in topology_candidates
        if candidate.group_a in active_ids and candidate.group_b in active_ids
    )
    accepted, anatomical_candidates, mirror_partners, central_ids, anatomical_warnings, active_components = (
        _anatomical_spanning_forest(analysis, active_ids, active_topology)
    )
    warnings.extend(anatomical_warnings)
    candidates = list(active_topology) + list(anatomical_candidates)
    if policy == "ATTACH_NEAREST" and len(active_components) > 1:
        component_bridges = _proximity_candidates_for_components(analysis, active_components)
        candidates.extend(component_bridges)
        accepted = _extend_spanning_forest(active_ids, accepted, component_bridges)

    parents, roots, edge_by_pair = _orient_forest(
        active_ids,
        accepted,
        group_by_id,
        analysis.characteristic_scale,
        root_override,
        allowed_roots=central_ids,
    )

    group_count = analysis.mesh.raw_by_compact.size
    parent_by_group = np.full(group_count, -1, dtype=np.int32)
    heads = np.zeros((group_count, 3), dtype=np.float64)
    tails = np.zeros((group_count, 3), dtype=np.float64)
    roll_references = np.zeros((group_count, 3), dtype=np.float64)
    children = {group_id: [] for group_id in active_ids}
    for group_id, parent_id in parents.items():
        parent_by_group[group_id] = parent_id
        if parent_id >= 0:
            children[parent_id].append(group_id)
            heads[group_id] = edge_by_pair[(parent_id, group_id)].boundary_joint
        else:
            heads[group_id] = group_by_id[group_id].centroid

    for group_id in active_ids:
        group = group_by_id[group_id]
        roll_references[group_id] = group.principal_axes[1]
        if children[group_id]:
            continuation = min(
                children[group_id],
                key=lambda child: (
                    0 if child in central_ids else 1,
                    -edge_by_pair[(group_id, child)].confidence,
                    group_by_id[child].raw_owner_id,
                ),
            )
            tails[group_id] = heads[continuation]
        else:
            direction = group.principal_axes[0].copy()
            parent_id = parents[group_id]
            if parent_id >= 0:
                away = group.centroid - heads[group_id]
                if np.dot(direction, away) < 0:
                    direction *= -1.0
            extent = max(
                float(np.ptp(analysis.mesh.vertices[group.vertex_indices] @ direction)),
                analysis.characteristic_scale * 0.05,
            )
            tails[group_id] = heads[group_id] + direction * extent

    return RigProposal(
        groups=analysis.groups,
        parent_by_group=parent_by_group,
        head_by_group=heads,
        tail_by_group=tails,
        roll_reference_by_group=roll_references,
        root_groups=roots,
        edge_candidates=tuple(sorted(candidates, key=lambda edge: (edge.group_a, edge.group_b))),
        accepted_edges=tuple((edge.group_a, edge.group_b) for edge in accepted),
        skipped_groups=tuple(sorted(skipped)),
        warnings=tuple(warnings),
        settings={
            "algorithm": "TOPOLOGY",
            "disconnected_policy": policy,
            "root_override": int(root_override),
            "mirror_pair_count": len(mirror_partners) // 2,
            "mirror_pairs": [
                [group_by_id[first].raw_owner_id, group_by_id[second].raw_owner_id]
                for first, second in sorted(mirror_partners.items())
                if first < second
            ],
            "central_group_count": len(central_ids),
        },
        algorithm_version=2,
    )


def raw_ids_from_metadata(value):
    """Read and validate raw IDs from mapping metadata.

    Returns ``None`` for missing, malformed, unsupported, or ambiguous data.
    """
    if not value:
        return None
    try:
        payload = json.loads(value) if isinstance(value, str) else dict(value)
        if int(payload.get("schema_version", -1)) != OWNER_MAPPING_SCHEMA_VERSION:
            return None
        raw_ids = np.asarray(payload["raw_by_compact"], dtype=np.int32).reshape(-1)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None

    if np.any(raw_ids < 0) or np.unique(raw_ids).size != raw_ids.size:
        return None
    return raw_ids
