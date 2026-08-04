import importlib.util
from pathlib import Path
import unittest

import numpy as np


MODULE_PATH = Path(__file__).parents[1] / "utils" / "rig_reconstruction.py"
SPEC = importlib.util.spec_from_file_location("rig_hierarchy", MODULE_PATH)
rig = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(rig)


class RigHierarchyTests(unittest.TestCase):
    def _chain_analysis(self, scale=1.0):
        vertices = np.array(
            [
                [0.0, 0.0, 0.0], [0.8, 0.0, 0.0],
                [1.0, 0.0, 0.0], [1.8, 0.0, 0.0],
                [2.0, 0.0, 0.0], [2.8, 0.0, 0.0],
            ],
            dtype=np.float64,
        ) * scale
        mesh = rig.build_mesh_analysis_input(
            vertices,
            [0, 0, 1, 1, 2, 2],
            [0, 4, 9],
            edges=[[0, 1], [1, 2], [2, 3], [3, 4], [4, 5]],
        )
        return rig.analyze_rig_geometry(mesh)

    def test_boundary_topology_builds_chain_and_boundary_heads(self):
        proposal = rig.build_topology_rig_proposal(self._chain_analysis())

        self.assertEqual(proposal.accepted_edges, ((0, 1), (1, 2)))
        self.assertEqual(proposal.root_groups, (1,))
        np.testing.assert_array_equal(proposal.parent_by_group, [1, -1, 1])
        np.testing.assert_allclose(proposal.head_by_group[0], [0.9, 0.0, 0.0])
        np.testing.assert_allclose(proposal.head_by_group[2], [1.9, 0.0, 0.0])
        self.assertTrue(all(
            edge.reason_codes == ("TOPOLOGY_BOUNDARY",)
            for edge in proposal.edge_candidates
        ))

    def test_parent_map_is_uniform_scale_invariant(self):
        baseline = rig.build_topology_rig_proposal(self._chain_analysis(1.0))
        tiny = rig.build_topology_rig_proposal(self._chain_analysis(0.01))
        huge = rig.build_topology_rig_proposal(self._chain_analysis(100.0))

        np.testing.assert_array_equal(tiny.parent_by_group, baseline.parent_by_group)
        np.testing.assert_array_equal(huge.parent_by_group, baseline.parent_by_group)
        self.assertEqual(tiny.root_groups, baseline.root_groups)
        self.assertEqual(huge.root_groups, baseline.root_groups)

    def test_root_diagnostics_include_disallowed_candidates(self):
        analysis = self._chain_analysis()
        proposal = rig.build_topology_rig_proposal(analysis)
        diagnostics = []
        group_by_id = {group.compact_id: group for group in analysis.groups}
        accepted = tuple(
            edge for edge in proposal.edge_candidates
            if tuple(sorted((edge.group_a, edge.group_b))) in proposal.accepted_edges
        )
        root = rig._select_component_root(
            (0, 1, 2),
            accepted,
            group_by_id,
            analysis.characteristic_scale,
            -1,
            allowed_roots={1},
            diagnostics=diagnostics,
        )
        self.assertEqual(root, 1)
        self.assertEqual(
            {item["compact_id"] for item in diagnostics if item["reason"] == "NOT_ALLOWED"},
            {0, 2},
        )

    def test_source_order_prior_selects_earliest_supported_backbone_group(self):
        # Both groups 0 and 3 are three-way central junctions. Raw owner 1 is
        # the source-ordered torso/root, while raw owner 17 represents a later
        # belly control that graph centrality must not promote to root.
        vertices = []
        centers = [
            [0.0, 0.0, 0.0],
            [0.0, -2.0, 0.0],
            [0.0, -1.0, 0.0],
            [0.0, 0.5, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 2.0, 0.0],
        ]
        for center in centers:
            vertices.extend([
                [center[0] - 0.1, center[1], center[2]],
                [center[0] + 0.1, center[1], center[2]],
            ])
        edges = [[2 * group_id, 2 * group_id + 1] for group_id in range(6)]
        edges.extend([[0, 2], [0, 6], [0, 10], [6, 4], [6, 8]])
        mesh = rig.build_mesh_analysis_input(
            vertices,
            [group_id for group_id in range(6) for _ in range(2)],
            [1, 2, 16, 17, 18, 31],
            edges=edges,
        )

        proposal = rig.build_topology_rig_proposal(rig.analyze_rig_geometry(mesh))

        self.assertEqual(proposal.root_groups, (0,))
        self.assertEqual(proposal.settings["root_selection_policy"], "SOURCE_ORDERED_BACKBONE_V1")

    def test_midline_lateral_leaf_pca_follows_signed_body_y_flow(self):
        # Midline control regions can span the model laterally, making PCA
        # point along X even though their useful control orientation follows
        # the head/tail flow on Blender Y.
        mesh = rig.build_mesh_analysis_input(
            [
                [-0.2, 0.0, 0.0], [0.2, 0.0, 0.0],
                [-1.0, -2.0, 0.0], [1.0, -2.0, 0.0],
                [-1.0, 2.0, 0.0], [1.0, 2.0, 0.0],
            ],
            [0, 0, 1, 1, 2, 2],
            [1, 8, 20],
            edges=[[0, 1], [0, 2], [2, 3], [1, 4], [4, 5]],
        )
        proposal = rig.build_topology_rig_proposal(
            rig.analyze_rig_geometry(mesh), root_override=0
        )

        forward = proposal.tail_by_group[1] - proposal.head_by_group[1]
        rearward = proposal.tail_by_group[2] - proposal.head_by_group[2]
        self.assertAlmostEqual(forward[0], 0.0)
        self.assertLess(forward[1], 0.0)
        self.assertAlmostEqual(forward[2], 0.0)
        self.assertAlmostEqual(rearward[0], 0.0)
        self.assertGreater(rearward[1], 0.0)
        self.assertAlmostEqual(rearward[2], 0.0)

    def test_two_adjacent_groups_produce_one_edge(self):
        mesh = rig.build_mesh_analysis_input(
            [[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]],
            [0, 0, 1, 1],
            [0, 1],
            edges=[[0, 1], [1, 2], [2, 3]],
        )
        proposal = rig.build_topology_rig_proposal(rig.analyze_rig_geometry(mesh))

        self.assertEqual(proposal.accepted_edges, ((0, 1),))
        self.assertEqual(len(proposal.root_groups), 1)

    def test_multi_root_preserves_disconnected_groups(self):
        mesh = rig.build_mesh_analysis_input(
            [[0, 0, 0], [1, 0, 0], [10, 0, 0], [11, 0, 0]],
            [0, 0, 1, 1],
            [2, 8],
            edges=[[0, 1], [2, 3]],
        )
        proposal = rig.build_topology_rig_proposal(
            rig.analyze_rig_geometry(mesh), disconnected_policy="MULTI_ROOT"
        )

        self.assertEqual(proposal.root_groups, (0, 1))
        self.assertEqual(proposal.skipped_groups, ())
        self.assertEqual(proposal.accepted_edges, ())

    def test_attach_nearest_marks_low_confidence_fallback(self):
        mesh = rig.build_mesh_analysis_input(
            [[0, 0, 0], [1, 0, 0], [10, 0, 0], [11, 0, 0]],
            [0, 0, 1, 1],
            [2, 8],
            edges=[[0, 1], [2, 3]],
        )
        proposal = rig.build_topology_rig_proposal(
            rig.analyze_rig_geometry(mesh), disconnected_policy="ATTACH_NEAREST"
        )

        self.assertEqual(len(proposal.root_groups), 1)
        self.assertEqual(proposal.accepted_edges, ((0, 1),))
        fallback = next(edge for edge in proposal.edge_candidates if edge.boundary_edge_count == 0)
        self.assertEqual(fallback.reason_codes, ("PROXIMITY_FALLBACK",))
        self.assertLessEqual(fallback.confidence, 0.35)

    def test_skip_selects_component_by_owned_vertex_support(self):
        mesh = rig.build_mesh_analysis_input(
            [[0, 0, 0], [1, 0, 0], [2, 0, 0], [10, 0, 0]],
            [0, 0, 0, 1],
            [3, 7],
            edges=[[0, 1], [1, 2]],
        )
        proposal = rig.build_topology_rig_proposal(
            rig.analyze_rig_geometry(mesh), disconnected_policy="SKIP"
        )

        self.assertEqual(proposal.root_groups, (0,))
        self.assertEqual(proposal.skipped_groups, (1,))

    def test_bilateral_limb_cannot_bridge_central_body_groups(self):
        # Surface ownership is asymmetric: left limb root 3 touches both central
        # groups 0 and 1, while mirrored root 5 only touches group 0. The limb
        # must attach once and may not become the parent of central group 1.
        mesh = rig.build_mesh_analysis_input(
            [
                [0, 0, 0], [0, 0.4, 0],
                [0, 1, 0], [0, 1.4, 0],
                [-1, 0, 0], [-2, 0, 0],
                [1, 0, 0], [2, 0, 0],
            ],
            [0, 0, 1, 1, 2, 3, 4, 5],
            [1, 2, 15, 16, 18, 19],
            edges=[
                [0, 1], [2, 3],
                [0, 4], [2, 4], [4, 5],
                [0, 6], [6, 7],
            ],
        )
        proposal = rig.build_topology_rig_proposal(rig.analyze_rig_geometry(mesh))

        self.assertNotEqual(proposal.parent_by_group[1], 2)
        self.assertIn(proposal.parent_by_group[2], (0, 1))
        self.assertEqual(proposal.parent_by_group[4], proposal.parent_by_group[2])
        self.assertNotIn(1, self._descendants(proposal.parent_by_group, 2))

    @staticmethod
    def _descendants(parents, root):
        descendants = set()
        pending = [root]
        while pending:
            current = pending.pop()
            children = [index for index, parent in enumerate(parents) if parent == current]
            descendants.update(children)
            pending.extend(children)
        return descendants

    def test_supported_torso_path_does_not_create_mirror_cross_link(self):
        # Group 0 is torso; groups 1 and 2 are symmetric limbs. Only actual
        # owner boundaries become candidates, so no unsupported limb-limb edge.
        mesh = rig.build_mesh_analysis_input(
            [[0, 0, 0], [0, 1, 0], [-1, 1, 0], [-2, 1, 0], [1, 1, 0], [2, 1, 0]],
            [0, 0, 1, 1, 2, 2],
            [0, 1, 2],
            edges=[[0, 1], [1, 2], [2, 3], [1, 4], [4, 5]],
        )
        proposal = rig.build_topology_rig_proposal(rig.analyze_rig_geometry(mesh))
        pairs = {(edge.group_a, edge.group_b) for edge in proposal.edge_candidates}

        self.assertEqual(pairs, {(0, 1), (0, 2)})
        self.assertNotIn((1, 2), pairs)
        self.assertEqual(proposal.root_groups, (0,))


if __name__ == "__main__":
    unittest.main()
