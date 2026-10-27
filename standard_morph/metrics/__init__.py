"""QC metric implementations.

Importing this package imports each metric module, which self-registers the
metric with ``standard_morph.registry.REGISTRY``.
"""
from standard_morph.metrics.base import Metric, Applicability, EvaluationPhase, BlockScope
from standard_morph.metrics.integrity import (
    RequiredColumnsMetric,
    NonEmptyMetric,
    CastableColumnsMetric,
    UniqueNodeIdsMetric,
    ValidParentReferencesMetric,
    AcyclicMetric,
    ParentBeforeChildMetric,
)
from standard_morph.metrics.filename_format import FilenameFormatMetric
from standard_morph.metrics.single_root import SingleRootNodeMetric
from standard_morph.metrics.soma_first_node import SomaFirstNodeMetric
from standard_morph.metrics.node_identity import NodeIdentityTypesMetric
from standard_morph.metrics.duplicate_coordinates import DuplicateNodeCoordinatesMetric
from standard_morph.metrics.edge_length import EdgeLengthMetric
from standard_morph.metrics.soma_child_distance import SomaChildDistanceMetric
from standard_morph.metrics.axon_origination import AxonOriginationMetric
from standard_morph.metrics.apical_origination import ApicalOriginationMetric
from standard_morph.metrics.compartment_transitions import CompartmentTransitionsMetric
from standard_morph.metrics.soma_at_centroid import SomaAtCentroidMetric
from standard_morph.metrics.root_near_detected_soma import RootNearDetectedSomaMetric
from standard_morph.metrics.local_tortuosity import LocalTortuosityMetric
from standard_morph.metrics.connected_component import SingleConnectedComponentMetric
from standard_morph.metrics.branch_degree import BranchMaxDegreeMetric
from standard_morph.metrics.ccf_mesh import (
    NodesOutsideCcfMeshMetric,
    SomaInsideCcfMeshMetric,
)

__all__ = [
    "Metric",
    "Applicability",
    "EvaluationPhase",
    "BlockScope",
    "RequiredColumnsMetric",
    "NonEmptyMetric",
    "CastableColumnsMetric",
    "UniqueNodeIdsMetric",
    "ValidParentReferencesMetric",
    "AcyclicMetric",
    "ParentBeforeChildMetric",
    "FilenameFormatMetric",
    "SingleRootNodeMetric",
    "SomaFirstNodeMetric",
    "NodeIdentityTypesMetric",
    "DuplicateNodeCoordinatesMetric",
    "EdgeLengthMetric",
    "SomaChildDistanceMetric",
    "AxonOriginationMetric",
    "ApicalOriginationMetric",
    "CompartmentTransitionsMetric",
    "SomaAtCentroidMetric",
    "RootNearDetectedSomaMetric",
    "LocalTortuosityMetric",
    "SingleConnectedComponentMetric",
    "BranchMaxDegreeMetric",
    "NodesOutsideCcfMeshMetric",
    "SomaInsideCcfMeshMetric",
]
