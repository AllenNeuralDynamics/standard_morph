"""Export QC results as aind-data-schema ``QualityControl`` (``qc.json``).

Each :class:`~standard_morph.models.qc_result.MetricResult` becomes one
``QCMetric``:

* ``status``      -- ``pass`` -> Pass, ``fail`` -> Fail, anything else
  (``review``, ``error``, ``skipped``) -> Pending, i.e. needs a human.
* ``description`` -- the message, the thresholds used and, when it holds more
  than the headline value, the measurements.
* ``reference``   -- a ``js:neuroglancer?...`` reference rendered by the Zombie
  QC portal, with one ``action=goto:x,y,z`` per flagged node so each flagged
  coordinate can be jumped to. Coordinates are written as-is (raw SWC space).
  The ``image`` param defaults to ``context.resources["image_zarr_path"]``.

``counts``, ``artifacts`` and ``runtime_ms`` are not exported.

Requires the ``data-schema`` extra (``pip install standard_morph[data-schema]``).
"""
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional, Sequence, Union
from urllib.parse import quote, urlencode

from aind_data_schema.core.quality_control import (
    QCMetric,
    QCStatus,
    QualityControl,
    Stage,
    Status,
)
from aind_data_schema_models.modalities import Modality

from standard_morph.models.qc_context import QCContext
from standard_morph.models.qc_policy import PolicyRange
from standard_morph.models.qc_result import MetricResult
from standard_morph.models.qc_run import RunReport

QC_FILENAME = "qc.json"
IMAGE_RESOURCE_KEY = "image_zarr_path"

_STATUS_MAP = {"pass": Status.PASS, "fail": Status.FAIL}


def _fmt(value) -> str:
    if isinstance(value, PolicyRange):
        value = {"lo": value.lo, "hi": value.hi}
    if isinstance(value, dict):
        if set(value) == {"lo", "hi"}:
            return f"[{_fmt(value['lo'])}, {_fmt(value['hi'])}]"
        return "{" + ", ".join(f"{k}: {_fmt(v)}" for k, v in value.items()) + "}"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_fmt(v) for v in value) + "]"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


def _fmt_coord(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def build_description(result: MetricResult) -> Optional[str]:
    """Combine message, thresholds and (non-headline) measurements."""
    parts = []
    if result.message:
        parts.append(result.message)
    if result.thresholds_used:
        parts.append(
            "Thresholds: " + ", ".join(f"{k}={_fmt(v)}" for k, v in result.thresholds_used.items())
        )
    extra = {k: v for k, v in result.measurements.items() if k != result.value_label}
    if extra:
        parts.append("Measurements: " + ", ".join(f"{k}={_fmt(v)}" for k, v in extra.items()))
    return " | ".join(parts) or None


def build_neuroglancer_reference(
    result: MetricResult,
    *,
    image: Optional[str] = None,
    skeleton: Optional[str] = None,
    segments: Optional[Sequence[Union[int, str]]] = None,
) -> Optional[str]:
    """Build a ``js:neuroglancer`` reference for ``result``.

    ``image`` is an s3:// OME-Zarr and ``skeleton`` an s3:// precomputed
    skeleton source; both are optional. Returns ``None`` only when there is
    nothing to put in the reference.
    """
    params = []
    if image:
        params.append(("image", image))
    if skeleton:
        params.append(("skeleton", skeleton))
        if segments:
            params.append(("segments", ",".join(str(s) for s in segments)))
    for xyz in result.flagged_node_coordinates:
        params.append(("action", "goto:" + ",".join(_fmt_coord(v) for v in xyz)))
    if not params:
        return None
    return "js:neuroglancer?" + urlencode(params, quote_via=quote, safe=":/,")


def to_qc_metric(
    result: MetricResult,
    *,
    context: Optional[QCContext] = None,
    image: Optional[str] = None,
    skeleton: Optional[str] = None,
    segments: Optional[Sequence[Union[int, str]]] = None,
    modality=Modality.SPIM,
    stage: Stage = Stage.PROCESSING,
    evaluator: str = "Automated: standard_morph",
    timestamp: Optional[datetime] = None,
    tags: Optional[dict] = None,
) -> QCMetric:
    """Convert one :class:`MetricResult` into an aind-data-schema ``QCMetric``.

    ``image`` defaults to ``context.resources["image_zarr_path"]`` when given.
    """
    if context is not None:
        image = image or context.resources.get(IMAGE_RESOURCE_KEY)
    return QCMetric(
        name=result.name,
        modality=modality,
        stage=stage,
        value=None if result.value is None else float(result.value),
        status_history=[
            QCStatus(
                evaluator=evaluator,
                status=_STATUS_MAP.get(result.status, Status.PENDING),
                timestamp=timestamp or datetime.now(tz=timezone.utc),
            )
        ],
        description=build_description(result),
        reference=build_neuroglancer_reference(
            result,
            image=image,
            skeleton=skeleton,
            segments=segments,
        ),
        tags=tags or {},
    )


def to_quality_control(
    source: Union[RunReport, Iterable[MetricResult]],
    *,
    context: Optional[QCContext] = None,
    timestamp: Optional[datetime] = None,
    notes: Optional[str] = None,
    **metric_kwargs,
) -> QualityControl:
    """Build a ``QualityControl`` from a :class:`RunReport` or a list of results.

    For a ``RunReport``, metrics are tagged ``{"phase": "integrity" | "morphology"}``
    and grouped by phase. ``metric_kwargs`` are forwarded to :func:`to_qc_metric`.
    """
    timestamp = timestamp or datetime.now(tz=timezone.utc)
    if isinstance(source, RunReport):
        tagged = [(r, {"phase": "integrity"}) for r in source.integrity_results]
        tagged += [(r, {"phase": "morphology"}) for r in source.results]
        grouping = ["phase"]
    else:
        tagged = [(r, None) for r in source]
        grouping = []
    metrics = [
        to_qc_metric(r, context=context, timestamp=timestamp, tags=tags, **metric_kwargs)
        for r, tags in tagged
    ]
    return QualityControl(metrics=metrics, default_grouping=grouping, notes=notes)


def write_qc_json(
    source: Union[QualityControl, RunReport, Iterable[MetricResult]],
    output_path: Union[str, Path],
    **kwargs,
) -> Path:
    """Write ``qc.json`` and return its path.

    ``output_path`` may be a directory (``qc.json`` is written inside it) or a
    file path. ``kwargs`` are forwarded to :func:`to_quality_control`.
    """
    qc = source if isinstance(source, QualityControl) else to_quality_control(source, **kwargs)
    path = Path(output_path)
    if path.is_dir():
        path = path / QC_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(qc.model_dump_json(indent=2))
    return path
