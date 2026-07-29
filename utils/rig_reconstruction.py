"""Pure data structures and algorithms used by CAR rig reconstruction.

This module intentionally has no Blender dependencies so owner handling and future
rig proposal analysis can be tested outside Blender.
"""

from dataclasses import dataclass
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
