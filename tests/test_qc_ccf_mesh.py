"""CCF-mesh metric tests.

These use a tiny synthetic annotation array injected via
``resources["ccf_annotation"]`` so they never load the real ~4.8 GB atlas.
The synthetic atlas is a 10x10x10 volume whose central [2,8) cube is "in brain"
(non-zero); everything else is voxel value 0 = outside brain. With resolution=1,
micron coordinates map directly to voxel indices.
"""
import unittest

import numpy as np
import pandas as pd

from standard_morph.preparation import PreparedMorphology
from standard_morph.metrics.ccf_mesh import NodesOutsideCcfMeshMetric, SomaInsideCcfMeshMetric
from standard_morph.models.qc_context import QCContext, Space
from standard_morph.models.qc_policy import Policy
from standard_morph.exceptions import IncompatibleMetricContextError
from standard_morph import run_qc


def _df(rows):
    return pd.DataFrame(rows, columns=["node_id", "compartment", "x", "y", "z", "r", "parent"])


def _synthetic_atlas():
    ann = np.zeros((10, 10, 10), dtype=np.uint32)
    ann[2:8, 2:8, 2:8] = 500  # a "structure" occupying the central cube
    return ann


def _ccf_ctx(**overrides):
    kwargs = dict(space=Space.CCF_REGISTERED, ccf_resolution=1,
                  resources={"ccf_annotation": _synthetic_atlas()})
    kwargs.update(overrides)
    return QCContext(**kwargs)


class TestNodesOutsideCcfMesh(unittest.TestCase):
    def _policy(self, max_fraction=0.05):
        return Policy("t", {"nodes_outside_ccf_mesh": {"max_fraction_outside": max_fraction}})

    def test_flags_out_of_brain_nodes(self):
        # soma + one node inside the cube; two nodes clearly outside it.
        df = _df([
            (1, 1, 5.0, 5.0, 5.0, 1.0, -1),   # inside
            (2, 3, 5.0, 5.0, 6.0, 1.0, 1),    # inside
            (3, 3, 0.0, 0.0, 0.0, 1.0, 2),    # outside (voxel value 0)
            (4, 3, 9.0, 9.0, 9.0, 1.0, 3),    # outside
        ])
        pm = PreparedMorphology.from_dataframe(df)
        result = NodesOutsideCcfMeshMetric().evaluate(pm, _ccf_ctx(), self._policy())
        self.assertEqual(result.status, "review")
        self.assertEqual(sorted(result.flagged_node_ids), [3, 4])
        self.assertEqual(result.measurements["n_outside"], 2)
        self.assertAlmostEqual(result.measurements["fraction_outside"], 0.5)

    def test_passes_when_within_threshold(self):
        df = _df([
            (1, 1, 5.0, 5.0, 5.0, 1.0, -1),
            (2, 3, 5.0, 5.0, 6.0, 1.0, 1),
            (3, 3, 0.0, 0.0, 0.0, 1.0, 2),   # 1/3 outside
        ])
        pm = PreparedMorphology.from_dataframe(df)
        result = NodesOutsideCcfMeshMetric().evaluate(pm, _ccf_ctx(), self._policy(max_fraction=0.9))
        self.assertEqual(result.status, "pass")

    def test_out_of_bounds_counts_as_outside(self):
        # A node beyond the volume (and a negative one) must not wrap around.
        df = _df([
            (1, 1, 5.0, 5.0, 5.0, 1.0, -1),
            (2, 3, 100.0, 5.0, 5.0, 1.0, 1),   # past the array bound
            (3, 3, -5.0, 5.0, 5.0, 1.0, 2),    # negative index
        ])
        pm = PreparedMorphology.from_dataframe(df)
        result = NodesOutsideCcfMeshMetric().evaluate(pm, _ccf_ctx(), self._policy(max_fraction=0.9))
        self.assertEqual(sorted(result.flagged_node_ids), [2, 3])


class TestSomaInsideCcfMesh(unittest.TestCase):
    def test_soma_inside_passes(self):
        df = _df([
            (1, 1, 5.0, 5.0, 5.0, 1.0, -1),
            (2, 3, 5.0, 5.0, 6.0, 1.0, 1),
        ])
        pm = PreparedMorphology.from_dataframe(df)
        result = SomaInsideCcfMeshMetric().evaluate(pm, _ccf_ctx(), Policy("t", {}))
        self.assertEqual(result.status, "pass")
        self.assertTrue(result.measurements["soma_inside_brain"])
        self.assertEqual(result.measurements["soma_structure_id"], 500)

    def test_soma_outside_flags_for_review(self):
        df = _df([
            (1, 1, 0.0, 0.0, 0.0, 1.0, -1),   # outside the cube
            (2, 3, 5.0, 5.0, 5.0, 1.0, 1),
        ])
        pm = PreparedMorphology.from_dataframe(df)
        result = SomaInsideCcfMeshMetric().evaluate(pm, _ccf_ctx(), Policy("t", {}))
        self.assertEqual(result.status, "review")
        self.assertEqual(result.flagged_node_ids, [1])


class TestCcfApplicability(unittest.TestCase):
    def test_wrong_space_is_inapplicable(self):
        img_ctx = QCContext(space=Space.IMAGE_SPACE, ccf_resolution=1)
        with self.assertRaises(IncompatibleMetricContextError):
            NodesOutsideCcfMeshMetric().validate_context(img_ctx)
        self.assertFalse(NodesOutsideCcfMeshMetric().is_applicable(img_ctx))

    def test_run_qc_fail_fast_on_image_space(self):
        # The headline fail-fast example: a CCF metric on image_space halts.
        df = _df([(1, 1, 5.0, 5.0, 5.0, 1.0, -1), (2, 3, 5.0, 5.0, 6.0, 1.0, 1)])
        with self.assertRaises(IncompatibleMetricContextError):
            run_qc(df, QCContext(space=Space.IMAGE_SPACE), metrics=["nodes_outside_ccf_mesh"])

    def test_run_qc_end_to_end_with_injected_atlas(self):
        df = _df([
            (1, 1, 5.0, 5.0, 5.0, 1.0, -1),
            (2, 3, 5.0, 5.0, 6.0, 1.0, 1),
            (3, 3, 0.0, 0.0, 0.0, 1.0, 2),
        ])
        report = run_qc(df, _ccf_ctx(), suite_name="default_post_registration_tests")
        from standard_morph.suites import resolve_suite
        names = [r.name for r in report.results]
        self.assertEqual(names, resolve_suite("default_post_registration_tests"))
        # Resolution is run-level provenance on the report, not a per-metric measurement.
        self.assertEqual(report.ccf_resolution, 1)
        outside = next(r for r in report.results if r.name == "nodes_outside_ccf_mesh")
        self.assertNotIn("resolution_um", outside.measurements)


if __name__ == "__main__":
    unittest.main()
