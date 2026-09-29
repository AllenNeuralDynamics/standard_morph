# Standard Morph

Standard Morph is a Python library for **quality control of neuron SWC reconstructions**. It runs a configurable set of QC *metrics* against a morphology and returns a structured, versioned report designed to plug into automated pipelines and QC databases/portal.

The library is built around four ideas:

| Concept | What it is |
|---------|-----------|
| **Context** (`QCContext`) | Runtime metadata about the morphology: coordinate `space`, `morphology_kind`, external `resources`, and the threshold `policy_version`. |
| **Metric** | A single QC check with declared *applicability* rules and computation logic. Metrics know which contexts they make sense in. |
| **Suite** | A named, ordered list of metrics (e.g. `default_pre_registration_tests`). |
| **Policy** | A versioned dictionary of thresholds. Metrics read thresholds from the policy rather than from call-time arguments, and the policy version is recorded in every report. |

A run resolves a suite (or explicit metric list), validates that every metric is compatible with the context, prepares shared morphology features once, executes the metrics in deterministic order, and aggregates a `RunReport`.


## Installation

```
git clone https://github.com/AllenInstitute/standard_morph.git
cd standard_morph
pip install ".[full]"
```

The `full` extra includes all optional dependencies (`pynrrd`, `imageio`, `s3fs`, `zarr`, `scikit-image`, `neuroglancer`) and is the recommended starting point. If you need a minimal install, the core library requires only `numpy` and `pandas`:

```
pip install .
```

Available extras for selective installs:

```
pip install ".[ccf]"        # pynrrd -- CCF atlas / brain-mesh checks
pip install ".[soma-mip]"   # imageio, s3fs, zarr, scikit-image -- image-based checks
pip install ".[neuroglancer]"  # neuroglancer -- write QC errors as Neuroglancer layers (Python >= 3.10)
pip install ".[test]"       # pytest
```


## Quick start

```python
from standard_morph import run_qc, QCContext, Space

# The user provides the coordinate space; the pipeline stage (pre-/post-
# registration) is derived from it. image_space -> pre-registration checks.
context = QCContext(space=Space.IMAGE_SPACE)

report = run_qc(
    "cell.swc",
    context,
    suite_name="default_pre_registration_tests",
)

print(report.summary)
# {'n_metrics': 12, 'n_pass': 9, 'n_fail': 2, 'n_review': 1, 'n_error': 0, 'n_skipped': 0, 'overall_status': 'fail'}

for r in report.results:
    print(f"{r.name:28s} {r.status:5s} {r.message}")
# single_root_node             pass  Single valid root: one soma-type root, node_id 1, first line.
# soma_first_node              pass  First node (id 1) is the soma.
# single_connected_component   pass  Reconstruction is a single connected component.
# node_identity_types          pass  Node types [1, 2, 3] are all expected.
# duplicate_node_coordinates   pass  No duplicate node coordinates.
# branch_max_degree            review 1 branch point(s) have more than 2 children.
# edge_length                  fail  2985/3232 edge(s) outside [0, 30.0] um (min 0.2, max 1360.7 um).
# soma_child_distance          fail  3/4 soma child(ren) farther than 50.0 um (max 70.9 um).
# axon_origination             pass  Single axon origination from the soma/basal, near the soma.
# apical_origination           pass  No apical dendrite nodes present.
# compartment_transitions      pass  All dendrite compartment transitions are valid.
# local_tortuosity             pass  All 3000 evaluated node(s) within tortuosity threshold 10.0.
```


## Inputs

`run_qc(input_data, ...)` accepts any of:

```python
run_qc("cell.swc", context, suite_name=...)          # path to an SWC file
run_qc(dataframe, context, suite_name=...)            # a canonical SWC DataFrame
run_qc(prepared_morphology, context, suite_name=...)  # a PreparedMorphology
```

SWC files are read with `read_swc`, which handles Janelia/Horta `# OFFSET` headers. A DataFrame must have the canonical SWC columns `node_id, compartment, x, y, z, r, parent` — all required (the `required_columns` integrity check enforces this at run time).

```python
from standard_morph import read_swc
df = read_swc("cell.swc")
```


## Suites vs. explicit metrics

Provide **exactly one** of `suite_name` or `metrics`:

```python
# Run a built-in suite
run_qc("cell.swc", context, suite_name="default_pre_registration_tests")

# Or hand-pick metrics in the order you want them run
run_qc("cell.swc", context, metrics=["single_connected_component", "branch_max_degree"])
```

Built-in suites map to the two coordinate spaces:

```python
from standard_morph import available_suites, resolve_suite

available_suites()
# ['default_post_registration_tests', 'default_pre_registration_tests']

resolve_suite("default_pre_registration_tests")
# ['single_root_node', 'soma_first_node', 'single_connected_component', 'node_identity_types',
#  'duplicate_node_coordinates', 'branch_max_degree', 'edge_length', 'soma_child_distance',
#  'axon_origination', 'apical_origination', 'compartment_transitions', 'local_tortuosity']
# (default_post_registration_tests is the same list plus soma_inside_ccf_mesh and
#  nodes_outside_ccf_mesh -- the only two checks that require CCF registration.)
```


## Coordinate space and applicability (fail-fast)

Each metric declares which spaces, morphology kinds, and external resources it supports. If **any** requested metric is incompatible with the context, the run halts immediately with `IncompatibleMetricContextError` — it never silently skips a check.

For example, `nodes_outside_ccf_mesh` only makes sense on a CCF-registered morphology (it looks each node up in the CCF atlas). Requesting it on an `image_space` morphology halts before any work is done:

```python
from standard_morph import run_qc, QCContext, Space, IncompatibleMetricContextError

image_context = QCContext(space=Space.IMAGE_SPACE)

try:
    run_qc("cell.swc", image_context, metrics=["nodes_outside_ccf_mesh"])
except IncompatibleMetricContextError as e:
    print(e)
    # Metric 'nodes_outside_ccf_mesh' is incompatible with context:
    # space 'image_space' not in allowed ['ccf_registered']
```

`QCContext` fields:

```python
QCContext(
    space=Space.IMAGE_SPACE,              # or Space.CCF_REGISTERED  (required)
    morphology_kind=MorphologyKind.MERGED,# AXON | DENDRITE | MERGED (default MERGED)
    resources={},                         # per-file inputs: {"filename": ...}, {"ccf_atlas_path": ...}, {"image_soma_xyz": ...}
    ccf_resolution=10,                    # microns/voxel; defaults to 10 (bundled atlas), set only for a custom atlas
    policy_version="policy_v1",           # threshold policy to apply
)
```

`resources` is how image/atlas paths reach the metrics that need them. A metric that requires a resource will refuse to run (fail-fast) if it is missing.


## Resources: which metric needs what

Most metrics need nothing beyond the SWC. A handful read extra per-file inputs from `context.resources` (or, for the CCF metrics, when a custom atlas is used, the `ccf_resolution` context field). Anything **required** is enforced fail-fast — the run halts with `IncompatibleMetricContextError` if it is absent, rather than silently skipping the check.

**Required** — the metric will not run without it:

| Resource | Required by | What it is |
|----------|-------------|-----------|
| `filename` | `filename_format` | The SWC filename. Auto-derived when you pass a path; supply it explicitly for a DataFrame / `PreparedMorphology`. |
| `image_soma_xyz` | `soma_at_centroid` | Image-detected soma centroid `(x, y, z)`. |
| `image_soma_radius_xyz` | `soma_at_centroid` | Per-axis soma radius `(rx, ry, rz)`. |

**Optional** — enables extra behaviour or overrides a default:

| Resource | Used by | Effect |
|----------|---------|--------|
| `image_zarr_path` | `soma_at_centroid` | Local or `s3://` OME-Zarr → renders the soma MIP QC image. |
| `soma_mip_path` | `soma_at_centroid` | Output path for the rendered MIP (required *if* `image_zarr_path` is set). |
| `soma_mip_crop_size`, `soma_mip_depth` | `soma_at_centroid` | MIP crop size / depth (defaults `128` / `10`). |
| `ccf_atlas_path` | `soma_inside_ccf_mesh`, `nodes_outside_ccf_mesh` | A custom atlas `.nrrd` instead of the bundled 10 µm volume. Set `ccf_resolution` to match. |
| `ccf_annotation` | `soma_inside_ccf_mesh`, `nodes_outside_ccf_mesh` | A preloaded annotation array to reuse across many cells (skips re-reading). Set `ccf_resolution` to match if not 10 µm. |
| `ccf_resolution` *(context field, not `resources`)* | `soma_inside_ccf_mesh`, `nodes_outside_ccf_mesh` | Microns/voxel of the atlas (default `10`). Only set this when using a custom atlas at a different resolution. |

Every other metric requires no resources.


## Two phases: input integrity, then morphology quality

A QC run has two phases, because there are two different kinds of "wrong":

1. **Input integrity** — is the raw SWC *table* even well-formed? (required columns incl. radius, non-empty, values castable to the right numeric types, unique node ids). These checks read the DataFrame *before* any graph is built.
2. **Morphology quality** — given a well-formed table, does the *neuron* have problems? (roots, connectivity, tortuosity, CCF position). These read the built `PreparedMorphology`.

The integrity phase runs first, and **the *scope* of a failure decides what gets skipped** — it is not all-or-nothing:

- A **build-scope** failure (missing column, empty table) means no arrays can be built, so *every* morphology metric is skipped.
- A **topology-scope** failure (duplicate node ids) means the table builds fine, but the tree structure (`parent`/`children`/`roots`) is untrustworthy. Only metrics that *need* topology are skipped; coordinate/attribute-only metrics still run, because their per-node data (`xyz`, `compartment`) is intact.

So on a file with duplicate ids, `single_root_node`, `single_connected_component`, and `local_tortuosity` are skipped — but `nodes_outside_ccf_mesh`, `duplicate_node_coordinates`, and `node_identity_types` still run and report:

```python
report = run_qc(dup_id_swc, ccf_context, metrics=["nodes_outside_ccf_mesh", "single_root_node"])

report.integrity_ok   # False (unique_node_ids failed)
{r.name: r.status for r in report.results}
# {'nodes_outside_ccf_mesh': 'fail',       <- ran: fraction-outside is a pure coordinate lookup
#  'single_root_node':       'skipped'}    <- needs topology
```

Either way the report has the same shape — a malformed file names its integrity failure and marks the un-runnable metrics `"skipped"`, never a stack trace. This is what keeps batch QC over thousands of files robust. Metrics declare their role via three attributes:

- `evaluation_phase` — `INPUT_INTEGRITY` or `MORPHOLOGY_QUALITY` (default).
- `blocks_on_failure` (integrity metrics) — `BUILD` or `TOPOLOGY`: what this metric's failure invalidates.
- `requires_topology` (morphology metrics) — default `True`; coordinate/attribute-only metrics set `False` so they survive a topology failure.

The buildability checks (`required_columns`, `non_empty`, `castable_columns`, `unique_node_ids`, `valid_parent_references`, `acyclic`) **always run**, regardless of the suite — they're the preconditions for a trustworthy tree. The first three are BUILD-scope (a failure means no usable arrays at all); the last three are TOPOLOGY-scope, which is what makes "the topology is trustworthy" an actual guarantee rather than an assumption — a duplicate id, a dangling parent reference, or a cycle each skips only the topology-dependent metrics while coordinate/attribute-only ones still run. `castable_columns` flags nulls, non-numeric, and non-finite (`inf`) values so a bad value yields a clean report instead of crashing the build or silently poisoning the geometry.

Two further input-integrity metrics are *opt-in* (requested explicitly; not in the default suites) and *non-blocking* — they fail the report but skip nothing downstream: `filename_format` (validates the SWC name; needs `resources["filename"]`) and `parent_before_child` (reports whether each parent precedes its children in the file).


## The run report

`run_qc` returns a `RunReport` with two result lists: `integrity_results` (input phase) and `results` (morphology phase). Every metric result carries its raw measurements, the thresholds used, and the **full** list of flagged nodes (ids *and* coordinates — no sampling or capping). `policy_thresholds` records the active policy's values for every metric in the run (the buildability checks plus the requested metrics), so a report carries its exact thresholds rather than only the `policy_version` name. `report.to_dict()` yields a JSON-serialisable structure suitable for writing to a file or database.

```python
import json

report = run_qc("cell.swc", context, suite_name="default_pre_registration_tests")

report.passed                 # False (bool convenience)
report.summary["overall_status"]  # 'fail'

with open("qc_report.json", "w") as f:
    json.dump(report.to_dict(), f, indent=2)
```

Example `report.to_dict()` (two of eleven results shown; flagged lists trimmed):

```json
{
  "schema_version": "1.2",
  "standard_morph_version": "0.1.0",
  "generated_at": "2026-08-12T20:58:02.477061+00:00",
  "space": "image_space",
  "morphology_kind": "merged",
  "ccf_resolution": null,
  "policy_version": "policy_v1",
  "suite_name": "default_pre_registration_tests",
  "requested_metrics": [
    "single_root_node", "single_connected_component", "node_identity_types",
    "duplicate_node_coordinates", "branch_max_degree", "edge_length", "soma_child_distance",
    "axon_origination", "apical_origination", "compartment_transitions", "local_tortuosity"
  ],
  "input_ref": "cell.swc",
  "policy_thresholds": {
    "required_columns": {}, "non_empty": {}, "castable_columns": {},
    "unique_node_ids": {}, "valid_parent_references": {}, "acyclic": {},
    "single_root_node": {},
    "edge_length": {"max_length_um": {"image_space": {"lo": 0, "hi": 30.0}, "ccf_registered": {"lo": 0, "hi": 10.0}}},
    "local_tortuosity": {"tortuosity_threshold": 10.0},
    ...
  },
  "integrity_results": [
    {"name": "required_columns",        "status": "pass", "value": 0,    "value_label": "n_missing_columns", ...},
    {"name": "non_empty",               "status": "pass", "value": 3237, "value_label": "n_rows", ...},
    {"name": "castable_columns",        "status": "pass", "value": 0,    "value_label": "n_uncastable_values", ...},
    {"name": "unique_node_ids",         "status": "pass", "value": 0,    "value_label": "n_duplicate_ids", ...},
    {"name": "valid_parent_references", "status": "pass", "value": 0,    "value_label": "n_dangling_parents", ...},
    {"name": "acyclic",                 "status": "pass", "value": 0,    "value_label": "n_cyclic_nodes", ...}
  ],
  "morphology_evaluated": true,
  "results": [
    {
      "name": "branch_max_degree",
      "status": "review",
      "message": "1 branch point(s) have more than 2 children.",
      "value": 3,
      "value_label": "max_children_observed",
      "thresholds_used": {"max_children": 2},
      "measurements": {"max_children_observed": 3},
      "flagged_node_ids": [68],
      "flagged_node_coordinates": [[30060.0, 9840.0, 12166.0]],
      "counts": {"n_flagged": 1},
      "artifacts": [],
      "runtime_ms": 0.021
    },
    {
      "name": "local_tortuosity",
      "status": "pass",
      "message": "All 3000 evaluated node(s) within tortuosity threshold 10.0.",
      "value": 4.503,
      "value_label": "max_tortuosity",
      "thresholds_used": {"tortuosity_threshold": 10.0},
      "measurements": {
        "n_evaluated": 3000,
        "max_tortuosity": 4.503,
        "mean_tortuosity": 1.111,
        "flagged_tortuosities": []
      },
      "flagged_node_ids": [],
      "flagged_node_coordinates": [],
      "counts": {"n_flagged": 0, "n_evaluated": 3000},
      "artifacts": []
    }
  ],
  "summary": {"n_metrics": 11, "n_pass": 8, "n_fail": 2, "n_review": 1, "n_error": 0, "n_skipped": 0, "overall_status": "fail"},
  "integrity_ok": true,
  "passed": false
}
```

`MetricResult` fields: `name`, `status` (`pass`/`fail`/`review`/`error`/`skipped`), `message`, `value`, `value_label`, `thresholds_used`, `measurements`, `flagged_node_ids`, `flagged_node_coordinates`, `counts`, `artifacts`, `runtime_ms`. `artifacts` lists any generated files (e.g. a soma MIP) as `{type, path, description}`.

`summary` covers the morphology-quality `results`; `report.passed` is the one-glance verdict (input was valid **and** all morphology checks passed).

### Fail vs. review — objective defects vs. human oversight

Not every violation is an objective error. Some checks flag something *unusual but possibly valid* — multiple apical trunks, a branch point with more than two children — that a human should judge rather than the machine reject. A metric declares this with **`violation_severity`** (`Severity.FAIL`, the default, or `Severity.REVIEW`):

- A `FAIL` metric reports status `"fail"` on violation and makes `overall_status` `"fail"`.
- A `REVIEW` metric reports status `"review"` on violation. This is **not** a failure: like a `"skipped"` check it rolls up to `overall_status` `"incomplete"` (needs human oversight), and `report.passed` is `False` — but it is never counted or graded as a fail.

So `overall_status` is one of `pass` / `incomplete` (some checks need review or did not run) / `fail` / `error`, and the summary carries a per-status breakdown (`n_pass`, `n_fail`, `n_review`, `n_error`, `n_skipped`). Moving a check between the two buckets is a one-line change to its `violation_severity`; currently `apical_origination` and `branch_max_degree` are `REVIEW` and everything else is `FAIL`.

Every metric that can be summarised by a single number exposes it as `value` (with `value_label` naming it) — a max, count, or fraction suitable for trending across many cells. Genuinely binary checks (e.g. filename convention) leave `value` as `None` and rely on `status`.


## Threshold policies (versioned)

Thresholds live in versioned policies, not in your call site. This keeps QC runs reproducible and makes threshold changes traceable — bump to a new `policy_vN` rather than editing values in place.

Policies hold more than numeric thresholds: any per-metric *configuration* lives there, including conventions like the filename `name_format` (`AIND`/`AIBS`) and `node_identity_types.allowed_types`. That's how `run_qc` knows which filename convention to enforce — it reads `name_format` from the active policy (it is **not** a resource). To check a different convention, run under a policy version whose `filename_format.name_format` differs.

```python
from standard_morph import get_policy, available_policies

available_policies()                 # ['policy_v1']
policy = get_policy("policy_v1")

# Select a policy per run via the context...
run_qc("cell.swc", QCContext(space=Space.IMAGE_SPACE, policy_version="policy_v1"), suite_name=...)

# ...or override it for a single call:
run_qc("cell.swc", context, suite_name=..., policy_version="policy_v1")
```

The active policy version is always recorded in the report, along with the policy values for every metric in the run (`policy_thresholds`).

### Custom policies

Pipelines that need their own thresholds can pass a `Policy` object directly instead of a built-in version name. Start from a built-in policy so every metric keeps a value, override what you need, and give it a distinct `version` -- that name is what the report records:

```python
from standard_morph import run_qc, get_policy, Policy, PolicyRange, QCContext, Space

base = get_policy("policy_v1")
thresholds = {name: dict(vals) for name, vals in base.thresholds.items()}
thresholds["edge_length"]["max_length_um"] = {"image_space": PolicyRange(lo=0, hi=40.0)}
thresholds["local_tortuosity"]["tortuosity_threshold"] = 5.0

policy = Policy(version="my_pipeline_v1", thresholds=thresholds)
report = run_qc("cell.swc", QCContext(space=Space.IMAGE_SPACE),
                suite_name="default_pre_registration_tests", policy=policy)
report.policy_version   # 'my_pipeline_v1'
```

`policy` and `policy_version` are mutually exclusive. Because the thresholds are copied into `report.policy_thresholds`, a report from a custom policy can still be traced to its exact values. A custom policy goes through the same pre-flight as a built-in one, so a missing required key raises `MissingPolicyValuesError` before any metric runs.

### Space-keyed thresholds

Some thresholds differ between coordinate spaces — edge lengths are typically ~30 µm before resampling (image space) and ~10 µm after (CCF). For these, the policy value is a **per-space dict** keyed by the `Space.value` string:

```python
"edge_length": {
    "max_length_um": {
        "image_space": PolicyRange(lo=0.0, hi=30.0),
        "ccf_registered": PolicyRange(lo=0.0, hi=10.0),
    }
}
```

A plain scalar is also accepted and applies to all spaces (flags any edge exceeding the value):

```python
"edge_length": {"max_length_um": 30.0}   # same threshold everywhere
```

If the value is a dict but the current space has no entry, the run raises `MissingPolicyValueError` immediately — it never silently falls back to a wrong threshold. Metric authors use `threshold_for_space` (from `standard_morph.metrics.base`) to read space-keyed values:

```python
from standard_morph.metrics.base import Metric, Applicability, threshold_for_space

class MySpaceVaryingMetric(Metric):
    required_policy_keys = frozenset({"my_threshold"})

    def evaluate(self, prepared_morph, context, policy):
        threshold = threshold_for_space(policy, self.name, "my_threshold", context.space.value)
        ...
```


## Implemented metrics

22 metrics are wired up across both phases. Adding a metric requires no engine or suite changes (see [Writing a custom metric](#writing-a-custom-metric)).

**Input-integrity metrics** — read the raw SWC table. The six *buildability* checks always run, regardless of suite; `filename_format` and `parent_before_child` are opt-in:

| Name | # | Checks | Failure scope |
|------|---|--------|---------------|
| `required_columns` | — | The canonical SWC columns are present. | build |
| `non_empty` | — | The table has at least one node. | build |
| `castable_columns` | 8 | ids/types cast to int and coordinates to float; nulls / non-numeric / non-finite (`inf`) values are flagged rather than crashing the build. | build |
| `unique_node_ids` | — | Node ids are unique (so the id→index map is unambiguous). | topology |
| `valid_parent_references` | — | Every non-root `parent` id exists as a `node_id` (no dangling edges / orphans). | topology |
| `acyclic` | — | The parent chain has no cycle (incl. a self-parent), so tree walks can't loop. | topology |
| `filename_format` | 7 | Filename matches a convention (`AIND` regex; `AIBS` is a TODO stub that passes). Needs `resources["filename"]` — auto-derived from a path. Opt-in, non-blocking. | none |
| `parent_before_child` | — | Each parent row precedes its children (a topological order — *not* strict BFS/DFS). Opt-in, non-blocking report. | none |

**Morphology-quality metrics** — read the built `PreparedMorphology`:

| Name | # | Checks | Spaces |
|------|---|--------|--------|
| `single_root_node` | 1 | Exactly one root, and it is the soma (type 1, `parent == -1`); no other type-1 node; root is node_id 1 / first line. | image + ccf |
| `soma_inside_ccf_mesh` | 2 | Soma voxel falls inside the CCF brain mesh. | ccf |
| `nodes_outside_ccf_mesh` | 3 | Fraction of nodes falling outside the CCF brain mesh (`max_fraction_outside`). | ccf |
| `edge_length` | 4/5 | Edges longer than `max_length_um` (space-keyed); soma-child edges excluded. | image + ccf |
| `node_identity_types` | 6 | All compartment codes are expected (1–4); merged files carry soma + axon + dendrite. | image + ccf |
| `local_tortuosity` | 10 | Local path-length / chord ratio over a 3-node window; reducible + branch (averaged) nodes. | image + ccf |
| `duplicate_node_coordinates` | 11 | Flags nodes sharing an identical (x, y, z) with another node. | image + ccf |
| `axon_origination` | 13 | Exactly one axon origin, from the soma or a basal, and near the soma point (`max_axon_origin_to_soma_um`). | image + ccf |
| `apical_origination` | 14 | At most one apical dendrite trunk sprouts from the soma (`max_origins`). | image + ccf |
| `compartment_transitions` | 15 | Every dendrite's parent is the soma root or a same-type dendrite. | image + ccf |
| `single_connected_component` | 16 | Reconstruction is one connected tree (flags extra roots / orphan subtrees). | image + ccf |
| `branch_max_degree` | 17 | Branch points with more than `max_children` children (soma excluded). | image + ccf |
| `soma_child_distance` | — | Soma's immediate children farther than `max_soma_child_to_soma_um` from the soma. | image + ccf |
| `soma_at_centroid` | — | SWC soma within a fraction of the soma radius of the image centroid (`max_offset_fraction`); optional soma MIP artifact. Needs `resources["image_soma_xyz"]` + `["image_soma_radius_xyz"]`. | image |

`filename_format` and `soma_at_centroid` need per-file resources, so they are **opt-in** — not in the default suites; request them via `metrics=[...]` or your own suite (see [Opt-in checks that need per-file inputs](#opt-in-checks-that-need-per-file-inputs)).


## Opt-in checks that need per-file inputs

Two checks are driven by per-file metadata supplied through `resources`, so they are opt-in rather than part of the default suites (a suite run over bare DataFrames would otherwise fail-fast on the missing input). Both follow the same contract as the CCF metrics: if the required resource is absent, the run halts with `IncompatibleMetricContextError` rather than silently skipping.

**Filename convention — `filename_format`.** Validates the SWC filename against a naming convention (`AIND` by default, a regex; `AIBS` is a TODO stub that always passes). Select the convention with the `name_format` policy key.

```python
# When you pass a path, the filename is taken from it automatically:
run_qc("N024-648434-CONSENSUS.swc", QCContext(space=Space.IMAGE_SPACE),
       metrics=["filename_format"])

# For a DataFrame / PreparedMorphology, supply it explicitly:
run_qc(df, QCContext(space=Space.IMAGE_SPACE, resources={"filename": "N024-648434-CONSENSUS.swc"}),
       metrics=["filename_format"])
```

A bad name fails the report (`integrity_ok` / `passed` become `False`) but is *non-blocking* — every downstream morphology check still runs.

**Soma position — `soma_at_centroid`.** Compares the reconstruction's soma to an image-detected soma centroid, measuring the offset per axis as a fraction of the soma radius; the worst axis must stay within `max_offset_fraction` (default `0.5` — within half a radius on every axis). If an OME-Zarr image is provided, it also renders a soma MIP QC image (centroid green, SWC soma red) into `result.artifacts`.

```python
context = QCContext(
    space=Space.IMAGE_SPACE,
    resources={
        "image_soma_xyz": (5417.0, 25287.0, 1200.0),     # detected soma centroid
        "image_soma_radius_xyz": (100.0, 100.0, 150.0),  # per-axis soma radius
        # optional QC image (needs the soma-mip extra):
        "image_zarr_path": "s3://bucket/image.zarr",     # or a local OME-Zarr path
        "soma_mip_path": "cell_soma_mip.png",
    },
)

report = run_qc("cell.swc", context, metrics=["soma_at_centroid"])
r = report.results[0]
print(r.value, r.value_label)   # e.g. 0.42 max_soma_offset_fraction
print(r.artifacts)              # [{'type': 'soma_mip', 'path': 'cell_soma_mip.png', ...}]
```

Rendering the MIP needs `pip install ".[soma-mip]"`; any rendering failure is recorded in `measurements["soma_mip_error"]` and never fails the check.


## CCF post-registration checks (atlas)

The `ccf_registered` metrics (`soma_inside_ccf_mesh`, `nodes_outside_ccf_mesh`) look each node up in the Allen CCF annotation volume — a 3-D atlas where each voxel holds a structure id and `0` means "outside the brain".

**CCF-registered SWC files must have coordinates in micron space** (the standard output of CCF registration pipelines). A node's atlas voxel is computed as `floor(coord_microns / resolution)`. The library always uses the bundled 10 µm Allen CCF atlas by default, so no extra configuration is needed for the common case:

```python
from standard_morph import run_qc, QCContext, Space

context = QCContext(space=Space.CCF_REGISTERED)  # defaults to the bundled 10 µm atlas

report = run_qc("cell_registered.swc", context,
                suite_name="default_post_registration_tests")

for r in report.results:
    print(r.name, r.status, r.message)
# soma_inside_ccf_mesh    pass  Soma is inside the CCF brain mesh.
# nodes_outside_ccf_mesh  pass  654/75093 nodes (0.9%) outside the CCF brain mesh, within 5.0%.
```

Notes:

- **Custom atlas.** Only the 10 µm atlas is bundled. To use a different atlas, supply the file and set `ccf_resolution` to match its voxel size:
  ```python
  QCContext(space=Space.CCF_REGISTERED, ccf_resolution=25,
            resources={"ccf_atlas_path": "/path/to/annotation_25.nrrd"})
  ```
- **Preloaded atlas.** To reuse one loaded atlas across many cells (and skip re-reading), pass the array directly: `resources={"ccf_annotation": my_array}`. Set `ccf_resolution` if the array is not 10 µm.
- **Install the extra.** Reading a `.nrrd` atlas needs `pip install ".[ccf]"` (pulls in `pynrrd`).
- **Memory.** The 10 µm volume decompresses to ~4.8 GB in RAM; it is loaded once and cached.


## QC-ing many neurons (atlas caching)

Reading the `.nrrd` atlas is the slow part (~5–20 s). It is **loaded once per process and cached** (keyed by resolution + path), so a loop over thousands of cells pays that cost a single time:

```python
from standard_morph import run_qc, QCContext, Space

context = QCContext(space=Space.CCF_REGISTERED)  # ccf_resolution defaults to 10

for swc in many_swc_paths:           # atlas loads on the first cell, cached thereafter
    report = run_qc(swc, context, suite_name="default_post_registration_tests")
    save(report.to_dict())
```

To make it explicit — warm the cache up front, or share one array across cells:

```python
from standard_morph import load_ccf_annotation, clear_atlas_cache

annotation = load_ccf_annotation(resolution=10)          # ~5-20 s, once
context = QCContext(space=Space.CCF_REGISTERED, ccf_resolution=10,
                    resources={"ccf_annotation": annotation})  # reuse this array

for swc in many_swc_paths:
    run_qc(swc, context, ...)

clear_atlas_cache()   # optional: free the ~4.8 GB when done
```

**Across multiple processes** (e.g. a multiprocessing pool), the in-process cache doesn't cross the process boundary, so each worker loads once. Load in a pool *initializer* so it happens a fixed number of times (once per worker), not once per cell:

```python
from multiprocessing import Pool
from standard_morph import load_ccf_annotation

def _init():
    load_ccf_annotation(resolution=10)   # warms this worker's cache

with Pool(initializer=_init) as pool:
    pool.map(qc_one_cell, many_swc_paths)
```


## Inspecting morphology structure

`PreparedMorphology` is the array-backed structure every metric runs on. It is useful on its own for quick topological inspection:

```python
from standard_morph import read_swc, PreparedMorphology

pm = PreparedMorphology.from_dataframe(read_swc("cell.swc"))

pm.n                 # node count
pm.roots             # indices of root nodes (parent == -1)
pm.tips              # leaf indices
pm.branch_points     # branch indices (>= 2 children)
pm.orphans           # nodes whose parent id is missing from the file
pm.n_components      # connected components (1 for a well-formed tree)
pm.segments          # list of unbranched paths (node indices) between branch/terminal points

# Indices map back to original SWC ids via pm.node_id
original_ids = [int(pm.node_id[i]) for i in pm.branch_points]
```


## Writing a custom metric

A metric is a small, self-contained class: subclass `Metric`, declare `applicability`, implement `evaluate`, and register it. No engine or suite plumbing changes are needed — registered metrics are immediately runnable by name. By default a metric is a `MORPHOLOGY_QUALITY` check that needs valid topology, so an ordinary metric sets no phase attributes at all.

```python
import time
from standard_morph.metrics.base import Metric, Applicability
from standard_morph.models.qc_context import Space, ALL_MORPHOLOGY_KINDS
from standard_morph.models.qc_result import MetricResult
from standard_morph.registry import register


class SomaExistsMetric(Metric):
    name = "soma_exists"
    display_name = "Exactly one soma"
    applicability = Applicability(
        spaces=frozenset({Space.IMAGE_SPACE, Space.CCF_REGISTERED}),
        morphology_kinds=ALL_MORPHOLOGY_KINDS,
        required_resources=frozenset(),
    )
    # Defaults, shown for clarity (usually omitted):
    #   evaluation_phase = EvaluationPhase.MORPHOLOGY_QUALITY   # gets a PreparedMorphology, not a raw unprepared SWC. Change this if you are implementing an integrity metric

    #   requires_topology = True   # set False for coordinate/attribute-only metrics that do not require a topologically correct graph. For example duplicate x,y,z coordinate checks do not require topology.

    def evaluate(self, prepared_morph, context, policy):   # a PreparedMorphology
        t0 = time.perf_counter()
        result = MetricResult(name=self.name, status="pass")
        n_soma = int((prepared_morph.compartment == 1).sum())
        result.value = n_soma                # canonical headline scalar
        result.value_label = "n_soma"
        result.measurements = {"n_soma": n_soma}
        result.counts = {"n_soma": n_soma}
        if n_soma != 1:
            result.status = "fail"
            result.message = f"Expected exactly one soma, found {n_soma}."
        else:
            result.message = "Exactly one soma present."
        result.runtime_ms = (time.perf_counter() - t0) * 1000
        return result


register(SomaExistsMetric())

# Now runnable by name:
run_qc("cell.swc", context, metrics=["soma_exists"])
```

If your metric needs a threshold, declare it in `required_policy_keys` and read it with direct indexing inside `evaluate`. The engine validates all required keys before any metric runs, so by the time `evaluate` is called the key is guaranteed to be present — the indexing is both the safe read path and a second safeguard:

```python
class SomeThresholdMetric(Metric):
    name = "some_threshold"
    ...
    required_policy_keys = frozenset({"max_value"})

    def evaluate(self, prepared_morph, context, policy):
        max_value = policy[self.name, "max_value"]   # raises MissingPolicyValueError if absent
        ...
```

Add the new key to `policy_v1` in `standard_morph/policies.py`. If you omit `required_policy_keys`, the engine won't catch a missing key at pre-flight — the `policy[self.name, key]` call will still raise `MissingPolicyValueError` at runtime, but the error message will be less informative than the aggregate pre-flight report.

An **input-integrity** metric instead sets `evaluation_phase = EvaluationPhase.INPUT_INTEGRITY`, a `blocks_on_failure` scope, and receives the raw DataFrame (named `swc_df`) — see `standard_morph/metrics/integrity.py`.


## Visualizing QC errors in Neuroglancer

`standard_morph.neuroglancer` turns QC runs into [Neuroglancer](https://github.com/google/neuroglancer) precomputed layers and links, so you can see each flagged node in place:

* **`skeletons/`** is a skeleton source with one segment per neuron. Each node carries `radius`, `n_flags` (the number of metrics that flagged it), and a `flag_<metric>` attribute (0 or 1) for every metric that flagged a node. By default the skeleton shader draws flagged nodes in red. Segment properties hold each neuron's label, tags for its failing metrics, and a flag count per metric.
* **`annotations/<metric>/`** is one point-annotation source per metric, with a point at each flagged node. Each point stores the SWC `node_id` and the result `status`, and links to its neuron's segment, so each layer shows errors only for the neurons selected in the skeleton layer.

```python
from standard_morph import run_qc
from standard_morph.neuroglancer import NeuroglancerExporter

exporter = NeuroglancerExporter(scale_um=(0.748, 0.748, 1.0))  # um per SWC unit (x, y, z)
for path in swc_paths:
    report = run_qc(path, context, metrics=metrics, policy=policy)
    segment_id = exporter.add(path, report)

exporter.write("out/ng")                     # then publish out/ng at s3://bucket/prefix
batch_url = exporter.batch_url("s3://bucket/prefix", image_source="zarr://s3://.../fused.zarr")
neuron_url = exporter.neuron_url(1, "s3://bucket/prefix")  # one neuron, centered on its first error
```

* **`scale_um`** is the physical size of one SWC unit. Coordinates are never converted, so use `(1, 1, 1)` for SWCs in micrometers and the voxel size for SWCs in voxel indices. A wrong scale misaligns the layers with the image.
* **`image_source`** is optional. It is any Neuroglancer image source, added under the skeletons.
* **Links** are plain JSON states. `batch_state` and `neuron_state` return them for editing, and `state_to_url` encodes them. The default viewer is `https://neuroglancer-demo.appspot.com`. Pass `base_url=` to use another deployment.
* **Hosting:** Neuroglancer fetches the layers from the browser. An `s3://` source must be publicly readable, with CORS that allows `GET`.
* **Dependencies:** writing annotations needs the `neuroglancer` extra. Skeletons, states and links need only the core dependencies.
* **Unbuildable files:** a file whose morphology could not be built (a BUILD integrity failure) gets no skeleton, but any flagged nodes that have coordinates are still annotated.

## Programmatic discovery

```python
from standard_morph import REGISTRY, available_suites, available_policies

REGISTRY.names()        # ['branch_max_degree', 'local_tortuosity', 'nodes_outside_ccf_mesh', ...]
available_suites()      # built-in suite names
available_policies()    # ['policy_v1']
```


## Legacy `Standardizer`

The original monolithic `Standardizer` / `tools` API has been retired to `standard_morph/_archived/` and is no longer the supported interface. It remains importable for reference during migration:

```python
from standard_morph._archived.Standardizer import Standardizer  # deprecated
```

New work should use `run_qc`.


## Release Maintenance

