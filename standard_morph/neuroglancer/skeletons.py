"""Write morphologies as a standalone Neuroglancer precomputed skeleton source.

Layout under ``out_dir``::

    info                        neuroglancer_skeletons metadata
    <segment_id>                one encoded skeleton per neuron
    segment_properties/info     neuron labels, failing-metric tags, flag counts

Each vertex carries ``radius``, ``n_flags`` (how many metrics flagged that node)
and one ``flag_<metric>`` (0 or 1) per metric that flagged a node anywhere in
the batch, so a skeleton shader can color flagged nodes. The binary encoding
matches ``neuroglancer.skeleton.Skeleton.encode``; this module does not need the
``neuroglancer`` package.
"""
import json
import struct
from pathlib import Path

import numpy as np

from standard_morph.neuroglancer.coordinates import skeleton_transform_nm

SEGMENT_PROPERTIES_DIR = "segment_properties"


def flag_attribute(metric):
    """Name of the per-vertex attribute that marks nodes flagged by ``metric``."""
    return f"flag_{metric}"


def vertex_attributes(record, metrics):
    """Return ``{name: (N,) float32 array}`` for one record's skeleton."""
    skeleton = record.skeleton
    n = len(skeleton.node_id)
    index_of = {int(nid): i for i, nid in enumerate(skeleton.node_id)}
    attributes = {"radius": skeleton.radius.astype("<f4")}
    n_flags = np.zeros(n, dtype="<f4")
    per_metric = {}
    for metric in metrics:
        flag = np.zeros(n, dtype="<f4")
        for node in record.flags.get(metric, ()):
            i = index_of.get(node.node_id)
            if i is not None:
                flag[i] = 1.0
        n_flags += flag
        per_metric[flag_attribute(metric)] = flag
    attributes["n_flags"] = n_flags
    attributes.update(per_metric)
    return attributes


def encode_skeleton(xyz, edges, attributes):
    """Encode one skeleton in the precomputed format.

    ``attributes`` is an ordered ``{name: (N,) array}``; its order must match
    the ``vertex_attributes`` list in the source ``info``.
    """
    xyz = np.asarray(xyz, dtype="<f4")
    edges = np.asarray(edges, dtype="<u4").reshape(-1, 2)
    parts = [struct.pack("<II", xyz.shape[0], edges.shape[0]), xyz.tobytes(), edges.tobytes()]
    parts += [np.asarray(values, dtype="<f4").tobytes() for values in attributes.values()]
    return b"".join(parts)


def write_skeletons(records, out_dir, scale_um, metrics):
    """Write every record that has a skeleton to ``out_dir``.

    Parameters
    ----------
    records : list[NeuronRecord]
    out_dir : str | Path
    scale_um : sequence of 3 floats
        Micrometers per SWC unit along x, y, z.
    metrics : list[str]
        Metrics to give a ``flag_<metric>`` attribute (normally every metric
        that flagged a node in the batch).

    Returns the segment ids written.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    attribute_names = ["radius", "n_flags"] + [flag_attribute(m) for m in metrics]
    info = {
        "@type": "neuroglancer_skeletons",
        "transform": skeleton_transform_nm(scale_um),
        "vertex_attributes": [
            {"id": name, "data_type": "float32", "num_components": 1} for name in attribute_names
        ],
        "segment_properties": SEGMENT_PROPERTIES_DIR,
    }
    (out_dir / "info").write_text(json.dumps(info))

    written = []
    for record in records:
        if record.skeleton is None:
            continue
        attributes = vertex_attributes(record, metrics)
        data = encode_skeleton(record.skeleton.xyz, record.skeleton.edges, attributes)
        (out_dir / str(record.segment_id)).write_bytes(data)
        written.append(record.segment_id)

    _write_segment_properties(out_dir / SEGMENT_PROPERTIES_DIR, records, metrics)
    return written


def _write_segment_properties(path, records, metrics):
    path.mkdir(parents=True, exist_ok=True)
    tags = list(metrics)
    tag_index = {m: i for i, m in enumerate(tags)}
    properties = [
        {"id": "label", "type": "label", "values": [r.label for r in records]},
        {
            "id": "tags",
            "type": "tags",
            "tags": tags,
            "values": [sorted(tag_index[m] for m in r.flags if m in tag_index) for r in records],
        },
    ]
    for metric in metrics:
        properties.append({
            "id": f"n_flagged_{metric}",
            "type": "number",
            "data_type": "uint32",
            "values": [len(r.flags.get(metric, ())) for r in records],
        })
    info = {
        "@type": "neuroglancer_segment_properties",
        "inline": {"ids": [str(r.segment_id) for r in records], "properties": properties},
    }
    (path / "info").write_text(json.dumps(info))
