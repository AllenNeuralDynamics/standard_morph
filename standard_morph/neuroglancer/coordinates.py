"""Coordinate handling shared by the Neuroglancer writers.

Every layer is written in the SWC's own units and never converted. What changes
is the *scale*, the physical size in micrometers of one SWC unit along x, y and
z. For SWCs already in micrometers the scale is ``(1, 1, 1)``. For SWCs in voxel
indices it is the voxel size (e.g. ``(0.748, 0.748, 1.0)`` for exaSPIM). Keeping
the raw coordinates means flagged-node positions in a ``RunReport`` can be used
as Neuroglancer positions directly.
"""
import numpy as np

AXES = ("x", "y", "z")


def validate_scale(scale_um):
    """Return ``scale_um`` as a float ``(3,)`` array, or raise ``ValueError``."""
    scale = np.asarray(scale_um, dtype=float)
    if scale.shape != (3,) or not np.all(np.isfinite(scale)) or np.any(scale <= 0):
        raise ValueError(
            f"scale_um must be three positive numbers (um per SWC unit along x, y, z); "
            f"got {scale_um!r}"
        )
    return scale


def dimensions_json(scale_um):
    """Neuroglancer ``dimensions`` JSON: ``{"x": [scale_m, "m"], ...}``."""
    scale = validate_scale(scale_um)
    return {axis: [float(s) * 1e-6, "m"] for axis, s in zip(AXES, scale)}


def coordinate_space(scale_um):
    """Return a ``neuroglancer.CoordinateSpace`` in SWC units with ``scale_um``.

    Requires the ``neuroglancer`` extra.
    """
    from standard_morph.neuroglancer._deps import import_neuroglancer

    ng = import_neuroglancer()
    return ng.CoordinateSpace(json=dimensions_json(scale_um))


def skeleton_transform_nm(scale_um):
    """Row-major 3x4 transform from SWC units to nanometers, for skeleton ``info``.

    Standalone precomputed skeletons are defined in nanometers, so the transform
    is diagonal with the scale in nm.
    """
    scale_nm = validate_scale(scale_um) * 1000.0
    transform = np.zeros((3, 4))
    transform[np.arange(3), np.arange(3)] = scale_nm
    return [float(v) for v in transform.ravel()]
