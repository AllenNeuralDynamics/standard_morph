import json
import unittest

import pandas as pd

from standard_morph.engine import run_qc
from standard_morph.exceptions import IncompatibleMetricContextError, MissingPolicyValueError
from standard_morph.metrics.root_near_detected_soma import RootNearDetectedSomaMetric
from standard_morph.preparation import PreparedMorphology
from standard_morph.models.qc_context import QCContext, Space
from standard_morph.models.qc_policy import Policy, PolicyRange

NAME = "root_near_detected_soma"
POLICY = Policy("t", {NAME: {"soma_radius_um": PolicyRange(lo=10.0, hi=30.0)}})
EMPTY = Policy("t", {})  # missing soma_radius_um -> MissingPolicyValueError


def _df(rows):
    return pd.DataFrame(rows, columns=["node_id", "compartment", "x", "y", "z", "r", "parent"])


def _pm(rows):
    return PreparedMorphology.from_dataframe(_df(rows))


def _ctx(xyz, radius):
    return QCContext(space=Space.IMAGE_SPACE, resources={
        "soma_detection_xyz": xyz, "soma_detection_radius_um": radius,
    })


def _soma_at(x, y, z):
    # A one-soma tree whose soma sits at (x, y, z).
    return _pm([
        (1, 1, x, y, z, 1.0, -1),
        (2, 3, x, y, z + 10.0, 1.0, 1),
    ])


def _evaluate(pm, xyz, radius, policy=POLICY):
    return RootNearDetectedSomaMetric().evaluate(pm, _ctx(xyz, radius), policy)


# Two detections 1000 um apart, both with a 20 um radius.
DETECTIONS = [(0.0, 0.0, 0.0), (1000.0, 0.0, 0.0)]
RADII = [20.0, 20.0]


class TestRootNearDetectedSoma(unittest.TestCase):
    def test_root_within_radius_passes(self):
        r = _evaluate(_soma_at(1003.0, 4.0, 12.0), DETECTIONS, RADII)
        self.assertEqual(r.status, "pass")
        m = r.measurements
        self.assertEqual(m["matched_detection_index"], 1)
        self.assertEqual(m["nearest_detection_index"], 1)
        self.assertAlmostEqual(m["nearest_distance_um"], 13.0)
        self.assertAlmostEqual(m["second_nearest_distance_um"], ((1003.0 ** 2) + 16 + 144) ** 0.5)
        self.assertEqual(m["n_detections_within_tolerance"], 1)
        self.assertEqual(r.flagged_node_ids, [])

    def test_root_outside_every_radius_is_review(self):
        r = _evaluate(_soma_at(500.0, 0.0, 0.0), DETECTIONS, RADII)
        self.assertEqual(r.status, "review")
        self.assertEqual(r.flagged_node_ids, [1])
        self.assertEqual(r.flagged_node_coordinates, [(500.0, 0.0, 0.0)])
        self.assertIsNone(r.measurements["matched_detection_index"])
        self.assertAlmostEqual(r.value, 500.0)
        self.assertIn("500.0 um", r.message)

    def test_boundary_is_inclusive(self):
        r = _evaluate(_soma_at(20.0, 0.0, 0.0), DETECTIONS, RADII)
        self.assertEqual(r.status, "pass")

    def test_small_radius_clamped_up_to_lo(self):
        # A 0 um fit still gets the 10 um floor.
        r = _evaluate(_soma_at(8.0, 0.0, 0.0), [(0.0, 0.0, 0.0)], [0.0])
        self.assertEqual(r.status, "pass")
        self.assertEqual(r.measurements["nearest_tolerance_um"], 10.0)
        self.assertEqual(r.measurements["nearest_raw_radius_um"], 0.0)

    def test_large_radius_clamped_down_to_hi(self):
        # A 5000 um fit must not swallow a root 100 um away.
        r = _evaluate(_soma_at(100.0, 0.0, 0.0), [(0.0, 0.0, 0.0)], [5000.0])
        self.assertEqual(r.status, "review")
        self.assertEqual(r.measurements["nearest_tolerance_um"], 30.0)

    def test_any_detection_within_its_radius_matches(self):
        # The nearest center (12 um away, 10 um radius) misses, but a farther
        # detection with a larger radius (25 um away, 30 um radius) contains the root.
        r = _evaluate(_soma_at(0.0, 0.0, 0.0), [(12.0, 0.0, 0.0), (0.0, 25.0, 0.0)], [5.0, 30.0])
        self.assertEqual(r.status, "pass")
        self.assertEqual(r.measurements["nearest_detection_index"], 0)
        self.assertEqual(r.measurements["matched_detection_index"], 1)

    def test_best_match_is_smallest_relative_distance(self):
        r = _evaluate(
            _soma_at(0.0, 0.0, 0.0), [(9.0, 0.0, 0.0), (0.0, 12.0, 0.0)], [10.0, 30.0],
        )
        self.assertEqual(r.measurements["n_detections_within_tolerance"], 2)
        # 9/10 = 0.9 vs 12/30 = 0.4 -> detection 1.
        self.assertEqual(r.measurements["matched_detection_index"], 1)

    def test_scalar_threshold_is_a_fixed_radius(self):
        policy = Policy("t", {NAME: {"soma_radius_um": 15.0}})
        r = _evaluate(_soma_at(14.0, 0.0, 0.0), [(0.0, 0.0, 0.0)], [100.0], policy)
        self.assertEqual(r.status, "pass")
        self.assertEqual(r.measurements["nearest_tolerance_um"], 15.0)
        self.assertEqual(r.thresholds_used, {"soma_radius_um": {"lo": 15.0, "hi": 15.0}})

    def test_single_detection_has_no_second_nearest(self):
        r = _evaluate(_soma_at(0.0, 0.0, 0.0), [(0.0, 0.0, 0.0)], [20.0])
        self.assertIsNone(r.measurements["second_nearest_distance_um"])

    def test_missing_threshold_raises(self):
        with self.assertRaises(MissingPolicyValueError):
            _evaluate(_soma_at(0.0, 0.0, 0.0), DETECTIONS, RADII, EMPTY)

    def test_multiple_somas_error(self):
        pm = _pm([
            (1, 1, 0.0, 0.0, 0.0, 1.0, -1),
            (2, 3, 0.0, 0.0, 10.0, 1.0, 1),
            (3, 1, 500.0, 500.0, 500.0, 1.0, -1),  # a second soma root
        ])
        r = _evaluate(pm, DETECTIONS, RADII)
        self.assertEqual(r.status, "error")
        self.assertEqual(r.measurements["n_soma"], 2)

    def test_non_soma_root_error(self):
        pm = _pm([(1, 3, 0.0, 0.0, 0.0, 1.0, -1), (2, 3, 0.0, 0.0, 10.0, 1.0, 1)])
        r = _evaluate(pm, DETECTIONS, RADII)
        self.assertEqual(r.status, "error")
        self.assertEqual(r.measurements["n_soma"], 0)

    def test_no_detections_error(self):
        r = _evaluate(_soma_at(0.0, 0.0, 0.0), [], [])
        self.assertEqual(r.status, "error")

    def test_bad_shape_error(self):
        for xyz, radius in [
            ([(0.0, 0.0)], [20.0]),  # 2-D points
            (DETECTIONS, [20.0]),  # one radius for two detections
            ((0.0, 0.0, 0.0), [20.0]),  # a single point, not (N, 3)
        ]:
            with self.subTest(xyz=xyz, radius=radius):
                r = _evaluate(_soma_at(0.0, 0.0, 0.0), xyz, radius)
                self.assertEqual(r.status, "error")
                self.assertIn("shape", r.message)

    def test_non_finite_error(self):
        r = _evaluate(_soma_at(0.0, 0.0, 0.0), DETECTIONS, [20.0, float("nan")])
        self.assertEqual(r.status, "error")
        self.assertIn("non-finite", r.message)


class TestRootNearDetectedSomaPlumbing(unittest.TestCase):
    def test_missing_resources_is_incompatible(self):
        with self.assertRaises(IncompatibleMetricContextError):
            run_qc(_df([(1, 1, 0.0, 0.0, 0.0, 1.0, -1)]),
                   QCContext(space=Space.IMAGE_SPACE), metrics=[NAME])

    def test_wrong_space_is_incompatible(self):
        ctx = _ctx(DETECTIONS, RADII)
        ctx = QCContext(space=Space.CCF_REGISTERED, resources=ctx.resources)
        with self.assertRaises(IncompatibleMetricContextError):
            run_qc(_df([(1, 1, 0.0, 0.0, 0.0, 1.0, -1), (2, 3, 0.0, 0.0, 10.0, 1.0, 1)]),
                   ctx, metrics=[NAME])

    def test_runs_through_engine_with_default_policy(self):
        df = _df([(1, 1, 500.0, 0.0, 0.0, 1.0, -1), (2, 3, 500.0, 0.0, 10.0, 1.0, 1)])
        report = run_qc(df, _ctx(DETECTIONS, RADII), metrics=[NAME])
        r = next(x for x in report.results if x.name == NAME)
        self.assertEqual(r.status, "review")
        self.assertEqual(report.summary["overall_status"], "incomplete")
        self.assertEqual(r.thresholds_used, {"soma_radius_um": {"lo": 35.0, "hi": 50.0}})
        json.dumps(report.to_dict())  # measurements must be JSON-safe


if __name__ == "__main__":
    unittest.main()
