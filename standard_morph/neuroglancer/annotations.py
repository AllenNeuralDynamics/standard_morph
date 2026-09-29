"""Write flagged nodes as Neuroglancer precomputed point annotations.

One annotation source is written per metric, at ``out_dir/<metric>``. Each point
carries the SWC ``node_id`` and the result ``status`` as properties, and a
``neuron`` relationship to the skeleton's segment id. That lets an annotation
layer linked to the skeleton layer show only the selected neurons' errors.

Requires the ``neuroglancer`` extra (``neuroglancer.write_annotations``).
"""
from pathlib import Path

from standard_morph.neuroglancer._deps import import_neuroglancer
from standard_morph.neuroglancer.coordinates import coordinate_space

#: Name of the annotation relationship that points at the skeleton segment.
RELATIONSHIP = "neuron"
#: Encoding of the ``status`` enum property.
STATUS_LABELS = ("fail", "review", "error")


def write_error_annotations(records, out_dir, scale_um, metrics):
    """Write one point-annotation source per metric in ``metrics``.

    Metrics with no flagged nodes in ``records`` are skipped. Returns the
    metrics written.
    """
    ng = import_neuroglancer()
    from neuroglancer.write_annotations import AnnotationWriter

    space = coordinate_space(scale_um)
    properties = [
        ng.AnnotationPropertySpec(id="node_id", type="uint32"),
        ng.AnnotationPropertySpec(
            id="status", type="uint8",
            enum_values=list(range(len(STATUS_LABELS))), enum_labels=list(STATUS_LABELS),
        ),
    ]
    status_code = {label: i for i, label in enumerate(STATUS_LABELS)}

    written = []
    for metric in metrics:
        writer = AnnotationWriter(
            coordinate_space=space, annotation_type="point",
            relationships=[RELATIONSHIP], properties=properties,
        )
        for record in records:
            for node in record.flags.get(metric, ()):
                writer.add_point(
                    node.xyz, node_id=node.node_id, status=status_code[node.status],
                    **{RELATIONSHIP: record.segment_id},
                )
        if writer.annotations:
            writer.write(Path(out_dir) / metric)
            written.append(metric)
    return written
