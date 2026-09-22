import unittest
from pathlib import Path

import pandas as pd

from standard_morph import run_qc, QCContext, Space, MorphologyKind
from standard_morph.exceptions import IncompatibleMetricContextError
from standard_morph.registry import REGISTRY
from standard_morph.suites import resolve_suite, available_suites

SWC_DIR = Path(__file__).parent / "swcs"


def _df(rows):
    return pd.DataFrame(rows, columns=["node_id", "compartment", "x", "y", "z", "r", "parent"])


def _clean_tree():
    return _df([
        (1, 1, 0.0, 0.0, 0.0, 1.0, -1),
        (2, 2, 10.0, 0.0, 0.0, 1.0, 1),
        (3, 2, 20.0, 0.0, 0.0, 1.0, 2),
        (4, 3, 5.0, 5.0, 0.0, 1.0, 1),
    ])


class TestRegistryAndSuites(unittest.TestCase):
    def test_metrics_registered(self):
        for name in ("local_tortuosity", "single_connected_component", "branch_max_degree"):
            self.assertIn(name, REGISTRY)

    def test_builtin_suites_reference_real_metrics(self):
        for suite in available_suites():
            for name in resolve_suite(suite):
                self.assertIn(name, REGISTRY, f"{suite} references unregistered '{name}'")


class TestRunQC(unittest.TestCase):
    def test_run_pre_suite_on_dataframe(self):
        ctx = QCContext(space=Space.IMAGE_SPACE, morphology_kind=MorphologyKind.MERGED)
        report = run_qc(_clean_tree(), ctx, suite_name="default_pre_registration_tests")
        self.assertEqual(report.summary["overall_status"], "pass")
        self.assertEqual(report.summary["n_metrics"], len(resolve_suite("default_pre_registration_tests")))
        self.assertEqual(report.requested_metrics, resolve_suite("default_pre_registration_tests"))
        self.assertEqual(report.policy_version, "policy_v1")
        self.assertTrue(report.passed)

    def test_explicit_metrics_list(self):
        ctx = QCContext(space=Space.IMAGE_SPACE)
        report = run_qc(_clean_tree(), ctx, metrics=["single_connected_component"])
        self.assertEqual(report.summary["n_metrics"], 1)
        self.assertEqual(report.results[0].name, "single_connected_component")

    def test_requires_exactly_one_of_suite_or_metrics(self):
        ctx = QCContext(space=Space.IMAGE_SPACE)
        with self.assertRaises(ValueError):
            run_qc(_clean_tree(), ctx)  # neither
        with self.assertRaises(ValueError):
            run_qc(_clean_tree(), ctx, suite_name="default_pre_registration_tests",
                   metrics=["single_connected_component"])  # both

    def test_unknown_metric_raises(self):
        ctx = QCContext(space=Space.IMAGE_SPACE)
        with self.assertRaises(KeyError):
            run_qc(_clean_tree(), ctx, metrics=["does_not_exist"])

    def test_fail_fast_on_incompatible_context(self):
        # nodes_outside_ccf_mesh is ccf_registered-only; requesting it under
        # image_space must halt before executing anything.
        ctx = QCContext(space=Space.IMAGE_SPACE)
        with self.assertRaises(IncompatibleMetricContextError):
            run_qc(_clean_tree(), ctx, metrics=["nodes_outside_ccf_mesh"])

    def test_registered_file_path_through_engine(self):
        # A CCF-registered file flows through run_qc from a path. Uses a
        # CCF-agnostic metric so this test stays light; the CCF-mesh metrics
        # (which need the atlas) are covered in test_qc_ccf_mesh.py.
        ctx = QCContext(space=Space.CCF_REGISTERED)
        reg_file = str(SWC_DIR / "17109_6601-X5417-Y25287_reg.swc")
        report = run_qc(reg_file, ctx, metrics=["single_connected_component"])
        self.assertIn(report.summary["overall_status"], ("pass", "fail"))
        self.assertEqual(report.input_ref, reg_file)

    def test_report_is_json_serialisable(self):
        import json

        ctx = QCContext(space=Space.IMAGE_SPACE)
        report = run_qc(_clean_tree(), ctx, suite_name="default_pre_registration_tests")
        json.dumps(report.to_dict())  # must not raise

    def test_review_metric_rolls_up_to_incomplete_not_fail(self):
        # A trifurcation trips branch_max_degree, which is a REVIEW metric: the
        # result is "review", the run is "incomplete" (needs human oversight),
        # and report.passed is False -- but it is NOT counted or graded as a fail.
        trifurcation = _df([
            (1, 1, 0.0, 0.0, 0.0, 1.0, -1),
            (2, 3, 1.0, 0.0, 0.0, 1.0, 1),
            (3, 3, 2.0, 1.0, 0.0, 1.0, 2),
            (4, 3, 2.0, 0.0, 0.0, 1.0, 2),
            (5, 3, 2.0, -1.0, 0.0, 1.0, 2),
        ])
        ctx = QCContext(space=Space.IMAGE_SPACE)
        report = run_qc(trifurcation, ctx, metrics=["branch_max_degree"])
        self.assertEqual(report.results[0].status, "review")
        self.assertEqual(report.summary["overall_status"], "incomplete")
        self.assertEqual(report.summary["n_review"], 1)
        self.assertEqual(report.summary["n_fail"], 0)
        self.assertFalse(report.passed)
        self.assertTrue(report.integrity_ok)  # the input itself is fine

    def test_policy_version_override(self):
        ctx = QCContext(space=Space.IMAGE_SPACE, policy_version="policy_v1")
        report = run_qc(_clean_tree(), ctx, metrics=["local_tortuosity"], policy_version="policy_v1")
        self.assertEqual(report.policy_version, "policy_v1")


class TestQCContextCoordinateScaleValidation(unittest.TestCase):
    def test_default_is_identity(self):
        ctx = QCContext(space=Space.IMAGE_SPACE)
        self.assertEqual(ctx.coordinate_scale, (1.0, 1.0, 1.0))

    def test_list_input_accepted_and_coerced_to_tuple_of_floats(self):
        ctx = QCContext(space=Space.IMAGE_SPACE, coordinate_scale=[2, 3, 4])
        self.assertEqual(ctx.coordinate_scale, (2.0, 3.0, 4.0))
        self.assertIsInstance(ctx.coordinate_scale[0], float)

    def test_wrong_length_raises(self):
        with self.assertRaises(ValueError):
            QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(1.0, 1.0))
        with self.assertRaises(ValueError):
            QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(1.0, 1.0, 1.0, 1.0))

    def test_non_numeric_element_raises(self):
        with self.assertRaises(TypeError):
            QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(1.0, "2.0", 1.0))

    def test_zero_element_raises(self):
        with self.assertRaises(ValueError):
            QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(1.0, 0.0, 1.0))

    def test_negative_element_raises(self):
        with self.assertRaises(ValueError):
            QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(1.0, -1.0, 1.0))


class TestCoordinateScaleIntegration(unittest.TestCase):
    def _short_edge_df(self):
        # A chain with edges of 2 um each -- well within the 30 um image-space threshold.
        return _df([
            (1, 1, 0.0, 0.0,  0.0, 1.0, -1),
            (2, 3, 0.0, 0.0,  2.0, 1.0,  1),  # soma child (excluded from edge_length)
            (3, 3, 0.0, 0.0,  4.0, 1.0,  2),  # edge 2 um
            (4, 3, 0.0, 0.0,  6.0, 1.0,  3),  # edge 2 um
        ])

    def test_identity_scale_passes_edge_length(self):
        ctx = QCContext(space=Space.IMAGE_SPACE)
        report = run_qc(self._short_edge_df(), ctx, metrics=["edge_length"])
        self.assertEqual(report.results[0].status, "pass")

    def test_scale_makes_edges_exceed_threshold(self):
        # scale of 20 turns 2 um raw -> 40 um effective, exceeding the 30 um limit.
        ctx = QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(20.0, 20.0, 20.0))
        report = run_qc(self._short_edge_df(), ctx, metrics=["edge_length"])
        self.assertEqual(report.results[0].status, "fail")

    def test_coordinate_scale_recorded_in_report(self):
        ctx = QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(0.5, 0.5, 1.0))
        report = run_qc(_clean_tree(), ctx, metrics=["single_connected_component"])
        self.assertEqual(report.coordinate_scale, (0.5, 0.5, 1.0))

    def test_prebuilt_morphology_with_nonidentity_scale_raises(self):
        from standard_morph.preparation import PreparedMorphology
        pm = PreparedMorphology.from_dataframe(_clean_tree())
        ctx = QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(2.0, 2.0, 2.0))
        with self.assertRaises(ValueError):
            run_qc(pm, ctx, metrics=["single_connected_component"])

    def test_coordinate_scale_in_to_dict(self):
        import json
        ctx = QCContext(space=Space.IMAGE_SPACE, coordinate_scale=(0.748, 0.748, 1.0))
        report = run_qc(_clean_tree(), ctx, metrics=["single_connected_component"])
        d = report.to_dict()
        self.assertEqual(d["coordinate_scale"], (0.748, 0.748, 1.0))
        json.dumps(d)  # must not raise


if __name__ == "__main__":
    unittest.main()
