import json
from urllib.parse import parse_qs

import pytest

pytest.importorskip("aind_data_schema")

from aind_data_schema.core.quality_control import Status  # noqa: E402

from standard_morph.data_schema import (  # noqa: E402
    build_description,
    build_neuroglancer_reference,
    to_qc_metric,
    to_quality_control,
    write_qc_json,
)
from standard_morph.models import MetricResult, QCContext  # noqa: E402

IMAGE = "s3://bucket/image.ome.zarr"


def _failing():
    return MetricResult(
        name="edge_length",
        status="fail",
        message="2 edges too long.",
        value=0.5,
        value_label="fraction_edges_over_threshold",
        thresholds_used={"max_length_um": {"lo": 1.0, "hi": 50.0}},
        measurements={"fraction_edges_over_threshold": 0.5, "n_over": 2},
        flagged_node_ids=[3, 7],
        flagged_node_coordinates=[(1.0, 2.5, 3.0), (10.0, 20.0, 30.125)],
        counts={"n": 2},
        artifacts=[{"type": "image", "path": "x.png"}],
        runtime_ms=12.0,
    )


def _params(reference):
    assert reference.startswith("js:neuroglancer?")
    return parse_qs(reference.split("?", 1)[1])


def test_description_combines_message_thresholds_and_extra_measurements():
    desc = build_description(_failing())
    assert desc == (
        "2 edges too long. | Thresholds: max_length_um=[1, 50] | Measurements: n_over=2"
    )


def test_description_omits_measurements_holding_only_value_label():
    r = MetricResult(
        name="m", value=1.0, value_label="max", measurements={"max": 1.0}, message="ok"
    )
    assert build_description(r) == "ok"


def test_reference_has_goto_action_per_flagged_coordinate():
    ref = build_neuroglancer_reference(_failing(), image=IMAGE)
    params = _params(ref)
    assert params["image"] == [IMAGE]
    assert params["action"] == ["goto:1,2.5,3", "goto:10,20,30.125"]


def test_reference_without_image_or_skeleton():
    ref = build_neuroglancer_reference(_failing())
    assert _params(ref) == {"action": ["goto:1,2.5,3", "goto:10,20,30.125"]}
    assert build_neuroglancer_reference(MetricResult(name="m")) is None


def test_qc_metric_uses_context_image_without_rescaling():
    ctx = QCContext(space="image_space", resources={"image_zarr_path": IMAGE},
                    coordinate_scale=(2.0, 2.0, 2.0))
    metric = to_qc_metric(_failing(), context=ctx, skeleton="s3://bucket/skel", segments=[1])
    params = _params(metric.reference)
    assert params["image"] == [IMAGE]
    assert params["skeleton"] == ["s3://bucket/skel"]
    assert params["segments"] == ["1"]
    assert params["action"][0] == "goto:1,2.5,3"
    assert metric.status.status == Status.FAIL
    assert metric.value == 0.5


@pytest.mark.parametrize("status,expected", [
    ("pass", Status.PASS), ("review", Status.PENDING),
    ("error", Status.PENDING), ("skipped", Status.PENDING),
])
def test_status_mapping(status, expected):
    assert to_qc_metric(MetricResult(name="m", status=status)).status.status == expected


def test_write_qc_json(tmp_path):
    results = [_failing(), MetricResult(name="single_root")]
    path = write_qc_json(results, tmp_path, image=IMAGE)
    assert path == tmp_path / "qc.json"
    data = json.loads(path.read_text())
    assert [m["name"] for m in data["metrics"]] == ["edge_length", "single_root"]
    assert data["metrics"][1]["reference"] == f"js:neuroglancer?image={IMAGE}"


def test_write_qc_json_without_image_or_skeleton(tmp_path):
    path = write_qc_json([_failing(), MetricResult(name="single_root")], tmp_path / "qc.json")
    data = json.loads(path.read_text())
    assert data["metrics"][0]["reference"].startswith("js:neuroglancer?action=goto:")
    assert data["metrics"][1]["reference"] is None


def test_quality_control_from_list_has_no_grouping():
    qc = to_quality_control([MetricResult(name="m")])
    assert qc.default_grouping == []
