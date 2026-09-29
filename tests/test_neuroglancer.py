"""Tests for the Neuroglancer layer writers and link builder."""
import json
import struct
import tempfile
import unittest
from pathlib import Path

import numpy as np

from standard_morph import Policy, QCContext, Space, MorphologyKind, run_qc
from standard_morph.neuroglancer import (
    ANNOTATION_DIR, SKELETON_DIR, NeuroglancerExporter, build_state, state_to_url,
)
from standard_morph.neuroglancer.coordinates import skeleton_transform_nm

try:
    import neuroglancer
except ImportError:  # the extra needs Python >= 3.10
    neuroglancer = None

needs_neuroglancer = unittest.skipIf(neuroglancer is None, "neuroglancer extra not installed")

SWC_DIR = Path(__file__).parent / "swcs"
SOURCE = "s3://bucket/prefix"
SCALE = (0.5, 0.5, 2.0)

# Soma, then an axon whose 3 -> 4 edge is 80 units long; node 4 is flagged.
LONG_EDGE_SWC = (
    "1 1 0 0 0 1 -1\n"
    "2 2 10 0 0 1 1\n"
    "3 2 20 0 0 1 2\n"
    "4 2 100 0 0 1 3\n"
    "5 2 110 0 0 1 4\n"
)
# A non-numeric coordinate fails castable_columns, a BUILD-scope check.
UNBUILDABLE_SWC = "1 1 0 0 0 1 -1\n2 2 abc 0 0 1 1\n"

CONTEXT = QCContext(space=Space.IMAGE_SPACE, morphology_kind=MorphologyKind.MERGED)
POLICY = Policy(version="test", thresholds={"edge_length": {"max_length_um": 30.0}})


def _run(path):
    return run_qc(str(path), CONTEXT, metrics=["edge_length"], policy=POLICY)


def _decode_skeleton(data, attribute_names):
    n_vertices, n_edges = struct.unpack_from("<II", data)
    offset = 8
    vertices = np.frombuffer(data, "<f4", n_vertices * 3, offset).reshape(-1, 3)
    offset += vertices.nbytes
    edges = np.frombuffer(data, "<u4", n_edges * 2, offset).reshape(-1, 2)
    offset += edges.nbytes
    attributes = {}
    for name in attribute_names:
        attributes[name] = np.frombuffer(data, "<f4", n_vertices, offset)
        offset += n_vertices * 4
    assert offset == len(data)
    return vertices, edges, attributes


class _ExporterCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.long_edge = self.tmp / "long_edge.swc"
        self.long_edge.write_text(LONG_EDGE_SWC)
        self.exporter = NeuroglancerExporter(SCALE)

    def tearDown(self):
        self._tmp.cleanup()


class TestRecords(_ExporterCase):
    def test_add_collects_flagged_nodes(self):
        report = _run(self.long_edge)
        segment_id = self.exporter.add(str(self.long_edge), report)
        record = self.exporter.record(segment_id)
        self.assertEqual(segment_id, 1)
        self.assertEqual(record.label, "long_edge")
        self.assertEqual(self.exporter.metrics, ["edge_length"])
        self.assertEqual([n.node_id for n in record.flags["edge_length"]], [4])
        self.assertEqual(record.flags["edge_length"][0].xyz, (100.0, 0.0, 0.0))
        self.assertEqual(record.position, (100.0, 0.0, 0.0))

    def test_clean_neuron_is_centered_on_soma(self):
        clean = self.tmp / "clean.swc"
        clean.write_text("1 1 5 6 7 1 -1\n2 2 10 6 7 1 1\n")
        self.exporter.add(str(clean), _run(clean))
        record = self.exporter.record(1)
        self.assertEqual(record.flags, {})
        self.assertEqual(record.position, (5.0, 6.0, 7.0))

    def test_unbuildable_file_has_no_skeleton(self):
        bad = self.tmp / "bad.swc"
        bad.write_text(UNBUILDABLE_SWC)
        report = _run(bad)
        self.assertFalse(report.morphology_evaluated)
        self.exporter.add(str(bad), report)
        self.assertIsNone(self.exporter.record(1).skeleton)

    def test_nodes_without_coordinates_placed_from_skeleton(self):
        # valid_parent_references flags node ids only; the skeleton places them.
        dangling = self.tmp / "dangling.swc"
        dangling.write_text(LONG_EDGE_SWC + "6 3 1 2 3 1 99\n")
        report = _run(dangling)
        result = next(r for r in report.integrity_results if r.name == "valid_parent_references")
        self.assertEqual((result.flagged_node_ids, result.flagged_node_coordinates), ([6], []))
        self.exporter.add(str(dangling), report)
        nodes = self.exporter.record(1).flags["valid_parent_references"]
        self.assertEqual([(n.node_id, n.xyz, n.status) for n in nodes], [(6, (1.0, 2.0, 3.0), "fail")])

    def test_invalid_scale_rejected(self):
        for scale in ((1, 1), (1, 0, 1), (1, -1, 1), (1, float("nan"), 1)):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                NeuroglancerExporter(scale)


class TestSkeletons(_ExporterCase):
    @needs_neuroglancer
    def test_skeleton_matches_swc_and_flags(self):
        self.exporter.add(str(self.long_edge), _run(self.long_edge))
        real = SWC_DIR / "N024-648434-CONSENSUS.swc"
        real_report = _run(real)
        self.exporter.add(str(real), real_report)
        written = self.exporter.write(self.tmp / "ng")
        self.assertEqual(written["skeletons"], [1, 2])

        skeleton_dir = self.tmp / "ng" / SKELETON_DIR
        info = json.loads((skeleton_dir / "info").read_text())
        self.assertEqual(info["@type"], "neuroglancer_skeletons")
        self.assertEqual(info["transform"], skeleton_transform_nm(SCALE))
        names = [a["id"] for a in info["vertex_attributes"]]
        self.assertEqual(names, ["radius", "n_flags", "flag_edge_length"])

        vertices, edges, attributes = _decode_skeleton((skeleton_dir / "1").read_bytes(), names)
        np.testing.assert_array_equal(vertices[:, 0], [0, 10, 20, 100, 110])
        self.assertEqual(sorted(map(tuple, edges.tolist())), [(1, 0), (2, 1), (3, 2), (4, 3)])
        np.testing.assert_array_equal(attributes["flag_edge_length"], [0, 0, 0, 1, 0])
        np.testing.assert_array_equal(attributes["n_flags"], [0, 0, 0, 1, 0])

        # On a real file, exactly the reported node ids are flagged.
        from standard_morph.swc_io import read_swc
        df = read_swc(str(real))
        _, edges, attributes = _decode_skeleton((skeleton_dir / "2").read_bytes(), names)
        self.assertEqual(len(edges), len(df) - 1)
        result = next(r for r in real_report.results if r.name == "edge_length")
        flagged = set(df["node_id"].to_numpy()[attributes["flag_edge_length"] == 1].tolist())
        self.assertEqual(flagged, set(result.flagged_node_ids))

        props = json.loads((skeleton_dir / "segment_properties" / "info").read_text())["inline"]
        self.assertEqual(props["ids"], ["1", "2"])
        self.assertEqual(props["properties"][0]["values"], ["long_edge", "N024-648434-CONSENSUS"])
        counts = next(p for p in props["properties"] if p["id"] == "n_flagged_edge_length")
        self.assertEqual(counts["values"], [1, len(result.flagged_node_ids)])

    def test_no_flags_writes_skeleton_without_annotations(self):
        clean = self.tmp / "clean.swc"
        clean.write_text("1 1 0 0 0 1 -1\n2 2 10 0 0 1 1\n")
        self.exporter.add(str(clean), _run(clean))
        written = self.exporter.write(self.tmp / "ng")
        self.assertEqual(written, {"skeletons": [1], "annotations": []})
        self.assertFalse((self.tmp / "ng" / ANNOTATION_DIR).exists())


@needs_neuroglancer
class TestAnnotations(_ExporterCase):
    def test_annotations_hold_flagged_nodes(self):
        self.exporter.add(str(self.long_edge), _run(self.long_edge))
        self.assertEqual(self.exporter.write(self.tmp / "ng")["annotations"], ["edge_length"])

        path = self.tmp / "ng" / ANNOTATION_DIR / "edge_length"
        info = json.loads((path / "info").read_text())
        self.assertEqual(info["annotation_type"], "point")
        self.assertEqual(info["dimensions"]["z"], [2e-06, "m"])
        self.assertEqual([p["id"] for p in info["properties"]], ["node_id", "status"])
        self.assertEqual(info["relationships"], [{"id": "neuron", "key": "rel_neuron"}])

        data = (path / "by_id" / "0").read_bytes()
        self.assertEqual(struct.unpack_from("<3f", data), (100.0, 0.0, 0.0))
        self.assertEqual(struct.unpack_from("<I", data, 12)[0], 4)  # node_id
        self.assertEqual(struct.unpack_from("<Q", data, len(data) - 8)[0], 1)  # neuron
        self.assertTrue((path / "rel_neuron" / "1").exists())

    def test_unbuildable_file_still_writes(self):
        bad = self.tmp / "bad.swc"
        bad.write_text(UNBUILDABLE_SWC)
        self.exporter.add(str(bad), _run(bad))
        self.exporter.add(str(self.long_edge), _run(self.long_edge))
        written = self.exporter.write(self.tmp / "ng")
        self.assertEqual(written["skeletons"], [2])
        self.assertFalse((self.tmp / "ng" / SKELETON_DIR / "1").exists())


class TestState(_ExporterCase):
    def setUp(self):
        super().setUp()
        self.exporter.add(str(self.long_edge), _run(self.long_edge))
        clean = self.tmp / "clean.swc"
        clean.write_text("1 1 5 6 7 1 -1\n2 2 10 6 7 1 1\n")
        self.exporter.add(str(clean), _run(clean))

    def test_batch_state(self):
        state = self.exporter.batch_state(SOURCE)
        self.assertEqual(state["dimensions"]["x"], [5e-07, "m"])
        neurons, annotations = state["layers"]
        self.assertEqual(neurons["source"], "precomputed://s3://bucket/prefix/skeletons")
        self.assertEqual(neurons["segments"], ["1", "2"])
        self.assertIn("n_flags", neurons["skeletonRendering"]["shader"])
        self.assertEqual(annotations["source"], "precomputed://s3://bucket/prefix/annotations/edge_length")
        self.assertEqual(annotations["linkedSegmentationLayer"], {"neuron": "neurons"})
        self.assertEqual(state["position"], [100.0, 0.0, 0.0])

    def test_image_layer_only_when_given(self):
        self.assertNotIn("image", [l["name"] for l in self.exporter.batch_state(SOURCE)["layers"]])
        state = self.exporter.batch_state(SOURCE, image_source="zarr://s3://bucket/image.zarr")
        self.assertEqual(state["layers"][0], {
            "type": "image", "name": "image", "source": "zarr://s3://bucket/image.zarr",
        })

    def test_neuron_state(self):
        flagged = self.exporter.neuron_state(1, SOURCE)
        self.assertEqual(flagged["layers"][0]["segments"], ["1"])
        self.assertEqual([l["name"] for l in flagged["layers"]], ["neurons", "edge_length"])
        self.assertEqual(flagged["position"], [100.0, 0.0, 0.0])

        clean = self.exporter.neuron_state(2, SOURCE)
        self.assertEqual([l["name"] for l in clean["layers"]], ["neurons"])
        self.assertEqual(clean["position"], [5.0, 6.0, 7.0])

    def test_state_without_skeleton_has_unlinked_annotations(self):
        state = build_state(None, {"edge_length": "precomputed://x"}, SCALE)
        self.assertEqual([l["type"] for l in state["layers"]], ["annotation"])
        self.assertNotIn("linkedSegmentationLayer", state["layers"][0])

    def test_url_prefix(self):
        url = self.exporter.batch_url(SOURCE, base_url="https://example.org/ng/")
        self.assertTrue(url.startswith("https://example.org/ng/#!%7B"))

    @needs_neuroglancer
    def test_url_round_trips_through_neuroglancer(self):
        state = self.exporter.batch_state(SOURCE, image_source="zarr://s3://bucket/image.zarr")
        parsed = neuroglancer.parse_url(state_to_url(state))
        self.assertEqual([l.name for l in parsed.layers], ["image", "neurons", "edge_length"])
        self.assertEqual(list(parsed.layers["neurons"].segments), [1, 2])
        np.testing.assert_allclose(parsed.position, [100.0, 0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
