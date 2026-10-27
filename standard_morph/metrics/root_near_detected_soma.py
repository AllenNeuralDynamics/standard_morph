"""Metric -- Root near a detected soma.

Checks that the reconstruction's soma root sits on a soma found by automated
soma detection. Unlike ``soma_at_centroid`` (one known reference soma), this
takes *every* detection in the brain and asks whether the root falls within the
radius of any of them:

    d_i   = |root - detection_i|                    (Euclidean)
    tol_i = clip(radius_i, soma_radius_um.lo, soma_radius_um.hi)
    pass  if any(d_i <= tol_i)

Detected radii are clamped because ellipsoid fits are unreliable: a tiny fit
would reject a correct root, and a huge one (fits of hundreds of um occur)
would accept almost anything. ``lo`` should also cover the detector's
positional error (e.g. snapping to a downsampled voxel grid).
A scalar ``soma_radius_um`` instead of a range applies one fixed radius to
every detection.

Inputs are supplied via ``context.resources`` (required -> the metric is
incompatible with the context, i.e. fail-fast, if either is absent):

* ``soma_detection_xyz``        -- (N, 3) detected soma centers (x, y, z), in
  the same frame and units (um) as the SWC
* ``soma_detection_radius_um``  -- (N,) radius of each detection, in um

A miss is graded ``review``, not ``fail``: detectors miss real somas, so a
root with no nearby detection needs a human look rather than an automatic
rejection.
"""
import time

import numpy as np

from standard_morph.metrics.base import Metric, Applicability, Severity
from standard_morph.models.qc_context import Space, ALL_MORPHOLOGY_KINDS
from standard_morph.models.qc_policy import PolicyRange
from standard_morph.models.qc_result import MetricResult
from standard_morph.registry import register


class RootNearDetectedSomaMetric(Metric):
    name = "root_near_detected_soma"
    display_name = "Root near a detected soma"

    # Detections are image-based, so only meaningful in image space. Reads only
    # the soma's coordinate + compartment, so it survives an untrustworthy topology.
    requires_topology = False
    violation_severity = Severity.REVIEW

    applicability = Applicability(
        spaces=frozenset({Space.IMAGE_SPACE}),
        morphology_kinds=ALL_MORPHOLOGY_KINDS,
        required_resources=frozenset({"soma_detection_xyz", "soma_detection_radius_um"}),
    )
    required_policy_keys = frozenset({"soma_radius_um"})

    def evaluate(self, prepared_morph, context, policy):
        t0 = time.perf_counter()
        result = MetricResult(name=self.name, status="pass")
        radius_range = policy[self.name, "soma_radius_um"]
        if not isinstance(radius_range, PolicyRange):
            # A scalar is a fixed tolerance: every detection gets that radius.
            radius_range = PolicyRange(lo=float(radius_range), hi=float(radius_range))
        result.thresholds_used = {"soma_radius_um": {"lo": radius_range.lo, "hi": radius_range.hi}}

        # -- Validate the SWC soma: exactly one canonical soma root. --
        soma_idx = prepared_morph.soma_roots
        if soma_idx.size != 1:
            return self._error(
                result, t0,
                f"Expected exactly one soma (type 1, root); found {soma_idx.size}.",
                {"n_soma": int(soma_idx.size)},
            )

        # -- Validate the detections. --
        det = np.asarray(context.resources["soma_detection_xyz"], dtype=float)
        radius = np.asarray(context.resources["soma_detection_radius_um"], dtype=float)
        if det.ndim != 2 or det.shape[1] != 3 or radius.shape != (det.shape[0],):
            return self._error(
                result, t0,
                "soma_detection_xyz must have shape (N, 3) and soma_detection_radius_um "
                f"shape (N,); got {det.shape} and {radius.shape}.",
                {},
            )
        if det.shape[0] == 0:
            return self._error(result, t0, "No soma detections were provided.", {"n_detections": 0})
        if not (np.all(np.isfinite(det)) and np.all(np.isfinite(radius))):
            return self._error(
                result, t0, "Soma detections contain non-finite coordinates or radii.", {},
            )

        # -- Distance to every detection vs. its clamped radius. --
        i = int(soma_idx[0])
        root = prepared_morph.xyz[i].astype(float)
        dist = np.linalg.norm(det - root, axis=1)
        tol = np.clip(radius, radius_range.lo, radius_range.hi)
        within = np.flatnonzero(dist <= tol)

        order = np.argsort(dist)
        nearest = int(order[0])
        matched = int(within[np.argmin(dist[within] / tol[within])]) if within.size else None

        result.value = float(dist[nearest])
        result.value_label = "nearest_detection_distance_um"
        result.measurements = {
            "root_xyz": [float(v) for v in root],
            "n_detections": int(det.shape[0]),
            "nearest_detection_index": nearest,
            "nearest_detection_xyz": [float(v) for v in det[nearest]],
            "nearest_distance_um": float(dist[nearest]),
            "nearest_tolerance_um": float(tol[nearest]),
            "nearest_raw_radius_um": float(radius[nearest]),
            "second_nearest_distance_um": float(dist[order[1]]) if order.size > 1 else None,
            "n_detections_within_tolerance": int(within.size),
            "matched_detection_index": matched,
            "matched_detection_xyz": [float(v) for v in det[matched]] if matched is not None else None,
            "matched_distance_um": float(dist[matched]) if matched is not None else None,
            "matched_tolerance_um": float(tol[matched]) if matched is not None else None,
        }
        result.counts = {"n_detections_within_tolerance": int(within.size)}

        if matched is None:
            result.status = self.violation_severity.value
            result.flagged_node_ids = [int(prepared_morph.node_id[i])]
            result.flagged_node_coordinates = [tuple(float(v) for v in root)]
            result.message = (
                f"Root is not within the radius of any detected soma: the nearest of "
                f"{det.shape[0]} detections is {dist[nearest]:.1f} um away (radius "
                f"{tol[nearest]:.1f} um after clamping)."
            )
        else:
            result.message = (
                f"Root is {dist[matched]:.1f} um from detected soma #{matched} "
                f"(radius {tol[matched]:.1f} um after clamping)."
            )

        result.runtime_ms = (time.perf_counter() - t0) * 1000
        return result

    @staticmethod
    def _error(result, t0, message, measurements):
        result.status = "error"
        result.message = message
        result.measurements = measurements
        result.counts = measurements
        result.runtime_ms = (time.perf_counter() - t0) * 1000
        return result


register(RootNearDetectedSomaMetric())
