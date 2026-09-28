"""Run-level aggregate report object.

`RunReport` is the structured output of a QC run. It reflects the two-phase
model (see ``standard_morph.metrics.base``):

* ``integrity_results`` -- the input-integrity phase (did the raw SWC table pass
  the checks needed to build a morphology at all?).
* ``results``           -- the morphology-quality phase (the traditional QC
  metrics). When a blocking integrity check failed, these were not run and each
  carries status ``"skipped"``; ``morphology_evaluated`` is then ``False``.

Keeping the two phases in separate lists means a malformed file and a clean file
produce the *same shape* of report -- only the statuses differ. ``to_dict``
yields a plain, JSON-serialisable structure suitable for a file or database, and
includes the derived ``integrity_ok`` / ``passed`` verdicts for convenience.
"""
from dataclasses import dataclass, asdict, field
from typing import List, Optional

from standard_morph.models.qc_result import MetricResult

#: Bump when the report structure changes in a breaking way.
#: 1.0 -> 1.1: added the input-integrity phase (integrity_results,
#: morphology_evaluated) and the "skipped" metric status.
#: 1.1 -> 1.2: added policy_thresholds (the policy values each executed metric
#: ran under), so a report is self-describing even for a custom policy.
SCHEMA_VERSION = "1.2"


@dataclass
class RunReport:
    """Structured output of a complete QC run.

    Contains results from both the input-integrity phase and the
    morphology-quality phase. A malformed file and a clean file produce the
    same shape of report — only the statuses differ — so batch processing
    never needs special-case logic for broken inputs.

    Use :meth:`to_dict` to obtain a plain, JSON-serialisable representation
    suitable for writing to a file or database.

    Attributes
    ----------
    schema_version : str
        Version of the report JSON schema (see :data:`SCHEMA_VERSION`).
        Bump this when the dict structure changes in a breaking way.
    standard_morph_version : str
        Version of the ``standard_morph`` package that produced this report.
    integrity_results : list[MetricResult]
        Results from the input-integrity phase (always run first).
    results : list[MetricResult]
        Results from the morphology-quality phase, or ``"skipped"`` entries
        when a blocking integrity failure prevented the morphology from being
        built.
    morphology_evaluated : bool
        ``False`` when a blocking integrity check failed and the morphology
        phase was skipped entirely.
    summary : dict
        Counts and ``overall_status`` over the morphology-quality results.
    policy_thresholds : dict
        ``{metric_name: {key: value}}`` from the active policy for every metric
        in the run (the always-run buildability checks plus the requested
        metrics), so the report records the exact thresholds, not just
        ``policy_version``. ``PolicyRange`` values become ``{"lo", "hi"}`` in
        :meth:`to_dict`.
    """

    schema_version: str
    standard_morph_version: str
    generated_at: str
    space: str
    morphology_kind: str
    ccf_resolution: Optional[int]  # microns/voxel, when space == ccf_registered
    policy_version: str
    suite_name: Optional[str]
    requested_metrics: List[str]
    input_ref: Optional[str]
    coordinate_scale: tuple  # (sx, sy, sz) multiplier applied to raw XYZ before metric evaluation
    #: Active policy values for every metric in the run, keyed by metric name.
    policy_thresholds: dict = field(default_factory=dict)
    #: Input-integrity phase results (always run, before the morphology is built).
    integrity_results: List[MetricResult] = field(default_factory=list)
    #: Morphology-quality phase results (or "skipped" ones, if integrity blocked).
    results: List[MetricResult] = field(default_factory=list)
    #: False when a blocking integrity failure meant the morphology phase was skipped.
    morphology_evaluated: bool = True
    #: Summary over the morphology-quality results.
    summary: dict = field(default_factory=dict)
    runtime_ms: float = 0.0

    @property
    def integrity_ok(self):
        """True if every input-integrity check passed."""
        return all(r.status == "pass" for r in self.integrity_results)

    @property
    def passed(self):
        """True only if the input was valid *and* every morphology check passed."""
        return self.integrity_ok and self.summary.get("overall_status") == "pass"

    def to_dict(self):
        """Return a plain, JSON-serialisable dict of the whole report.

        All dataclass fields are included; ``PolicyRange`` values in
        ``policy_thresholds`` become ``{"lo", "hi"}`` dicts. Coordinate tuples in
        ``flagged_node_coordinates`` are converted to lists so the result
        round-trips through ``json.dumps`` without a custom encoder.
        The derived properties :attr:`integrity_ok` and :attr:`passed` are
        also included as top-level keys for convenience.
        """
        d = asdict(self)
        # Coordinate tuples -> lists for clean JSON, across both result lists.
        for key in ("integrity_results", "results"):
            for r in d[key]:
                r["flagged_node_coordinates"] = [list(c) for c in r["flagged_node_coordinates"]]
        # Surface the derived verdicts (properties are not included by asdict).
        d["integrity_ok"] = self.integrity_ok
        d["passed"] = self.passed
        return d
