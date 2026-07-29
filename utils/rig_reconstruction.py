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
