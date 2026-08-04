"""Pure data structures and algorithms used by CAR rig reconstruction.

This module intentionally has no Blender dependencies so owner handling and future
rig proposal analysis can be tested outside Blender.
"""

from dataclasses import dataclass
import heapq
import json

import numpy as np


OWNER_MAPPING_SCHEMA_VERSION = 2
PROPOSAL_SCHEMA_VERSION = 1
OWNER_MAPPING_PROPERTY = "carnivores_owner_mapping"
UNOWNED_COMPACT_ID = -1


def _json_array(value):
    return np.asarray(value).tolist()


def _proposal_edge_payload(edge):
    return {
        "group_a": int(edge.group_a),
        "group_b": int(edge.group_b),
        "boundary_edge_count": int(edge.boundary_edge_count),
        "boundary_joint": _json_array(edge.boundary_joint),
        "nearest_distance": float(edge.nearest_distance),
        "cost_terms": {
            str(key): float(value) for key, value in edge.cost_terms.items()
        },
        "total_cost": float(edge.total_cost),
        "confidence": float(edge.confidence),
        "reason_codes": [str(code) for code in edge.reason_codes],
    }


def _proposal_group_payload(group, include_membership=True):
    return {
        "compact_id": int(group.compact_id),
        "raw_owner_id": int(group.raw_owner_id),
        "name": str(group.name),
        "vertex_indices": _json_array(group.vertex_indices) if include_membership else [],
        "vertex_count": int(group.vertex_count),
        "centroid": _json_array(group.centroid),
        "median_center": _json_array(group.median_center),
        "bounds_min": _json_array(group.bounds_min),
        "bounds_max": _json_array(group.bounds_max),
        "principal_axes": _json_array(group.principal_axes),
        "principal_values": _json_array(group.principal_values),
        "principal_direction_confidence": float(group.principal_direction_confidence),
        "topology_islands": (
            [_json_array(island) for island in group.topology_islands]
            if include_membership else []
        ),
    }


def serialize_rig_proposal(proposal, compact=False):
    """Return a deterministic JSON-safe representation of a rig proposal."""
    return {
        "schema_version": PROPOSAL_SCHEMA_VERSION,
        "storage_detail": "COMPACT" if compact else "FULL",
        "algorithm_version": int(proposal.algorithm_version),
        "groups": [
            _proposal_group_payload(group, include_membership=not compact)
            for group in proposal.groups
        ],
        "parent_by_group": _json_array(proposal.parent_by_group),
        "head_by_group": _json_array(proposal.head_by_group),
        "tail_by_group": _json_array(proposal.tail_by_group),
        "roll_reference_by_group": _json_array(proposal.roll_reference_by_group),
        "root_groups": [int(group_id) for group_id in proposal.root_groups],
        "edge_candidates": [
            _proposal_edge_payload(edge) for edge in proposal.edge_candidates
        ],
        "accepted_edges": [
            [int(first), int(second)] for first, second in proposal.accepted_edges
        ],
        "skipped_groups": [int(group_id) for group_id in proposal.skipped_groups],
        "warnings": [str(warning) for warning in proposal.warnings],
        "settings": json.loads(json.dumps(proposal.settings, sort_keys=True, default=str)),
    }


def rig_proposal_to_metadata(proposal, compact=False):
    """Serialize a rig proposal for Blender storage or test round-trips."""
    return json.dumps(
        serialize_rig_proposal(proposal, compact=compact),
        separators=(",", ":"),
        sort_keys=True,
    )


def deserialize_rig_proposal(value):
    """Decode and validate a serialized rig proposal before Blender uses it."""
    def array(raw, name, dtype, shape=None, finite=False):
        result = np.asarray(raw, dtype=dtype)
        if shape is not None and result.shape != shape:
            raise ValueError(f"{name} has shape {result.shape}; expected {shape}.")
        if finite and result.size and not np.isfinite(result).all():
            raise ValueError(f"{name} contains non-finite values.")
        return result

    try:
        payload = json.loads(value) if isinstance(value, str) else dict(value)
        if not isinstance(payload, dict):
            raise ValueError("Proposal payload must be an object.")
        if int(payload.get("schema_version", -1)) != PROPOSAL_SCHEMA_VERSION:
            raise ValueError("Unsupported rig proposal schema.")

        groups = []
        for raw_group in payload["groups"]:
            if not isinstance(raw_group, dict):
                raise ValueError("Proposal group entry must be an object.")
            vertex_indices = array(
                raw_group["vertex_indices"], "group vertex_indices", np.int32
            )
            if vertex_indices.ndim != 1 or np.any(vertex_indices < 0):
                raise ValueError("Group vertex_indices must be a nonnegative vector.")
            topology_islands = []
            for island in raw_group["topology_islands"]:
                island_array = array(island, "topology island", np.int32)
                if island_array.ndim != 1 or np.any(island_array < 0):
                    raise ValueError("Topology islands must be nonnegative vectors.")
                topology_islands.append(island_array)
            groups.append(
                RigGroup(
                    compact_id=int(raw_group["compact_id"]),
                    raw_owner_id=int(raw_group["raw_owner_id"]),
                    name=str(raw_group["name"]),
                    vertex_indices=vertex_indices,
                    vertex_count=int(raw_group["vertex_count"]),
                    centroid=array(raw_group["centroid"], "group centroid", np.float64, (3,), True),
                    median_center=array(raw_group["median_center"], "group median_center", np.float64, (3,), True),
                    bounds_min=array(raw_group["bounds_min"], "group bounds_min", np.float64, (3,), True),
                    bounds_max=array(raw_group["bounds_max"], "group bounds_max", np.float64, (3,), True),
                    principal_axes=array(raw_group["principal_axes"], "group principal_axes", np.float64, (3, 3), True),
                    principal_values=array(raw_group["principal_values"], "group principal_values", np.float64, (3,), True),
                    principal_direction_confidence=float(
                        raw_group["principal_direction_confidence"]
                    ),
                    topology_islands=tuple(topology_islands),
                )
            )

        compact_ids = [group.compact_id for group in groups]
        parent_by_group = array(
            payload["parent_by_group"], "parent_by_group", np.int32
        )
        if parent_by_group.ndim != 1:
            raise ValueError("parent_by_group must be a vector.")
        group_count = len(parent_by_group)
        if (
            any(group_id < 0 or group_id >= group_count for group_id in compact_ids)
            or len(set(compact_ids)) != len(compact_ids)
        ):
            raise ValueError("Proposal group compact IDs are out of range or duplicated.")
        if len({group.raw_owner_id for group in groups}) != len(groups):
            raise ValueError("Proposal groups must have unique raw owner IDs.")
        if any(group.vertex_count < 0 for group in groups):
            raise ValueError("Proposal group vertex counts cannot be negative.")

        if np.any(parent_by_group < -1) or np.any(parent_by_group >= group_count):
            raise ValueError("Proposal parent indices are out of range.")
        if any(int(parent) >= 0 and int(parent) not in compact_ids for parent in parent_by_group):
            raise ValueError("Proposal parent indices must reference nonempty groups.")
        if np.any(parent_by_group == np.arange(group_count, dtype=np.int32)):
            raise ValueError("A proposal group cannot parent itself.")
        head_by_group = array(
            payload["head_by_group"], "head_by_group", np.float64, (group_count, 3), True
        )
        tail_by_group = array(
            payload["tail_by_group"], "tail_by_group", np.float64, (group_count, 3), True
        )
        roll_reference_by_group = array(
            payload["roll_reference_by_group"],
            "roll_reference_by_group",
            np.float64,
            (group_count, 3),
            True,
        )

        edges = []
        seen_edge_pairs = set()
        for raw_edge in payload["edge_candidates"]:
            if not isinstance(raw_edge, dict):
                raise ValueError("Proposal edge entry must be an object.")
            group_a = int(raw_edge["group_a"])
            group_b = int(raw_edge["group_b"])
            pair = proposal_edge_key(group_a, group_b)
            if group_a not in compact_ids or group_b not in compact_ids:
                raise ValueError("Proposal edge references an invalid group.")
            if group_a == group_b:
                raise ValueError("Proposal edge cannot connect a group to itself.")
            if pair in seen_edge_pairs:
                raise ValueError("Proposal contains duplicate edge candidates.")
            seen_edge_pairs.add(pair)
            cost_terms = raw_edge["cost_terms"]
            if not isinstance(cost_terms, dict):
                raise ValueError("Proposal edge cost_terms must be an object.")
            cost_terms = {str(key): float(item) for key, item in cost_terms.items()}
            if any(not np.isfinite(item) for item in cost_terms.values()):
                raise ValueError("Proposal edge cost_terms contain non-finite values.")
            edges.append(
                RigEdgeCandidate(
                    group_a=group_a,
                    group_b=group_b,
                    boundary_edge_count=int(raw_edge["boundary_edge_count"]),
                    boundary_joint=array(
                        raw_edge["boundary_joint"], "boundary_joint", np.float64, (3,), True
                    ),
                    nearest_distance=float(raw_edge["nearest_distance"]),
                    cost_terms=cost_terms,
                    total_cost=float(raw_edge["total_cost"]),
                    confidence=float(raw_edge["confidence"]),
                    reason_codes=tuple(str(code) for code in raw_edge["reason_codes"]),
                )
            )
        if any(
            value < 0 or not np.isfinite(value)
            for edge in edges
            for value in (edge.boundary_edge_count, edge.nearest_distance, edge.total_cost, edge.confidence)
        ):
            raise ValueError("Proposal edge metrics are invalid.")

        roots = tuple(int(group_id) for group_id in payload["root_groups"])
        skipped = tuple(int(group_id) for group_id in payload["skipped_groups"])
        if any(group_id < 0 or group_id >= group_count for group_id in roots + skipped):
            raise ValueError("Proposal roots or skipped groups are out of range.")
        if any(group_id not in compact_ids for group_id in roots):
            raise ValueError("Proposal roots must reference nonempty groups.")
        accepted = tuple(
            proposal_edge_key(first, second)
            for first, second in payload["accepted_edges"]
        )
        edge_pairs = {proposal_edge_key(edge.group_a, edge.group_b) for edge in edges}
        if any(first == second or (first, second) not in edge_pairs for first, second in accepted):
            raise ValueError("Proposal accepted_edges contains an unknown or self edge.")
        if len(set(accepted)) != len(accepted):
            raise ValueError("Proposal accepted_edges contains duplicates.")
        union_parent = {int(group_id): int(group_id) for group_id in compact_ids}

        def union_find(group_id):
            while union_parent[group_id] != group_id:
                union_parent[group_id] = union_parent[union_parent[group_id]]
                group_id = union_parent[group_id]
            return group_id

        for first, second in accepted:
            first_root, second_root = union_find(first), union_find(second)
            if first_root == second_root:
                raise ValueError("Proposal accepted_edges contains a cycle.")
            union_parent[second_root] = first_root
        for group_id in compact_ids:
            chain = set()
            current = int(group_id)
            while current >= 0:
                if current in chain:
                    raise ValueError("Proposal parent_by_group contains a cycle.")
                chain.add(current)
                current = int(parent_by_group[current])
        settings = payload.get("settings", {})
        if not isinstance(settings, dict):
            raise ValueError("Proposal settings must be an object.")
        settings = dict(settings)
        for decision_key in ("forced_edges", "rejected_edges"):
            decisions = settings.get(decision_key, [])
            if not isinstance(decisions, list):
                raise ValueError(f"Proposal {decision_key} must be a list.")
            normalized_decisions = []
            for decision in decisions:
                if not isinstance(decision, (list, tuple)) or len(decision) != 2:
                    raise ValueError(f"Proposal {decision_key} contains an invalid edge.")
                pair = proposal_edge_key(*decision)
                if pair[0] not in compact_ids or pair[1] not in compact_ids:
                    raise ValueError(f"Proposal {decision_key} references an invalid group.")
                normalized_decisions.append([int(pair[0]), int(pair[1])])
            settings[decision_key] = normalized_decisions

        return RigProposal(
            groups=tuple(groups),
            parent_by_group=parent_by_group,
            head_by_group=head_by_group,
            tail_by_group=tail_by_group,
            roll_reference_by_group=roll_reference_by_group,
            root_groups=roots,
            edge_candidates=tuple(edges),
            accepted_edges=accepted,
            skipped_groups=tuple(sorted(set(skipped))),
            warnings=tuple(str(warning) for warning in payload["warnings"]),
            settings=dict(settings),
            algorithm_version=int(payload.get("algorithm_version", 1)),
        )
    except (TypeError, ValueError, KeyError, IndexError, OverflowError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid rig proposal metadata: {exc}") from exc


def rig_proposal_from_metadata(value):
    return deserialize_rig_proposal(value)


def mesh_analysis_checksum(mesh_input):
    """Return a stable checksum for all source data used by rig analysis."""
    import hashlib

    digest = hashlib.sha256()
    for name, array in (
        ("vertices", mesh_input.vertices),
        ("triangles", mesh_input.triangles),
        ("edges", mesh_input.edges),
        ("compact_owners", mesh_input.compact_owners),
        ("raw_by_compact", mesh_input.raw_by_compact),
    ):
        normalized = np.ascontiguousarray(np.asarray(array))
        digest.update(name.encode("utf-8"))
        digest.update(str(normalized.dtype).encode("ascii"))
        digest.update(repr(tuple(normalized.shape)).encode("ascii"))
        digest.update(normalized.tobytes())
    for name in mesh_input.group_names:
        encoded = str(name).encode("utf-8")
        digest.update(len(encoded).to_bytes(4, "little"))
        digest.update(encoded)
    return digest.hexdigest()


def proposal_edge_key(first, second):
    return tuple(sorted((int(first), int(second))))


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


def _normalize_side_axis(side_axis):
    if isinstance(side_axis, str):
        axis = {"X": 0, "Y": 1, "Z": 2}.get(side_axis.upper())
    else:
        try:
            axis = int(side_axis)
        except (TypeError, ValueError):
            axis = None
    if axis not in (0, 1, 2):
        raise ValueError(f"Unknown side axis: {side_axis}")
    return axis


def _side_coordinate(position, center, side_axis, side_inverted=False):
    value = float(np.asarray(position)[side_axis] - center)
    return -value if side_inverted else value


def _deterministic_roll_reference(analysis, side_axis=0):
    """Return a stable lateral roll reference aligned to ``side_axis``."""
    axis = _normalize_side_axis(side_axis)
    owned = analysis.mesh.vertices[analysis.mesh.compact_owners >= 0]
    if len(owned) < 3:
        reference = np.zeros(3, dtype=np.float64)
        reference[axis] = 1.0
        return reference
    centered = owned - np.mean(owned, axis=0)
    try:
        _, _, axes = np.linalg.svd(centered, full_matrices=False)
        thin = np.asarray(axes[-1], dtype=np.float64)
    except np.linalg.LinAlgError:
        thin = np.zeros(3, dtype=np.float64)
        thin[axis] = 1.0

    norm = float(np.linalg.norm(thin))
    if not np.isfinite(norm) or norm <= np.finfo(np.float64).eps:
        thin = np.zeros(3, dtype=np.float64)
        thin[axis] = 1.0
    else:
        thin /= norm
    other_axes = [index for index in range(3) if index != axis]
    if abs(thin[axis]) < max(abs(thin[index]) for index in other_axes):
        thin = np.zeros(3, dtype=np.float64)
        thin[axis] = 1.0
    if thin[axis] < 0.0:
        thin *= -1.0
    return thin


def _mirror_partner_map(analysis, group_ids, side_axis=0, side_inverted=False):
    """Return deterministic bilateral pairs around the selected mid-plane."""
    axis = _normalize_side_axis(side_axis)
    if len(group_ids) < 2:
        return {}, set(group_ids)
    group_by_id = {group.compact_id: group for group in analysis.groups}
    owned = analysis.mesh.vertices[analysis.mesh.compact_owners >= 0]
    center = float(np.median(owned[:, axis])) if owned.size else 0.0
    positions = np.asarray([group_by_id[group_id].centroid for group_id in group_ids])
    ranges = np.maximum(np.ptp(positions, axis=0), analysis.characteristic_scale)
    side_margin = max(float(ranges[axis]) * 0.04, analysis.characteristic_scale * 0.2)

    positive = [
        group_id for group_id in group_ids
        if _side_coordinate(group_by_id[group_id].centroid, center, axis, side_inverted) > side_margin
    ]
    negative = [
        group_id for group_id in group_ids
        if _side_coordinate(group_by_id[group_id].centroid, center, axis, side_inverted) < -side_margin
    ]
    possible = []
    for first in positive:
        first_group = group_by_id[first]
        reflected = first_group.centroid.copy()
        reflected[axis] = 2.0 * center - reflected[axis]
        for second in negative:
            second_group = group_by_id[second]
            delta = np.abs(reflected - second_group.centroid)
            score = (
                delta[axis] / max(float(ranges[axis]) * 0.08, analysis.characteristic_scale)
                + sum(
                    delta[index] / max(float(ranges[index]) * 0.08, analysis.characteristic_scale)
                    for index in range(3) if index != axis
                )
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


def _anatomical_spanning_forest(
    analysis,
    group_ids,
    topology_candidates,
    *,
    side_axis=0,
    side_inverted=False,
):
    """Build a central backbone, then attach each bilateral limb exactly once."""
    group_ids = tuple(sorted(group_ids))
    group_by_id = {group.compact_id: group for group in analysis.groups}
    partners, central_ids = _mirror_partner_map(
        analysis,
        group_ids,
        side_axis=side_axis,
        side_inverted=side_inverted,
    )
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
            and np.sign(_side_coordinate(
                group_by_id[edge.group_a].centroid,
                analysis.normalization_origin[side_axis],
                side_axis,
                side_inverted,
            ))
            == np.sign(_side_coordinate(
                group_by_id[edge.group_b].centroid,
                analysis.normalization_origin[side_axis],
                side_axis,
                side_inverted,
            ))
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


def _select_component_root(
    component,
    accepted,
    group_by_id,
    scale,
    override,
    allowed_roots=None,
    diagnostics=None,
):
    if override in component:
        if diagnostics is not None:
            diagnostics.extend(
                {
                    "compact_id": int(group_id),
                    "raw_owner_id": int(group_by_id[group_id].raw_owner_id),
                    "score": None,
                    "reason": "ROOT_OVERRIDE",
                    "selected": group_id == override,
                }
                for group_id in sorted(component)
            )
        return override
    adjacency = {group_id: [] for group_id in component}
    supported_degree = {group_id: 0 for group_id in component}
    for edge in accepted:
        if edge.group_a in adjacency and edge.group_b in adjacency:
            weight = max(float(edge.total_cost), np.finfo(np.float64).eps)
            adjacency[edge.group_a].append((edge.group_b, weight, edge.confidence))
            adjacency[edge.group_b].append((edge.group_a, weight, edge.confidence))
            if edge.boundary_edge_count > 0:
                supported_degree[edge.group_a] += 1
                supported_degree[edge.group_b] += 1
    positions = np.asarray([group_by_id[group_id].centroid for group_id in component])
    masses = np.asarray([group_by_id[group_id].vertex_count for group_id in component], dtype=np.float64)
    requested_allowed = (
        set(component) if allowed_roots is None
        else set(component) & set(allowed_roots)
    )
    allowed = set(requested_allowed)
    if not allowed:
        # Detached components may contain no central owner. Keep the existing
        # geometric fallback, but retain that fact in the diagnostics instead
        # of labeling every fallback candidate NOT_ALLOWED.
        allowed = set(component)

    # CAR owner IDs usually retain source bone ordering even though the file no
    # longer contains hierarchy records. Prefer the earliest central owner that
    # is demonstrably inside the supported topology rather than an endpoint.
    # This prevents tiny, late belly/control groups from winning purely because
    # they happen to be graph-central. Endpoint chains still use geometric and
    # graph scoring below.
    source_ordered_backbone = [
        group_id for group_id in allowed
        if supported_degree[group_id] >= 2
    ]
    if source_ordered_backbone:
        chosen = min(
            source_ordered_backbone,
            key=lambda group_id: (group_by_id[group_id].raw_owner_id, group_id),
        )
        if diagnostics is not None:
            diagnostics.extend(
                {
                    "compact_id": int(group_id),
                    "raw_owner_id": int(group_by_id[group_id].raw_owner_id),
                    "score": None,
                    "reason": (
                        "NOT_ALLOWED"
                        if allowed_roots is not None
                        and requested_allowed
                        and group_id not in requested_allowed
                        else "NO_ALLOWED_ROOT_IN_COMPONENT"
                        if allowed_roots is not None and not requested_allowed
                        else "SOURCE_ORDERED_BACKBONE"
                        if group_id in source_ordered_backbone
                        else "SOURCE_ORDERED_NON_BACKBONE"
                    ),
                    "supported_degree": int(supported_degree[group_id]),
                    "selected": group_id == chosen,
                }
                for group_id in sorted(component)
            )
        return chosen

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
    chosen = min(scores)[2]
    if diagnostics is not None:
        score_by_group = {
            int(group_id): (float(score), int(raw_owner_id))
            for score, raw_owner_id, group_id in scores
        }
        diagnostics.extend(
            {
                "compact_id": int(group_id),
                "raw_owner_id": int(group_by_id[group_id].raw_owner_id),
                "score": score_by_group[group_id][0] if group_id in score_by_group else None,
                "reason": (
                    "SCORED"
                    if group_id in score_by_group
                    else "NOT_ALLOWED"
                    if allowed_roots is not None and requested_allowed
                    else "NO_ALLOWED_ROOT_IN_COMPONENT"
                ),
                "selected": group_id == chosen,
            }
            for group_id in sorted(component)
        )
    return chosen


def _orient_forest(
    group_ids,
    accepted,
    group_by_id,
    scale,
    root_override,
    allowed_roots=None,
    root_diagnostics=None,
):
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
        component_diagnostics = []
        root = _select_component_root(
            component,
            accepted,
            group_by_id,
            scale,
            root_override,
            allowed_roots,
            diagnostics=component_diagnostics,
        )
        if root_diagnostics is not None:
            root_diagnostics.append({
                "compact_ids": [int(group_id) for group_id in component],
                "candidates": component_diagnostics,
            })
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


def _apply_edge_overrides(group_ids, accepted, candidates, forced_edges, rejected_edges, warnings):
    """Apply explicit edge decisions while preserving a valid spanning forest."""
    group_ids = set(int(group_id) for group_id in group_ids)
    forced = {proposal_edge_key(*edge) for edge in (forced_edges or ())}
    rejected = {proposal_edge_key(*edge) for edge in (rejected_edges or ())}
    candidate_by_pair = {}
    for candidate in candidates:
        pair = proposal_edge_key(candidate.group_a, candidate.group_b)
        if pair not in candidate_by_pair or (
            candidate.total_cost,
            candidate.group_a,
            candidate.group_b,
        ) < (
            candidate_by_pair[pair].total_cost,
            candidate_by_pair[pair].group_a,
            candidate_by_pair[pair].group_b,
        ):
            candidate_by_pair[pair] = candidate

    selected = []
    parent = {group_id: group_id for group_id in group_ids}

    def find(group_id):
        while parent[group_id] != group_id:
            parent[group_id] = parent[parent[group_id]]
            group_id = parent[group_id]
        return group_id

    def add(candidate, label):
        first_root, second_root = find(candidate.group_a), find(candidate.group_b)
        if first_root == second_root:
            warnings.append(
                f"{label} edge {candidate.group_a}-{candidate.group_b} would create a cycle; ignored."
            )
            return False
        parent[second_root] = first_root
        selected.append(candidate)
        return True

    for pair in sorted(forced):
        candidate = candidate_by_pair.get(pair)
        if candidate is None:
            warnings.append(f"Forced edge {pair[0]}-{pair[1]} is not a proposal candidate; ignored.")
            continue
        if pair in rejected:
            warnings.append(f"Edge {pair[0]}-{pair[1]} is both forced and rejected; rejection wins.")
            continue
        if pair[0] not in group_ids or pair[1] not in group_ids:
            warnings.append(f"Forced edge {pair[0]}-{pair[1]} references an inactive group; ignored.")
            continue
        add(candidate, "Forced")

    for candidate in sorted(accepted, key=lambda edge: (edge.group_a, edge.group_b)):
        pair = proposal_edge_key(candidate.group_a, candidate.group_b)
        if pair in rejected or pair in forced:
            continue
        add(candidate, "Accepted")

    selected_pairs = {proposal_edge_key(edge.group_a, edge.group_b) for edge in selected}
    for candidate in sorted(candidates, key=lambda edge: (edge.total_cost, edge.group_a, edge.group_b)):
        pair = proposal_edge_key(candidate.group_a, candidate.group_b)
        if pair in rejected or pair in forced or pair in selected_pairs:
            continue
        if add(candidate, "Repair"):
            selected_pairs.add(pair)

    return tuple(sorted(selected, key=lambda edge: (edge.group_a, edge.group_b)))


def build_topology_rig_proposal(
    analysis,
    *,
    disconnected_policy="MULTI_ROOT",
    root_override=-1,
    side_axis=0,
    side_inverted=False,
    forced_edges=(),
    rejected_edges=(),
):
    """Build a deterministic topology-first static rig proposal."""
    policy = str(disconnected_policy).upper()
    if policy not in {"MULTI_ROOT", "ATTACH_NEAREST", "SKIP", "HOOKS"}:
        raise ValueError(f"Unknown disconnected component policy: {disconnected_policy}")
    side_axis = _normalize_side_axis(side_axis)
    side_inverted = bool(side_inverted)

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

    if root_override >= 0 and root_override not in active_ids:
        warnings.append(
            f"Root override {root_override} is skipped or not present; automatic root selection used."
        )

    active_topology = tuple(
        candidate for candidate in topology_candidates
        if candidate.group_a in active_ids and candidate.group_b in active_ids
    )
    accepted, anatomical_candidates, mirror_partners, central_ids, anatomical_warnings, active_components = (
        _anatomical_spanning_forest(
            analysis,
            active_ids,
            active_topology,
            side_axis=side_axis,
            side_inverted=side_inverted,
        )
    )
    warnings.extend(anatomical_warnings)
    candidates = list(active_topology) + list(anatomical_candidates)
    if policy == "ATTACH_NEAREST" and len(active_components) > 1:
        component_bridges = _proximity_candidates_for_components(analysis, active_components)
        candidates.extend(component_bridges)
        accepted = _extend_spanning_forest(active_ids, accepted, component_bridges)

    if forced_edges or rejected_edges:
        accepted = _apply_edge_overrides(
            active_ids,
            accepted,
            candidates,
            forced_edges,
            rejected_edges,
            warnings,
        )
        if len(_components(active_ids, accepted)) > 1 and policy == "ATTACH_NEAREST":
            warnings.append("Edge overrides left disconnected topology components.")

    root_diagnostics = []
    parents, roots, edge_by_pair = _orient_forest(
        active_ids,
        accepted,
        group_by_id,
        analysis.characteristic_scale,
        root_override,
        allowed_roots=central_ids,
        root_diagnostics=root_diagnostics,
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
            # A median/MAD-filtered center is less sensitive to detached
            # vertices than either the ordinary centroid or a raw median.
            root_points = analysis.mesh.vertices[group_by_id[group_id].vertex_indices]
            heads[group_id], _ = _robust_joint(root_points)

    owned_vertices = analysis.mesh.vertices[analysis.mesh.compact_owners >= 0]
    center_side = float(np.median(owned_vertices[:, side_axis])) if owned_vertices.size else 0.0
    side_span = float(np.ptp(owned_vertices[:, side_axis])) if owned_vertices.size else 0.0
    midline_margin = max(side_span * 0.04, analysis.characteristic_scale * 0.2)

    root_by_group = {}
    for group_id in active_ids:
        ancestor = group_id
        while parents[ancestor] >= 0:
            ancestor = parents[ancestor]
        root_by_group[group_id] = ancestor

    active_positions = np.asarray(
        [group_by_id[group_id].median_center for group_id in active_ids],
        dtype=np.float64,
    )
    body_axes, _, _ = _stable_principal_axes(active_positions)
    body_axis = np.asarray(body_axes[0], dtype=np.float64)
    body_axis_norm = float(np.linalg.norm(body_axis))
    if body_axis_norm <= np.finfo(np.float64).eps:
        body_axis = np.array([0.0, 1.0, 0.0], dtype=np.float64)
    else:
        body_axis /= body_axis_norm
    centrality_scale = max(analysis.characteristic_scale, np.finfo(np.float64).eps)

    # Deterministic global lateral roll reference (the model's thinnest axis),
    # used for every group so mirrored limbs receive mirrored rolls. Per-group
    # secondary PCA axes are unreliable for small/degenerate groups.
    global_roll_reference = _deterministic_roll_reference(analysis, side_axis=side_axis)
    for group_id in active_ids:
        group = group_by_id[group_id]
        reference = global_roll_reference.copy()
        if _side_coordinate(
            group.centroid,
            analysis.normalization_origin[side_axis],
            side_axis,
            side_inverted,
        ) < 0.0:
            reference[side_axis] *= -1.0
        roll_references[group_id] = reference
        if children[group_id]:
            parent_center = group_by_id[group_id].median_center

            def continuation_key(child):
                child_group = group_by_id[child]
                child_direction = child_group.median_center - parent_center
                child_length = float(np.linalg.norm(child_direction))
                axis_alignment = (
                    abs(float(np.dot(child_direction, body_axis)) / child_length)
                    if child_length > np.finfo(np.float64).eps else 0.0
                )
                centrality = 1.0 - min(
                    abs(float(
                        _side_coordinate(
                            child_group.median_center,
                            center_side,
                            side_axis,
                            side_inverted,
                        )
                    )) / centrality_scale,
                    1.0,
                )
                name = child_group.name.lower()
                semantic_backbone = 1.0 if any(
                    token in name for token in ("root", "pelvis", "hip", "spine", "torso", "body", "neck", "head")
                ) else 0.0
                confidence = float(edge_by_pair[(group_id, child)].confidence)
                score = (
                    axis_alignment * 0.40
                    + centrality * 0.25
                    + confidence * 0.25
                    + semantic_backbone * 0.10
                    + (0.05 if child in central_ids else 0.0)
                )
                return (-score, child_group.raw_owner_id)

            continuation = min(children[group_id], key=continuation_key)
            tails[group_id] = heads[continuation]
        else:
            direction = group.principal_axes[0].copy()
            principal_extent = float(np.ptp(
                analysis.mesh.vertices[group.vertex_indices] @ direction
            ))
            parent_id = parents[group_id]
            lateral_pca = abs(direction[side_axis]) > max(
                abs(direction[index]) for index in range(3) if index != side_axis
            )
            on_midline = (
                abs(_side_coordinate(
                    group.centroid,
                    center_side,
                    side_axis,
                    side_inverted,
                )) <= midline_margin
                or (
                    group.bounds_min[side_axis] <= center_side <= group.bounds_max[side_axis]
                )
            )
            if parent_id >= 0 and group_id in central_ids and on_midline and lateral_pca:
                # Midline eye, belly, and other floating controls often span X,
                # making PCA produce an arbitrary lateral tail. Follow the
                # imported model's longitudinal Blender Y flow instead.
                root_center = group_by_id[root_by_group[group_id]].centroid
                longitudinal_offset = float(group.centroid[1] - root_center[1])
                if abs(longitudinal_offset) <= analysis.characteristic_scale * 1e-8:
                    longitudinal_offset = float(
                        group.centroid[1] - analysis.normalization_origin[1]
                    )
                direction = np.array(
                    [0.0, -1.0 if longitudinal_offset < 0.0 else 1.0, 0.0],
                    dtype=np.float64,
                )
            elif parent_id >= 0:
                away = group.centroid - heads[group_id]
                if np.dot(direction, away) < 0:
                    direction *= -1.0
            directional_extent = float(np.ptp(
                analysis.mesh.vertices[group.vertex_indices] @ direction
            ))
            extent = max(
                principal_extent,
                directional_extent,
                analysis.characteristic_scale * 0.05,
            )
            tails[group_id] = heads[group_id] + direction * extent

    final_components = _components(active_ids, accepted)
    mirror_pair_details = []
    if mirror_partners:
        owned_for_mirror = analysis.mesh.vertices[analysis.mesh.compact_owners >= 0]
        mirror_center = (
            float(np.median(owned_for_mirror[:, side_axis]))
            if owned_for_mirror.size else 0.0
        )
        mirror_positions = np.asarray(
            [group_by_id[group_id].centroid for group_id in active_ids],
            dtype=np.float64,
        )
        mirror_ranges = np.maximum(
            np.ptp(mirror_positions, axis=0), analysis.characteristic_scale
        ) if mirror_positions.size else np.ones(3, dtype=np.float64)
        for first, second in sorted(mirror_partners.items()):
            if first >= second:
                continue
            reflected = group_by_id[first].centroid.copy()
            reflected[side_axis] = 2.0 * mirror_center - reflected[side_axis]
            delta = np.abs(reflected - group_by_id[second].centroid)
            score = (
                delta[side_axis] / max(
                    float(mirror_ranges[side_axis]) * 0.08,
                    analysis.characteristic_scale,
                )
                + sum(
                    delta[index] / max(
                        float(mirror_ranges[index]) * 0.08,
                        analysis.characteristic_scale,
                    )
                    for index in range(3) if index != side_axis
                )
                + abs(np.log(max(group_by_id[first].vertex_count, 1) /
                           max(group_by_id[second].vertex_count, 1))) * 0.25
            )
            mirror_pair_details.append({
                "compact_ids": [int(first), int(second)],
                "raw_owner_ids": [
                    int(group_by_id[first].raw_owner_id),
                    int(group_by_id[second].raw_owner_id),
                ],
                "score": float(score),
                "confidence": float(np.clip(1.0 - score / 2.5, 0.0, 1.0)),
            })
    candidate_by_pair = {}
    for edge in candidates:
        pair = proposal_edge_key(edge.group_a, edge.group_b)
        previous = candidate_by_pair.get(pair)
        if previous is None or (
            edge.total_cost,
            edge.group_a,
            edge.group_b,
        ) < (
            previous.total_cost,
            previous.group_a,
            previous.group_b,
        ):
            candidate_by_pair[pair] = edge
    # Ensure the candidate displayed for an accepted pair is the exact edge
    # used to author its joint and hierarchy, even when a synthetic and a
    # boundary candidate share the same endpoints.
    for edge in accepted:
        candidate_by_pair[proposal_edge_key(edge.group_a, edge.group_b)] = edge
    unique_candidates = tuple(
        sorted(candidate_by_pair.values(), key=lambda edge: (edge.group_a, edge.group_b))
    )
    component_sizes = [
        {
            "compact_ids": [int(group_id) for group_id in component],
            "raw_owner_ids": [int(group_by_id[group_id].raw_owner_id) for group_id in component],
            "group_count": len(component),
            "vertex_count": int(sum(group_by_id[group_id].vertex_count for group_id in component)),
        }
        for component in final_components
    ]

    return RigProposal(
        groups=analysis.groups,
        parent_by_group=parent_by_group,
        head_by_group=heads,
        tail_by_group=tails,
        roll_reference_by_group=roll_references,
        root_groups=roots,
        edge_candidates=unique_candidates,
        accepted_edges=tuple((edge.group_a, edge.group_b) for edge in accepted),
        skipped_groups=tuple(sorted(skipped)),
        warnings=tuple(warnings),
        settings={
            "algorithm": "TOPOLOGY",
            "disconnected_policy": policy,
            "root_override": int(root_override),
            "side_axis": "XYZ"[side_axis],
            "side_inverted": side_inverted,
            "forced_edges": [
                list(proposal_edge_key(*edge)) for edge in sorted(forced_edges or ())
            ],
            "rejected_edges": [
                list(proposal_edge_key(*edge)) for edge in sorted(rejected_edges or ())
            ],
            "mirror_pair_count": len(mirror_partners) // 2,
            "mirror_pairs": [
                [group_by_id[first].raw_owner_id, group_by_id[second].raw_owner_id]
                for first, second in sorted(mirror_partners.items())
                if first < second
            ],
            "mirror_pair_details": mirror_pair_details,
            "central_group_count": len(central_ids),
            "component_count": len(final_components),
            "component_sizes": component_sizes,
            "root_candidates": root_diagnostics,
            "skipped_group_count": len(skipped),
            "leaf_tail_policy": "MIDLINE_BODY_Y_V1",
            "root_selection_policy": "SOURCE_ORDERED_BACKBONE_V1",
        },
        algorithm_version=4,
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
