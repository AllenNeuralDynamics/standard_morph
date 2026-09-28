"""Suite resolution, validation, two-phase execution, and aggregation.

`run_qc` is the single public entrypoint. It runs QC in two phases (see
``standard_morph.metrics.base`` for the rationale):

1. Resolve the requested suite / metric list, and fail-fast if any requested
   metric is incompatible with the context (wrong space, missing resource).
2. Load the raw SWC **table**.
3. **Input-integrity phase.** Run the buildability checks (always) plus any
   requested integrity metrics on the raw table. Each failure has a *scope*
   (``blocks_on_failure``): a ``BUILD`` failure means the morphology cannot be
   constructed; a ``TOPOLOGY`` failure means it builds but its tree structure is
   untrustworthy.
4. **Morphology-quality phase.** On a ``BUILD`` failure, skip every morphology
   metric. Otherwise build the `PreparedMorphology` once and run the metrics --
   but on a ``TOPOLOGY`` failure, skip the ones that need valid topology
   (``requires_topology``) while still running coordinate/attribute-only metrics.
   Skipped metrics are reported with status ``"skipped"``.
5. Aggregate everything into a `RunReport`.

This is what lets a batch of thousands of files stay robust: a malformed file
yields a normal report naming the integrity failure, never a stack trace.
Exceptions are reserved for genuine bugs, not for bad input.
"""
import copy
import os
import time
from dataclasses import replace
from datetime import datetime, timezone

import pandas as pd

import standard_morph.metrics  # noqa: F401  -- ensure metrics self-register
from standard_morph.registry import REGISTRY
from standard_morph import suites as suites_module
from standard_morph.swc_io import read_swc
from standard_morph.preparation import PreparedMorphology
from standard_morph.policies import get_policy
from standard_morph.exceptions import IncompatibleMetricContextError, MissingPolicyValuesError
from standard_morph.metrics.base import EvaluationPhase, BlockScope
from standard_morph.models.qc_policy import Policy
from standard_morph.models.qc_result import MetricResult
from standard_morph.models.qc_run import RunReport, SCHEMA_VERSION

#: Integrity checks that are *always* run before building the morphology,
#: regardless of the requested suite -- they are the preconditions
#: ``PreparedMorphology.from_dataframe`` needs, so building is safe once they
#: pass. Order matters: later checks assume earlier ones held.
BUILDABILITY_METRICS = [
    "required_columns", "non_empty", "castable_columns", "unique_node_ids",
    "valid_parent_references", "acyclic",
]


def _get_version():
    try:
        from importlib.metadata import version

        return version("standard_morph")
    except Exception:
        return "unknown"


def _resolve_metric_names(suite_name, metrics):
    if (suite_name is None) == (metrics is None):
        raise ValueError("Exactly one of 'suite_name' or 'metrics' must be provided.")
    if suite_name is not None:
        return suites_module.resolve_suite(suite_name)
    return list(metrics)


def _resolve_metrics(metric_names):
    unknown = [n for n in metric_names if n not in REGISTRY]
    if unknown:
        raise KeyError(
            f"Unknown metric(s): {unknown}. Registered metrics: {REGISTRY.names()}"
        )
    return [REGISTRY.get(n) for n in metric_names]


def _validate_applicability(metric_objs, context):
    """Fail-fast: collect every incompatibility, then raise once if any."""
    problems = []
    for metric in metric_objs:
        try:
            metric.validate_context(context)
        except IncompatibleMetricContextError as exc:
            problems.append(str(exc))
    if problems:
        raise IncompatibleMetricContextError(
            "One or more requested metrics are incompatible with the run "
            "context:\n  - " + "\n  - ".join(problems)
        )


def _validate_policy(metric_objs, policy):
    """Fail-fast: collect every missing policy key across all metrics, then raise once."""
    missing = []
    for metric in metric_objs:
        thresholds = policy.for_metric(metric.name)
        for key in metric.required_policy_keys:
            if key not in thresholds:
                missing.append((metric.name, key))
    if missing:
        lines = [f"  - {m}.{k}" for m, k in missing]
        raise MissingPolicyValuesError(
            f"Policy '{policy.version}' is missing required threshold(s):\n"
            + "\n".join(lines)
        )


def _load_table(input_data):
    """Normalise input into ``(swc_df, input_ref, prebuilt_morphology)``.

    * a path or DataFrame yields a raw ``swc_df`` for the integrity phase;
    * an already-built ``PreparedMorphology`` skips the integrity phase (it was
      built by the caller, so buildability is implied) -- ``swc_df`` is None and
      the prebuilt object is used directly for the morphology phase.
    """
    if isinstance(input_data, PreparedMorphology):
        return None, None, input_data
    if isinstance(input_data, pd.DataFrame):
        return input_data, None, None
    if isinstance(input_data, str):
        return read_swc(input_data), input_data, None
    raise TypeError(
        "input_data must be a path (str), a pandas DataFrame, or a "
        f"PreparedMorphology; got {type(input_data)}."
    )


_BUILD_BLOCKED_REASON = "morphology could not be built (a BUILD-scope integrity check failed)"
_TOPOLOGY_BLOCKED_REASON = (
    "topology is unreliable (e.g. duplicate node ids); this metric requires valid topology"
)


def _skipped(name, reason):
    return MetricResult(name=name, status="skipped", message=f"not run: {reason}")


def _summarise(results):
    """Summarise the morphology-quality results (may include skipped ones)."""
    n_pass = sum(r.status == "pass" for r in results)
    n_fail = sum(r.status == "fail" for r in results)
    n_review = sum(r.status == "review" for r in results)
    n_error = sum(r.status == "error" for r in results)
    n_skipped = sum(r.status == "skipped" for r in results)
    # A "review" flag (something a human must judge) or a "skipped" check (did
    # not run) both leave the run short of a clean automated pass, but neither is
    # an objective failure -- so they roll up to "incomplete", below "fail".
    if n_error:
        overall = "error"
    elif n_fail:
        overall = "fail"
    elif n_review or n_skipped:
        overall = "incomplete"  # needs human oversight and/or some checks did not run
    else:
        overall = "pass"
    return {
        "n_metrics": len(results),
        "n_pass": n_pass,
        "n_fail": n_fail,
        "n_review": n_review,
        "n_error": n_error,
        "n_skipped": n_skipped,
        "overall_status": overall,
    }


def _run_integrity_phase(swc_df, input_run_names, context, policy):
    """Run integrity metrics; stop early only on a BUILD-scope failure.

    A BUILD-scope failure means the morphology cannot be constructed, so later
    checks (and the build itself) are unsafe -- stop. A TOPOLOGY-scope failure
    is recorded but the phase continues, since it does not prevent building.

    Returns ``(integrity_results, build_blocked, topology_blocked)``.
    """
    integrity_results = []
    build_blocked = False
    topology_blocked = False
    for name in input_run_names:
        metric = REGISTRY.get(name)
        result = metric.evaluate(swc_df, context, policy)
        integrity_results.append(result)
        if result.status in ("fail", "error"):
            if metric.blocks_on_failure == BlockScope.BUILD:
                build_blocked = True
                break
            if metric.blocks_on_failure == BlockScope.TOPOLOGY:
                topology_blocked = True
    return integrity_results, build_blocked, topology_blocked


def _resolve_policy(context, policy_version, policy):
    """Return ``(policy, version)`` from a custom ``Policy`` or a built-in version."""
    if policy is None:
        version = policy_version or context.policy_version
        return get_policy(version), version
    if policy_version is not None:
        raise ValueError("Provide at most one of 'policy' or 'policy_version'.")
    if not isinstance(policy, Policy):
        raise TypeError(f"policy must be a Policy instance; got {type(policy)}.")
    return policy, policy.version


def run_qc(input_data, context, suite_name=None, metrics=None, policy_version=None, policy=None):
    """Run a QC suite (or explicit metric list) against a morphology.

    Parameters
    ----------
    input_data : str | pandas.DataFrame | PreparedMorphology
        An SWC file path, a canonical SWC DataFrame, or an already-prepared
        morphology.
    context : QCContext
        Run context (coordinate space, morphology kind, resources, policy).
    suite_name : str, optional
        Name of a built-in suite. Mutually exclusive with ``metrics``.
    metrics : list[str], optional
        Explicit ordered list of metric names. Mutually exclusive with
        ``suite_name``.
    policy_version : str, optional
        Overrides ``context.policy_version`` when provided.
    policy : Policy, optional
        A custom :class:`~standard_morph.models.qc_policy.Policy` to use instead
        of a built-in version (e.g. one derived from ``get_policy("policy_v1")``
        with pipeline-specific thresholds). Its ``version`` is recorded in the
        report. Mutually exclusive with ``policy_version``; overrides
        ``context.policy_version``.

    Returns
    -------
    RunReport
        With ``integrity_results`` (input phase) and ``results`` (morphology
        phase, possibly all ``"skipped"``).
    """
    t0 = time.perf_counter()

    policy, effective_policy_version = _resolve_policy(context, policy_version, policy)

    # When given a file path, expose its basename to filename-aware metrics
    # (e.g. filename_format) via resources["filename"], on a per-run copy so the
    # caller's context is never mutated (important for batch reuse). An
    # explicitly-provided resources["filename"] always wins.
    if isinstance(input_data, str) and "filename" not in context.resources:
        context = replace(
            context,
            resources={**context.resources, "filename": os.path.basename(input_data)},
        )

    metric_names = _resolve_metric_names(suite_name, metrics)
    metric_objs = _resolve_metrics(metric_names)

    # Fail fast on context incompatibility (wrong space / missing resource)
    # and missing policy keys.
    _validate_applicability(metric_objs, context)
    _validate_policy(metric_objs, policy)

    # Split the requested metrics by phase.
    requested_input = [
        n for n in metric_names
        if REGISTRY.get(n).evaluation_phase == EvaluationPhase.INPUT_INTEGRITY
    ]
    morph_names = [
        n for n in metric_names
        if REGISTRY.get(n).evaluation_phase == EvaluationPhase.MORPHOLOGY_QUALITY
    ]
    # Buildability always runs first, then any additionally-requested input metrics.
    extra_input_names = [n for n in requested_input if n not in BUILDABILITY_METRICS]
    input_run_names = list(BUILDABILITY_METRICS) + extra_input_names

    swc_df, input_ref, prebuilt = _load_table(input_data)

    # -- Phase 1: input integrity --
    if swc_df is not None:
        # Raw table available: run buildability plus any requested input metrics.
        integrity_results, build_blocked, topology_blocked = _run_integrity_phase(
            swc_df, input_run_names, context, policy
        )
    else:
        # A PreparedMorphology was passed in: the raw SWC table is unavailable,
        # so every integrity metric is skipped. Buildability is implied by the
        # fact that the caller already built the morphology.
        _pm_skip_reason = "raw SWC table not available (PreparedMorphology was passed as input)"
        integrity_results = [_skipped(n, _pm_skip_reason) for n in input_run_names]
        build_blocked = False
        topology_blocked = False

    # -- Phase 2: morphology quality --
    # A BUILD-scope failure skips everything (no arrays to run on). A
    # TOPOLOGY-scope failure skips only the metrics that need valid topology;
    # coordinate/attribute-only metrics (requires_topology=False) still run.
    if build_blocked:
        results = [_skipped(n, _BUILD_BLOCKED_REASON) for n in morph_names]
        morphology_evaluated = False
    else:
        prepared_morph = prebuilt if prebuilt is not None else PreparedMorphology.from_dataframe(swc_df)
        results = []
        for n in morph_names:
            metric = REGISTRY.get(n)
            if topology_blocked and metric.requires_topology:
                results.append(_skipped(n, _TOPOLOGY_BLOCKED_REASON))
            else:
                results.append(metric.evaluate(prepared_morph, context, policy))
        morphology_evaluated = True

    return RunReport(
        schema_version=SCHEMA_VERSION,
        standard_morph_version=_get_version(),
        generated_at=datetime.now(timezone.utc).isoformat(),
        space=context.space.value,
        morphology_kind=context.morphology_kind.value,
        ccf_resolution=context.ccf_resolution,
        policy_version=effective_policy_version,
        suite_name=suite_name,
        requested_metrics=metric_names,
        input_ref=input_ref,
        policy_thresholds={
            n: copy.deepcopy(policy.for_metric(n)) for n in input_run_names + morph_names
        },
        integrity_results=integrity_results,
        results=results,
        morphology_evaluated=morphology_evaluated,
        summary=_summarise(results),
        runtime_ms=(time.perf_counter() - t0) * 1000,
    )
