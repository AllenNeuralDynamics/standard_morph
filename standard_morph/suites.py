"""Built-in suite definitions and suite composition helpers.

A suite is a named, ordered list of metric names. The two built-in suites map
to the two coordinate spaces: run ``default_pre_registration_tests`` on
``image_space`` reconstructions and ``default_post_registration_tests`` on
``ccf_registered`` reconstructions. Applicability is still enforced per-metric
by the engine, so a mismatched suite/space combination fails fast rather than
silently skipping.

The two suites share a single base list of **space-agnostic** morphology
checks -- everything that is meaningful regardless of coordinate space -- and the
post-registration suite simply appends the **CCF-only** checks (which require the
data to be registered to the atlas). Defining them this way keeps the two suites
from drifting apart: the *only* difference is the CCF metrics.

Note: the always-run input-integrity checks (``required_columns``, ...,
``acyclic``) are not listed here -- the engine runs them every time regardless of
suite. Resource-driven, opt-in metrics (``filename_format``,
``parent_before_child``, ``soma_at_centroid``, ``root_near_detected_soma``) are
likewise omitted; request them explicitly via ``metrics=[...]`` when their inputs
are available.
"""

#: Morphology checks that are meaningful in *any* coordinate space, so they run
#: in both the pre- and post-registration suites.
_SPACE_AGNOSTIC = [
    "single_root_node",
    "soma_first_node",
    "single_connected_component",
    "node_identity_types",
    "duplicate_node_coordinates",
    "branch_max_degree",
    "edge_length",
    "soma_child_distance",
    "axon_origination",
    "apical_origination",
    "compartment_transitions",
    "local_tortuosity",
]

#: Checks that only make sense once the morphology is registered to the CCF
#: atlas; they are appended to the post-registration suite only.
_CCF_ONLY = [
    "soma_inside_ccf_mesh",
    "nodes_outside_ccf_mesh",
]

#: Ordered built-in suites, keyed by name. The post suite is the pre suite plus
#: the CCF-only checks, so the two never diverge except by those metrics.
BUILTIN_SUITES = {
    "default_pre_registration_tests": list(_SPACE_AGNOSTIC),
    "default_post_registration_tests": _SPACE_AGNOSTIC + _CCF_ONLY,
}


def available_suites():
    """Return the names of the built-in suites, sorted."""
    return sorted(BUILTIN_SUITES)


def resolve_suite(name):
    """Return the ordered metric-name list for a built-in suite."""
    if name not in BUILTIN_SUITES:
        raise KeyError(
            f"Unknown suite '{name}'. Available suites: {available_suites()}"
        )
    return list(BUILTIN_SUITES[name])
