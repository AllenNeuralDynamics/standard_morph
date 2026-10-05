"""CCF brain-mesh metrics (post-registration).

Metric 2 -- Soma inside registered CCF brain mesh.
Metric 3 -- Nodes outside CCF brain mesh after registration.

Both operate in ``ccf_registered`` space and expect morphology coordinates in
microns. The atlas resolution defaults to 10 µm/voxel (the bundled Allen CCF);
set ``context.ccf_resolution`` only when supplying a custom atlas via
``resources["ccf_atlas_path"]`` or ``resources["ccf_annotation"]``.
"""
import time

import numpy as np

from standard_morph.metrics.base import Metric, Applicability, Severity
from standard_morph.models.qc_context import Space, ALL_MORPHOLOGY_KINDS
from standard_morph.models.qc_result import MetricResult
from standard_morph.registry import register
from standard_morph.atlas import load_ccf_annotation, coordinates_to_voxels, in_brain_mask

class _CcfMeshMetric(Metric):
    """Shared applicability and atlas resolution for CCF-mesh metrics."""

    violation_severity = Severity.REVIEW
    applicability = Applicability(
        spaces=frozenset({Space.CCF_REGISTERED}),
        morphology_kinds=ALL_MORPHOLOGY_KINDS,
        required_resources=frozenset(),
    )

    @staticmethod
    def _resolve_annotation(context):
        """Return ``(annotation_array, resolution)`` from the context."""
        resolution = context.ccf_resolution
        resources = context.resources or {}
        annotation = resources.get("ccf_annotation")
        if annotation is not None:
            return np.asarray(annotation), resolution
        annotation = load_ccf_annotation(
            resolution=resolution, atlas_path=resources.get("ccf_atlas_path")
        )
        return annotation, resolution


class NodesOutsideCcfMeshMetric(_CcfMeshMetric):
    name = "nodes_outside_ccf_mesh"
    display_name = "Nodes outside CCF brain mesh after registration"
    metric_number = 3
    requires_topology = False  # per-node voxel lookup; reads only xyz
    required_policy_keys = frozenset({"max_fraction_outside"})
    

    def evaluate(self, prepared_morph, context, policy):
        t0 = time.perf_counter()
        max_fraction = policy[self.name, "max_fraction_outside"]
        annotation, resolution = self._resolve_annotation(context)

        result = MetricResult(name=self.name, status="pass")
        result.thresholds_used = {"max_fraction_outside": max_fraction}

        voxels = coordinates_to_voxels(prepared_morph.xyz, resolution)
        outside_idx = np.flatnonzero(~in_brain_mask(voxels, annotation))
        n = prepared_morph.n
        n_outside = int(outside_idx.size)
        fraction = (n_outside / n) if n else 0.0

        result.measurements = {
            "n_nodes": n,
            "n_outside": n_outside,
            "fraction_outside": fraction,
        }
        result.value = fraction
        result.value_label = "fraction_outside"
        result.flagged_node_ids = [int(prepared_morph.node_id[i]) for i in outside_idx]
        result.flagged_node_coordinates = [
            tuple(float(v) for v in prepared_morph.xyz[i]) for i in outside_idx
        ]
        result.counts = {"n_outside": n_outside, "n_nodes": n}

        if fraction > max_fraction:
            result.status = self.violation_severity.value
            result.message = (
                f"{n_outside}/{n} nodes ({fraction:.1%}) fall outside the CCF brain "
                f"mesh, exceeding {max_fraction:.1%}."
            )
        else:
            result.message = (
                f"{n_outside}/{n} nodes ({fraction:.1%}) outside the CCF brain mesh, "
                f"within {max_fraction:.1%}."
            )
        result.runtime_ms = (time.perf_counter() - t0) * 1000
        return result


class SomaInsideCcfMeshMetric(_CcfMeshMetric):
    name = "soma_inside_ccf_mesh"
    display_name = "Soma inside registered CCF brain mesh"
    metric_number = 2

    def evaluate(self, prepared_morph, context, policy):
        t0 = time.perf_counter()
        annotation, resolution = self._resolve_annotation(context)
        result = MetricResult(name=self.name, status="pass")

        soma_idx = prepared_morph.soma_roots
        if soma_idx.size != 1:
            result.status = "error"
            result.message = (
                f"Expected exactly one soma (type 1, root); found {soma_idx.size}."
            )
            result.measurements = {"n_soma": int(soma_idx.size)}
            result.counts = {"n_soma": int(soma_idx.size)}
            result.runtime_ms = (time.perf_counter() - t0) * 1000
            return result

        i = int(soma_idx[0])
        voxel = coordinates_to_voxels(prepared_morph.xyz[i].reshape(1, 3), resolution)
        inside = bool(in_brain_mask(voxel, annotation)[0])

        v = voxel[0]
        shape = annotation.shape
        in_bounds = all(0 <= int(v[d]) < shape[d] for d in range(3))
        structure_id = int(annotation[v[0], v[1], v[2]]) if in_bounds else 0

        result.measurements = {
            "soma_inside_brain": inside,
            "soma_structure_id": structure_id,
            "soma_voxel": [int(x) for x in v],
        }
        result.counts = {"soma_inside_brain": int(inside)}

        if inside:
            result.message = "Soma is inside the CCF brain mesh."
        else:
            result.status = self.violation_severity.value
            result.flagged_node_ids = [int(prepared_morph.node_id[i])]
            result.flagged_node_coordinates = [tuple(float(x) for x in prepared_morph.xyz[i])]
            result.message = "Soma falls outside the CCF brain mesh."

        result.runtime_ms = (time.perf_counter() - t0) * 1000
        return result


register(NodesOutsideCcfMeshMetric())
register(SomaInsideCcfMeshMetric())
