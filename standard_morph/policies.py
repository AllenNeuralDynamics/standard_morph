"""Versioned threshold policy definitions and loader.

Threshold changes are collaborative and must be traceable in version history:
bump to a new ``policy_vN`` key rather than editing an existing one in place.
"""
from standard_morph.models.qc_policy import Policy, PolicyRange  # noqa: F401 — re-exported for policy authors

#: Registry of built-in policy versions.
_POLICY_DEFS = {
    "policy_v1": {
        "local_tortuosity": {
            # Local path-length / chord ratio over a 3-node window. 1.0 == straight.
            # NOTE: 10.0 is a loose placeholder;  Tune
            # against a labelled set and bump to policy_v2 when changed.
            "tortuosity_threshold": 10.0,
        },
        "branch_max_degree": {
            # Flag branch points with strictly more than this many children.
            "max_children": 2,
        },
        "edge_length": {
            # Max edge length, keyed by coordinate space: ~30 um before
            # resampling (image), ~10 um after (ccf). Excludes soma-child edges.
            "max_length_um": {
                "image_space": PolicyRange(lo=0, hi=30.0), 
                "ccf_registered": PolicyRange(lo=0, hi=10.0),
            },
        },
        "soma_child_distance": {
            # Soma's immediate children may sit farther out (soma is a point +
            # radius), but not beyond this.
            "max_soma_child_to_soma_um": 50.0,
        },
        "axon_origination": {
            # Straight-line distance from the axon origin to the soma point.
            # Deliberately LARGER than `soma_child_distance.max_soma_child_to_soma_um`: an
            # axon may originate a hop or two out on a basal dendrite, not just
            # at an immediate soma child, so it is allowed farther from the soma.
            "max_axon_origin_to_soma_um": 75.0,
        },
        "apical_origination": {
            # Flag when the number of apical dendrite trunks exceeds this.
            "max_origins": 1,
        },
        "compartment_transitions": {},  # rule-based, no threshold
        "filename_format": {
            # Naming convention to validate the SWC filename against.
            # "AIND" is enforced by regex; "AIBS" is a TODO stub (always passes).
            "name_format": "AIND",
        },
        "soma_at_centroid": {
            # Max fraction of the soma radius the SWC soma may be offset from the
            # image centroid, per axis. 0.5 -> must sit within half a radius on
            # every axis (near the centroid, not merely inside the soma extent).
            "max_offset_fraction": 0.5,
        },
        "root_near_detected_soma": {
            # Clamp applied to each detected soma's radius before testing the
            # root against it. Ellipsoid fits are unreliable (fits of hundreds
            # of um occur), and lo must also cover the detector's positional
            # error: the AIND exaSPIM detector currently reports the proposal
            # position, which put correct roots of 13 traced 841260 neurons up
            # to 27 um away. Tighten once detections report the fitted center.
            "soma_radius_um": PolicyRange(lo=35.0, hi=50.0),
        },
        "single_connected_component": {},  # no thresholds
        "single_root_node": {},  # structural, no thresholds
        "soma_first_node": {},  # structural, no thresholds
        "valid_parent_references": {},  # structural, no thresholds
        "acyclic": {},  # structural, no thresholds
        "parent_before_child": {},  # ordering convention, no thresholds
        "node_identity_types": {
            # SWC compartment codes considered valid (soma/axon/basal/apical).
            "allowed_types": [1, 2, 3, 4],
        },
        "duplicate_node_coordinates": {},  # exact-coordinate check, no threshold
        "nodes_outside_ccf_mesh": {
            # Fail if more than this fraction of nodes fall outside the brain mesh.
            "max_fraction_outside": 0.05,
        },
        "soma_inside_ccf_mesh": {},  # binary inside/outside, no threshold
    },
}


def available_policies():
    """Return the list of known policy version names."""
    return sorted(_POLICY_DEFS)


def get_policy(version="policy_v1"):
    """Load a built-in threshold policy by version name."""
    if version not in _POLICY_DEFS:
        raise KeyError(
            f"Unknown policy version '{version}'. Available: {available_policies()}"
        )
    return Policy(version=version, thresholds=_POLICY_DEFS[version])
