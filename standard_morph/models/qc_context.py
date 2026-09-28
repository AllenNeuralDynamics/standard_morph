"""QC context object and shared context enums.

The context carries the runtime metadata a metric needs to decide whether it
is applicable and how to evaluate. The user provides `space` explicitly; the
pipeline stage (pre-/post-registration) is derived from it, never entered
directly.
"""
from dataclasses import dataclass, field
from enum import Enum


class Space(str, Enum):
    """Coordinate space the morphology lives in."""

    IMAGE_SPACE = "image_space"
    CCF_REGISTERED = "ccf_registered"


class MorphologyKind(str, Enum):
    """What the SWC represents."""

    AXON = "axon"
    DENDRITE = "dendrite"
    MERGED = "merged"


#: Convenience set for metrics that apply to any morphology kind.
ALL_MORPHOLOGY_KINDS = frozenset(MorphologyKind)
ALL_COORDINATE_SPACES = frozenset(Space)


@dataclass
class QCContext:
    """Runtime metadata required to evaluate metric applicability.

    Parameters
    ----------
    space : Space
        Coordinate space, ``image_space`` or ``ccf_registered`` (required).
    morphology_kind : MorphologyKind
        Whether this is an axon, dendrite, or merged reconstruction.
    resources : dict
        Optional external inputs keyed by name, e.g. ``{"image_path": ...}``
        or ``{"ccf_atlas_path": ...}``. Metrics declare which keys they need.
    coordinate_scale : tuple of float, length 3
        Per-axis multiplier ``(sx, sy, sz)`` applied to raw XYZ coordinates
        before any distance computation. Default ``(1.0, 1.0, 1.0)`` leaves
        coordinates unchanged (i.e. they are already in microns).

        To convert voxel coordinates to microns: set each value to the
        physical size of one voxel along that axis (e.g. ``(0.748, 0.748,
        1.0)`` if XY voxels are 0.748 µm and Z voxels are 1.0 µm).

        To correct for tissue expansion or shrinkage: set each value to the
        inverse of the expansion factor along that axis (e.g.
        ``(1/1.2, 1/1.2, 1/1.2)`` to correct coordinates from tissue that
        expanded 1.2×, or ``(1/0.9, 1/0.9, 1/0.9)`` to correct for 0.9×
        shrinkage).

        Each value must be a positive int or float.
    ccf_resolution : int
        Microns per voxel used to convert micron coordinates to atlas voxel
        indices. Defaults to 10 (the bundled Allen CCF atlas). Only needs to
        be set explicitly when supplying a custom atlas via
        ``resources["ccf_atlas_path"]`` or ``resources["ccf_annotation"]``
        with a different resolution. Only meaningful when
        ``space == ccf_registered``.
    policy_version : str
        Selected threshold policy version, e.g. ``"policy_v1"``.
    """

    space: Space
    morphology_kind: MorphologyKind = MorphologyKind.MERGED
    resources: dict = field(default_factory=dict)
    ccf_resolution: int = 10
    policy_version: str = "policy_v1"
    coordinate_scale: tuple = (1.0, 1.0, 1.0)

    def __post_init__(self):
        # Coerce plain strings into enums so callers can pass either.
        self.space = Space(self.space)
        self.morphology_kind = MorphologyKind(self.morphology_kind)
        if self.resources is None:
            self.resources = {}
        scale = self.coordinate_scale
        if len(scale) != 3:
            raise ValueError(
                f"coordinate_scale must have exactly 3 elements (x, y, z), got {len(scale)}"
            )
        if not all(isinstance(v, (int, float)) for v in scale):
            raise TypeError(
                f"coordinate_scale elements must be int or float, got "
                f"{[type(v).__name__ for v in scale]}"
            )
        if any(v <= 0 for v in scale):
            raise ValueError(
                f"coordinate_scale elements must be positive, got {list(scale)}"
            )
        self.coordinate_scale = tuple(float(v) for v in scale)
