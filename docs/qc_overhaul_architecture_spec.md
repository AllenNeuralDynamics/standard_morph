# QC Overhaul Architecture Spec (Draft v1)

## 1. Scope and Goals

This document defines the architecture for the `standard_morph` QC overhaul before implementation.

Primary goals:
1. Preserve familiar workflow while enabling modular QC.
2. Support exactly two coordinate spaces: `image_space` and `ccf_registered`.
3. Allow predefined test suites (`default_pre_registration_tests`, `default_post_registration_tests`) and user-defined custom suites.
4. Enforce strict context compatibility: if any requested metric is incompatible with input context, raise an error and halt execution immediately. E.g. An error is raised if an "out of atlas" check is run on `image_space` coordinate space.  
5. Support quantitative metrics with both raw measurements and threshold-based pass/fail decisions. Keep track of the values and whether they are above the thresholds defined.
6. Version thresholds/policies collaboratively. A configuration file that sets thresholds for each test (that requires one), rather than asking the use to set the thresholds when the test is run. 
7. Return all flagged nodes for failed checks (no sampling/capping).

## 2. Key Terms

1. **Context**: Runtime metadata required to evaluate metric applicability
2. **Metric**: A single QC check with applicability (context) rules and computation logic. Not every test makes sense for every context.
3. **Suite**: Named list of metrics
4. **Policy**: Versioned threshold dictionary used by metrics. E.g. 
```{ axon_origin_from_soma_distance threshold: 50 }```
5. **Run Report**: Structured output with per-metric results and run summary.

## 3. Context Model

`QCContext` must include:
1. `space`: `image_space` or `ccf_registered` (required, user-provided)
2. `morphology_kind`: `axon`, `dendrite`, or `merged`
3. `resources`: optional external inputs needed by specific checks (for example image path/ome-zarr path for image-based checks, or CCF atlas path)
4. `policy_version`: selected threshold policy version


## 4. Metrics

Each metric implements a standard interface and metadata:
1. `name`:  (string). 
2. `display_name`: human-readable title.
3. `applicability`: When is it applicable to run this test? For example, the soma-node-and-soma-image-alignment Metric is only able to be run when:
    * `space` =  `image_space`
    * `morphology kind` = any kind allowed (dend only, merged, etc.)
    * `resources` contains the path to the omezarr 
4. `evaluate(prepared_data, context, policy) -> MetricResult`

`MetricResult` fields:
1. `name`
2. `status`: `pass`, `fail`, `review` (flagged for human oversight), `error`, `skipped`
3. `message`
4. `thresholds_used`
5. `measurements` (quantitative outputs)
6. `flagged_node_ids` (full list)
7. `flagged_node_coordinates` (full list)
8. `counts` (for convenience, even though full lists are included)
9. `runtime_ms`

## 5. Execution Engine Rules

Execution steps:
1. Parse and validate context.
2. Resolve suite into ordered metric list.
3. Validate applicability of each metric against context.
4. If any requested metric is incompatible, raise `IncompatibleMetricContextError` and stop immediately.
5. Prepare shared morphology/graph features once.
6. Execute metrics in deterministic order.
7. Aggregate results into run report.

Fail-fast policy:
1. Incompatibility errors are terminal and halt run.
2. Runtime exceptions in metric code are terminal by default for v1.

## 6. Suites and Suite Configs

Definition used in this project:
1. A **suite** is a named collection of metric names and optional per-metric threshold overrides.

Supported config sources:
1. Python objects.
2. YAML/JSON files (supported for pipeline portability and non-code configuration).

Built-in suites (v1):
1. `default_pre_registration_tests`
2. `default_post_registration_tests`

Custom suites:
1. Users can pass explicit metric names directly.
2. Users can load suite definitions from YAML/JSON.

## 7. Threshold Policy Versioning

Policy model:
1. Store default thresholds in versioned policy definitions.
2. Include policy version in every run report.
3. Allow collaborative updates via version bump (for example `policy_v1`, `policy_v2`).

Governance:
1. Threshold changes are collaborative and must be traceable in version history.
2. Threshold policy must be included in the output report.

## 8. Quantitative Reporting Requirements

For metrics producing quantities (for example count of large sidesteps):
1. Always store quantitative measurements in `measurements`.
2. Evaluate pass/fail against policy thresholds.
3. Include full `flagged_node_ids` list.
4. Include convenient summary counts in `counts`.



##### ToDo: revise the below.

## 9. Proposed Package Layout

1. `standard_morph/qc/__init__.py` (public QC entrypoints and stable exports)
2. `standard_morph/qc/models/` (QC object model package)
    - `__init__.py` (shared model exports for the QC object layer)
    - `qc_context.py` (QC context object and any shared base context types)
    - `qc_result.py` (metric and run result objects)
    - `qc_policy.py` (versioned threshold policy objects)
    - `qc_run.py` (run-level summary and aggregate report objects)
3. `standard_morph/qc/exceptions.py` (context validation and execution exception types)
4. `standard_morph/qc/preparation.py` (shared morphology/graph feature preparation and caching)
5. `standard_morph/qc/registry.py` (metric registration, metadata, and lookup)
6. `standard_morph/qc/engine.py` (suite resolution, validation, execution, and aggregation)
7. `standard_morph/qc/suites.py` (built-in suite definitions and suite composition helpers)
8. `standard_morph/qc/policies.py` (versioned threshold policy definitions and loading helpers)
9. `standard_morph/qc/config_io.py` (Python/YAML/JSON suite and policy loading)
10. `standard_morph/qc/metrics/` (individual metric modules grouped by concern)
11. `standard_morph/qc/reporting/` (HTML and structured report adapters specific to QC runs)

## 10. API Shape (v1)

Programmatic API:
1. `run_qc(input_data, context, suite_name=None, metrics=None, policy_version="policy_v1")`
2. Exactly one of `suite_name` or `metrics` is required.

## 11. Metric Inventory

Full set of 17 QC metrics. Each entry lists: stage applicability, whether the check is eligible for automated pre-refinement failure, current implementation status in `standard_morph`, and outstanding gaps or notes. Implementation status is one of: **Yes** (fully implemented), **Partial** (exists but incomplete), or **No** (not yet implemented).

The SWC processing pipeline stages in order: (1) merge axon and dendrite SWCs, (2) snap nodes to centroid of signal, (3) A\* search to generate dense paths, (4) transform to CCF, (5) resample to ~10 µm spacing. "Pre" and "Post" refer to before and after CCF registration (step 4).

---

**Metric 1 — Single root node validation**
- Stage: Pre and Post | Auto-fail pre-refinement: Yes | Status: **Partial**
- Description: SWC axon and dendrite files must have a single root node; merged files must have a single root node. Root node should be the first line of the SWC, `node_id == 1`, `node_type == 1`, `parent == -1` and no other node should have `node_type == 1`. 
- Discussion/Questions: previously standard_morph will write a DFS sorted swc file. Still do that? 

---

**Metric 2 — Soma inside registered CCF brain mesh**
- Stage: Post only | Auto-fail pre-refinement: No | Status: **No**
- Description: Soma node is located inside the brain mesh of the CCF when registered. Capture distance from brain surface.
- Discussion/Questions: what resolution atlas will AIND be registering too? 10um/vox? 

---

**Metric 3 — Nodes outside brain mesh after CCF registration**
- Stage: Post only | Auto-fail pre-refinement: No | Status: **No**
- Description: Flag axon and dendrite nodes that fall outside the brain mesh after CCF registration. Capture the count of out-of-mesh nodes.
- Discussion/Questions: what resolution atlas will AIND be registering too? 10um/vox? What threshold is acceptable? <5% of nodes? 
---

**Metric 4 — Pre-resampling edge length threshold**
- Stage: Pre only | Auto-fail pre-refinement: Yes | Status: **Yes**
- Description: Edge length prior to resampling must not exceed a threshold (e.g. 30 µm) to avoid erroneously placed nodes or mislabeled root. Flag long/straight paths with endpoint pair and distance.
- Discussion/Questions: what resolution atlas will AIND be registering too? 10um/vox? What threshold is acceptable? <5% of nodes? 

---

**Metric 5 — Post-resampling internode distance threshold**
- Stage: Post only | Auto-fail pre-refinement: No | Status: **Yes**
- Description: Internode distance post-refinement/resampling must not exceed 10 µm. Resampling strategy must hold branch and terminal nodes fixed and sample along existing paths.
- Note: Currently tracks edges above threshold. Additional statistics could be added for the QC portal.

---

**Metric 6 — Expected node identity types in merged file**
- Stage: Pre and Post | Auto-fail pre-refinement: Yes | Status: **No**
- Description: Merged file must contain soma, dendrite, and axon node identities. List unique node types present and flag unexpected values (e.g. values other than 1/2/3 or 1/2/3/4). Flag cases where axon and dendrite are traced using the same node type; separately flag axon-only/soma-only or dendrite-only morphologies.

---

**Metric 7 — SWC filename convention**
- Stage: Pre and Post | Auto-fail pre-refinement: Yes | Status: **Yes**
- Description: SWC file must fulfill expected naming convention: `N###-SampleID-annotator_initials-partitioned_morphology(axon/dendrite/CONSENSUS).swc`.
- Note: AIND-specific convention only.

---

**Metric 8 — SWC file format/content convention**
- Stage: Pre and Post | Auto-fail pre-refinement: Yes | Status: **Partial**
- Description: SWC file contents must fulfill expected format conventions: non-corrupted content, no null values, consistent whitespace (no mixing of spaces and tabs).
- Gap: Basic SWC/dataframe loading, required columns, empty dataframe checks, and type casts exist. No explicit null-value or whitespace-consistency QC report.

---

**Metric 9 — Root node centered on soma image position**
- Stage: Pre and Post | Auto-fail pre-refinement: Yes | Status: **Yes** (`root_near_detected_soma`; `soma_at_centroid` for a single known centroid)
- Description: Root node must be centered on the soma image position. Compute distance from root node to annotated soma centroid and flag above threshold; include overlay image.
- Implementation: `root_near_detected_soma` checks the root against every automated soma detection in the brain, passed in through `resources` (it passes if the root is within any detection's clamped radius, and a miss is graded `review`). Image space only; there is no post-registration variant yet. `soma_at_centroid` covers a single known centroid and can render the soma MIP overlay.

---

**Metric 10 — Large sidestep / local tortuosity deviations**
- Stage: Pre only | Auto-fail pre-refinement: Yes | Status: **No**
- Description: Check for nodes with large sidestep deviations off the main path (extreme local tortuosity). Flag the number of points above threshold.
- Note: Accurate implementation requires reference to image data, since sharp turns frequently occur in real neurons.

---

**Metric 11 — Duplicate node coordinates**
- Stage: Post only | Auto-fail pre-refinement: No | Status: **Partial**
- Description: Detect duplicate nodes based on (x, y, z) coordinate identity.
- Gap: Implementation exists but is commented out; needs to be re-enabled, tested, and integrated into the metric framework.

---

**Metric 12 — Duplicate segments / path overlays**
- Stage: Post only | Auto-fail pre-refinement: No | Status: **No**
- Description: Detect duplicate segments where paths overlay without producing duplicate nodes.
- Note: Non-trivial to implement. Algorithm design and edge-case enumeration are required before implementation.

---

**Metric 13 — Axon origination distance from soma/basal dendrite**
- Stage: Pre only | Auto-fail pre-refinement: Yes | Status: **Yes**
- Description: Axon origination must not be greater than approximately 50 µm from the soma or basal dendrite.

---

**Metric 14 — Multiple apical dendrite origination points**
- Stage: Pre and Post | Auto-fail pre-refinement: Yes | Status: **No**
- Description: Flag multiple apical dendrite points of origination (while noting this may be biologically plausible).
- Note: AIND tracings do not distinguish basal/apical dendrite, so apical compartment identity would need to be manually annotated. Implementation would target apical-annotated compartments when available.

---

**Metric 15 — Node type transition within unbranched segment**
- Stage: Pre and Post | Auto-fail pre-refinement: Yes | Status: **Partial**
- Description: Node types must not change within an unbranched segment. Without a branch point, a segment must not convert from one compartment type (e.g. type 3) to another (e.g. type 2).
- Gap: `AxonOrigins` and `DendriteOrigins` catch invalid parent/child compartment transitions at branch points, but do not enforce the segment-level unbranched constraint.

---

**Metric 16 — Single connected component**
- Stage: Pre and Post | Auto-fail pre-refinement: Yes | Status: **Partial**
- Description: Each SWC must be validated as a single connected component.
- Gap: `OrphanedNodes` catches missing parents; `CheckForLoops` traverses from roots; however, the presence of multiple root components is not explicitly failed.

---

**Metric 17 — Branch points with more than two daughters**
- Stage: Pre only | Auto-fail pre-refinement: Yes | Status: **Partial**
- Description: Branch points with more than two daughter nodes should be flagged for interrogation.
- Gap: `MaxNodeDegree` exists, but the default failure threshold is >4 children rather than >2. Soma node is (correctly) excluded from this check.


## 13. Things to Decide Still

1. Exact exception hierarchy and error message format for incompatibility failures.
2. Schema version key name for reports (`schema_version` recommended).
3. When to bring deferred metrics into active development, including:
    - `Root node centered on soma image position`
    - `Duplicate segments / path overlays`
4. Whether deferred metrics remain out of v1 execution suites until those designs are finalized.

## 14. Acceptance Criteria for This Architecture

1. User provides `space` explicitly, and stage is derived internally with no user-entered stage field.
2. Incompatible metric request halts run immediately with clear error.
3. Every metric result contains quantitative data when applicable and full flagged node lists.
4. Threshold policy is versioned and reported.
5. Deferred metrics are tracked outside active suites and can be added later without requiring architecture rewrites.
