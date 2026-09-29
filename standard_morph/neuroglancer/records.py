"""Collect what the Neuroglancer writers need from one QC run.

A `NeuronRecord` is a small, self-contained snapshot of one morphology and the
nodes its QC run flagged. The writers work from records rather than from
``PreparedMorphology`` / ``RunReport`` objects, so a batch exporter only holds
the arrays it will write.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np

#: Result statuses whose flagged nodes are shown. A passing result has no
#: flagged nodes; a skipped one never ran.
FLAG_STATUSES = ("fail", "review", "error")


@dataclass
class FlaggedNode:
    node_id: int
    xyz: Tuple[float, float, float]
    status: str


@dataclass
class SkeletonData:
    node_id: np.ndarray  # (N,) original SWC ids
    xyz: np.ndarray      # (N, 3) SWC coordinates
    radius: np.ndarray   # (N,)
    edges: np.ndarray    # (E, 2) vertex index pairs (child, parent)
    soma_index: Optional[int] = None

    @classmethod
    def from_morphology(cls, morph):
        child = np.flatnonzero(morph.parent >= 0)
        edges = np.stack([child, morph.parent[child]], axis=1) if child.size else np.zeros((0, 2))
        soma = morph.soma_roots
        return cls(
            node_id=np.asarray(morph.node_id, dtype=np.int64),
            xyz=np.asarray(morph.xyz, dtype=float),
            radius=np.asarray(morph.radius, dtype=float),
            edges=np.asarray(edges, dtype=np.int64),
            soma_index=int(soma[0]) if soma.size else None,
        )


@dataclass
class NeuronRecord:
    segment_id: int
    label: str
    #: None when the morphology could not be built (a BUILD integrity failure).
    skeleton: Optional[SkeletonData]
    #: metric name -> flagged nodes, in report order (integrity results first).
    flags: Dict[str, List[FlaggedNode]] = field(default_factory=dict)

    @property
    def position(self):
        """Where to center a view of this neuron: its first flagged node, else
        its soma, else its first node. None if nothing has coordinates."""
        for nodes in self.flags.values():
            if nodes:
                return nodes[0].xyz
        if self.skeleton is not None and len(self.skeleton.node_id):
            index = self.skeleton.soma_index if self.skeleton.soma_index is not None else 0
            return tuple(float(v) for v in self.skeleton.xyz[index])
        return None


def flagged_nodes(report, statuses=FLAG_STATUSES, skeleton=None):
    """Return ``{metric: [FlaggedNode]}`` for every result in ``report`` that
    flagged nodes with a status in ``statuses``.

    Some results (notably the integrity checks) report node ids without
    coordinates. Given the neuron's ``skeleton``, those nodes are placed by id.
    Nodes that still have no finite coordinates cannot be placed, so they are
    left out.
    """
    xyz_by_id = {}
    if skeleton is not None:
        xyz_by_id = {int(nid): skeleton.xyz[i] for i, nid in enumerate(skeleton.node_id)}
    flags = {}
    for result in list(report.integrity_results) + list(report.results):
        if result.status not in statuses or not result.flagged_node_ids:
            continue
        coordinates = list(result.flagged_node_coordinates)
        nodes = []
        for i, node_id in enumerate(result.flagged_node_ids):
            xyz = coordinates[i] if i < len(coordinates) else xyz_by_id.get(int(node_id), ())
            xyz = tuple(float(v) for v in xyz)
            if len(xyz) == 3 and np.all(np.isfinite(xyz)):
                nodes.append(FlaggedNode(int(node_id), xyz, result.status))
        if nodes:
            flags[result.name] = nodes
    return flags
