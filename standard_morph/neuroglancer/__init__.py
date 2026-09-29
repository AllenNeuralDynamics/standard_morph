"""Neuroglancer layers and links for node-level QC errors.

Typical batch use::

    from standard_morph import run_qc
    from standard_morph.neuroglancer import NeuroglancerExporter

    exporter = NeuroglancerExporter(scale_um=(0.748, 0.748, 1.0))
    for path in swc_paths:
        report = run_qc(path, context, metrics=metrics, policy=policy)
        segment_id = exporter.add(path, report)
    exporter.write("out/ng")            # upload out/ng to s3://bucket/prefix
    url = exporter.batch_url("s3://bucket/prefix")

``write`` produces a skeleton source (``skeletons/``), with a per-node flag
attribute for every metric, and one point-annotation source per metric
(``annotations/<metric>/``). Writing annotations needs the ``neuroglancer``
extra; building states and links does not.
"""
import os
from pathlib import Path

import pandas as pd

from standard_morph.preparation import PreparedMorphology
from standard_morph.swc_io import read_swc
from standard_morph.neuroglancer.annotations import write_error_annotations
from standard_morph.neuroglancer.coordinates import coordinate_space, validate_scale
from standard_morph.neuroglancer.records import (
    FLAG_STATUSES, FlaggedNode, NeuronRecord, SkeletonData, flagged_nodes,
)
from standard_morph.neuroglancer.skeletons import flag_attribute, write_skeletons
from standard_morph.neuroglancer.state import (
    DEFAULT_BASE_URL, DEFAULT_SKELETON_SHADER, build_state, state_to_url,
)

SKELETON_DIR = "skeletons"
ANNOTATION_DIR = "annotations"


class NeuroglancerExporter:
    """Accumulate QC runs, then write their layers and build links.

    Parameters
    ----------
    scale_um : sequence of 3 floats
        Micrometers per SWC unit along x, y, z: ``(1, 1, 1)`` for SWCs in
        micrometers, the voxel size for SWCs in voxel indices.
    flag_statuses : sequence of str
        Result statuses whose flagged nodes are shown.
    """

    def __init__(self, scale_um, flag_statuses=FLAG_STATUSES):
        self.scale_um = tuple(float(v) for v in validate_scale(scale_um))
        self.flag_statuses = tuple(flag_statuses)
        self.records = []

    def add(self, input_data, report, label=None):
        """Record one QC run and return the neuron's segment id (1, 2, ...).

        ``input_data`` is what was passed to ``run_qc``: an SWC path, a
        DataFrame, or a ``PreparedMorphology``. When a BUILD integrity check
        failed (``report.morphology_evaluated`` is False) the neuron gets no
        skeleton, but its flagged nodes that have coordinates are still shown.
        """
        segment_id = len(self.records) + 1
        skeleton = None
        if report.morphology_evaluated:
            skeleton = SkeletonData.from_morphology(_as_morphology(input_data))
        self.records.append(NeuronRecord(
            segment_id=segment_id,
            label=label or _default_label(input_data, report, segment_id),
            skeleton=skeleton,
            flags=flagged_nodes(report, self.flag_statuses, skeleton),
        ))
        return segment_id

    @property
    def metrics(self):
        """Metrics that flagged at least one node, in first-seen order."""
        seen = {}
        for record in self.records:
            for metric in record.flags:
                seen.setdefault(metric, None)
        return list(seen)

    def record(self, segment_id):
        return self.records[segment_id - 1]

    def write(self, out_dir):
        """Write ``skeletons/`` and ``annotations/<metric>/`` under ``out_dir``.

        Returns ``{"skeletons": [segment ids], "annotations": [metrics]}``.
        """
        out_dir = Path(out_dir)
        metrics = self.metrics
        segments = write_skeletons(self.records, out_dir / SKELETON_DIR, self.scale_um, metrics)
        annotated = write_error_annotations(
            self.records, out_dir / ANNOTATION_DIR, self.scale_um, metrics
        ) if metrics else []
        return {"skeletons": segments, "annotations": annotated}

    # ------------------------------------------------------------ states/links
    def sources(self, source_url, metrics=None):
        """Return ``(skeleton_source, {metric: annotation_source})`` for layers
        written by :meth:`write` and published at ``source_url``."""
        base = source_url.rstrip("/")
        metrics = self.metrics if metrics is None else metrics
        return (
            f"precomputed://{base}/{SKELETON_DIR}",
            {m: f"precomputed://{base}/{ANNOTATION_DIR}/{m}" for m in metrics},
        )

    def batch_state(self, source_url, image_source=None):
        """Viewer state showing every neuron and every metric's errors."""
        skeleton_source, annotation_sources = self.sources(source_url)
        segments = [r.segment_id for r in self.records if r.skeleton is not None]
        first = next((r.position for r in self.records if r.position is not None), None)
        return build_state(
            skeleton_source if segments else None, annotation_sources, self.scale_um,
            image_source=image_source, segments=segments, position=first,
        )

    def neuron_state(self, segment_id, source_url, image_source=None):
        """Viewer state showing one neuron, centered on its first flagged node
        (or its soma), with a layer for each metric that flagged it."""
        record = self.record(segment_id)
        skeleton_source, annotation_sources = self.sources(source_url, metrics=list(record.flags))
        has_skeleton = record.skeleton is not None
        return build_state(
            skeleton_source if has_skeleton else None, annotation_sources, self.scale_um,
            image_source=image_source, segments=[segment_id] if has_skeleton else [],
            position=record.position,
        )

    def batch_url(self, source_url, image_source=None, base_url=DEFAULT_BASE_URL):
        return state_to_url(self.batch_state(source_url, image_source), base_url)

    def neuron_url(self, segment_id, source_url, image_source=None, base_url=DEFAULT_BASE_URL):
        return state_to_url(self.neuron_state(segment_id, source_url, image_source), base_url)


def _as_morphology(input_data):
    if isinstance(input_data, PreparedMorphology):
        return input_data
    if isinstance(input_data, pd.DataFrame):
        return PreparedMorphology.from_dataframe(input_data)
    if isinstance(input_data, (str, os.PathLike)):
        return PreparedMorphology.from_dataframe(read_swc(str(input_data)))
    raise TypeError(
        "input_data must be a path, a pandas DataFrame, or a PreparedMorphology; "
        f"got {type(input_data)}."
    )


def _default_label(input_data, report, segment_id):
    path = report.input_ref or (input_data if isinstance(input_data, (str, os.PathLike)) else None)
    return Path(path).stem if path else f"neuron_{segment_id}"


__all__ = [
    "NeuroglancerExporter",
    "NeuronRecord",
    "SkeletonData",
    "FlaggedNode",
    "flagged_nodes",
    "write_skeletons",
    "write_error_annotations",
    "flag_attribute",
    "build_state",
    "state_to_url",
    "coordinate_space",
    "DEFAULT_BASE_URL",
    "DEFAULT_SKELETON_SHADER",
    "SKELETON_DIR",
    "ANNOTATION_DIR",
]
