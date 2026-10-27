# Standard Morph — QC Architecture Overview

*A narrative walkthrough of the QC overhaul: what changed, how it's built, and why. For the design-decision rationale behind each piece; the [README](../README.md) is the usage reference.*

---

## 1. Motivation

The original `standard_morph` was a single `Standardizer` class plus a `tools.py` of QC functions. It worked, but it had structural limits:

- **Thresholds were constructor arguments** — set at the call site, not versioned or traceable.
- **A fixed `validate()` sequence** — every check ran, in one order, with no notion of *when* a check applies.
- **No concept of coordinate space or context** — the same checks ran whether the cell was in image space or CCF-registered space, even when a check only makes sense in one.
- **Loose dict results** — hard to aggregate, trend, or feed a QC portal.
- **Malformed input crashed** — a bad file threw an exception instead of producing a QC record.

The overhaul keeps the familiar workflow but rebuilds the internals as a **modular, context-aware QC engine** with a single entrypoint, `run_qc()`.

---

## 2. The pipeline at a glance

```
input (SWC path │ DataFrame │ PreparedMorphology)
        +
   QCContext (space, morphology_kind, resources, ccf_resolution, policy_version)
        │
        ▼
   run_qc()
        │  resolve suite / metric list
        │  fail-fast applicability check   ── incompatible? → IncompatibleMetricContextError
        │
        ├─ Phase 1 · INPUT INTEGRITY   (reads the raw table)
        │     required_columns · non_empty · castable_columns · unique_node_ids
        │            │
        │            ├─ BUILD-scope failure     → skip ALL morphology metrics
        │            └─ TOPOLOGY-scope failure  → skip only topology-dependent metrics
        │
        ├─ Phase 2 · MORPHOLOGY QUALITY   (reads the built PreparedMorphology)
        │     single_root_node · single_connected_component · local_tortuosity · …
        │
        ▼
   RunReport (integrity_results + results + summary + passed)
```

A malformed file flows through the *same* path and produces the *same shape* of report — it just names the integrity failure and marks the un-runnable metrics `"skipped"`. Never a stack trace.

---

## 3. Core concepts

| Concept | What it is |
|---------|-----------|
| **Context** (`QCContext`) | Runtime metadata: coordinate `space` (`image_space` / `ccf_registered`), `morphology_kind`, external `resources`, `ccf_resolution`, and `policy_version`. The user supplies `space`; the pipeline stage (pre-/post-registration) is *derived*, never entered. |
| **Metric** | One QC check. Declares its `applicability` (spaces / kinds / resources), its `evaluation_phase`, and — where relevant — `blocks_on_failure` or `requires_topology`. Implements `evaluate(...) → MetricResult`. |
| **Suite** | A named, ordered list of metrics (`default_pre_registration_tests`, `default_post_registration_tests`). |
| **Policy** | A versioned threshold dictionary (`policy_v1`). Metrics read thresholds from the policy, never from the call site; the version is recorded in every report. |
| **RunReport** | Structured output: `integrity_results` + `results`, a summary, and provenance (schema version, library version, policy, context). `to_dict()` is JSON-ready for a database or portal. |

---

## 4. Design decisions — and why

### 4.1 An array-backed data structure, not a graph object

**Decision.** The morphology is a `PreparedMorphology`: contiguous-reindexed numpy arrays (`parent`, `xyz`, `compartment`) with child adjacency and a cached segment/component decomposition derived once. The DataFrame is kept only as the tabular face.

**Why.** An SWC is a rooted forest where each node has exactly one parent — the whole topology is one integer per node. Reindexing to `0..N-1` gives O(1) parent lookups, cache-friendly traversal, and vectorized geometry, without a heavyweight graph object. `networkx` was rejected: at fMOST scale (10⁵–10⁶ nodes) its per-node Python-object overhead is too slow and memory-hungry, and most metrics are vectorized coordinate math it wouldn't help with anyway. The old "merge the DataFrame on itself" trick is fine for *one-hop* parent features but can't express multi-hop traversal (segments, components).

### 4.2 Two phases: input integrity, then morphology quality

**Decision.** A run has two phases. **Input-integrity** metrics read the raw DataFrame *before* a graph is built; **morphology-quality** metrics read the built `PreparedMorphology`. A metric declares which via `evaluation_phase` (default `MORPHOLOGY_QUALITY`, so ordinary metrics need no boilerplate).

**Why.** There are two genuinely different kinds of "wrong": *the table is malformed* vs. *the neuron is malformed*. You can't run graph checks on a graph you couldn't build — but you also shouldn't crash. Splitting the phases lets a malformed file produce a clean, uniform report. This is essential for QC-ing thousands of files unattended.

### 4.3 Scoped blocking — malformed input reports, not crashes

**Decision.** Each integrity failure carries a *scope* (`BlockScope`): `BUILD` (arrays can't be built — e.g. missing column, uncastable value) or `TOPOLOGY` (arrays build fine, but the tree structure is untrustworthy — e.g. duplicate node ids). Morphology metrics declare `requires_topology` (default `True`). The engine matches them:

- **BUILD failure** → skip *every* morphology metric.
- **TOPOLOGY failure** → skip only metrics that need topology; coordinate/attribute-only metrics (like `nodes_outside_ccf_mesh`) **still run**, because their per-node data is intact.

**Why.** "Blocking" isn't all-or-nothing. A duplicate id corrupts the *topology* but not the *coordinates* — so the fraction of nodes outside the CCF mesh is still perfectly computable. The scope (what the failure *broke*) matched against `requires_topology` (what the metric *needs*) decides precisely what can still run. And a stray non-numeric value or null — which used to crash the build or silently become a garbage integer — now becomes a clean report line.

### 4.4 Applicability and fail-fast

**Decision.** Every metric declares the spaces, morphology kinds, and resources it supports. If any requested metric is incompatible with the context, `run_qc` raises `IncompatibleMetricContextError` *before* doing any work.

**Why.** A check that only makes sense post-registration (e.g. "soma inside the CCF brain mesh") should never silently no-op on an image-space cell. Failing loudly and early is safer than a report that quietly omits a check.

### 4.5 Versioned threshold policies

**Decision.** Thresholds live in versioned policy definitions (`policy_v1`), not at the call site. The active version is recorded in every report; changing a threshold means bumping to `policy_v2`.

**Why.** QC runs must be reproducible and thresholds must be traceable and collaboratively governed. "What threshold produced this result?" is answerable from the report alone.

### 4.6 One headline scalar per metric

**Decision.** Every metric that can be summarised by a single number sets `value` + `value_label` — a max, count, or fraction. Genuinely binary checks leave `value = None`.

**Why.** A QC portal wants one sortable, trendable column per check across thousands of cells. The full detail stays in `measurements`; `value` is the headline. (This was direct feedback from the team.)

### 4.7 Full flagged lists, and a registry

**Decision.** Failing metrics return the *complete* list of flagged node ids and coordinates — no sampling or capping. Metrics self-register on import, so adding one is a self-contained file with no engine or suite changes.

**Why.** Reconstructors need every flagged node to act on, not a sample. And a low-friction "one file per metric" path is what lets the 17-metric inventory get built incrementally.

---

## 5. Current status

**12 metrics implemented; 108 tests passing.**

**Morphology-quality (8):**

| # | Metric | Checks |
|---|--------|--------|
| 1 | `single_root_node` | Exactly one soma-root (type 1, parent −1); node_id 1 / first line |
| 2 | `soma_inside_ccf_mesh` | Soma voxel inside the CCF brain mesh |
| 3 | `nodes_outside_ccf_mesh` | Fraction of nodes outside the CCF mesh |
| 6 | `node_identity_types` | Compartment codes valid; merged file has soma + axon + dendrite |
| 10 | `local_tortuosity` | Path/chord ratio over 3-node windows (reducible + branch, averaged) |
| 11 | `duplicate_node_coordinates` | Nodes sharing identical (x, y, z) |
| 16 | `single_connected_component` | One connected tree (extra roots / orphans) |
| 17 | `branch_max_degree` | Branch points with more than 2 children (soma excluded) |

**Input-integrity (4):** `required_columns`, `non_empty`, `castable_columns`, `unique_node_ids`.

**Also:** the 10 µm Allen CCF annotation atlas is bundled and cached; the package was flattened (`qc/` → `standard_morph/`) and the legacy `Standardizer`/`tools` moved to `standard_morph/_archived/`.

---

## 6. Roadmap

**Remaining morphology metrics (of the 17):**

- **Pure graph / geometry** (buildable now): 4 & 5 (pre/post-resampling edge length), 13 (axon origin distance + valid parent-child origins), 15 (node-type transition within an unbranched segment).
- **Needs a resource or convention**: 7 (filename — needs the file name), 9 (root centered on soma *image* position — `root_near_detected_soma` checks it against automated soma detections in image space; a CCF-space variant is still open), 14 (multiple apical origins — needs apical/basal annotation AIND tracings don't yet carry).
- **Needs algorithm design**: 12 (duplicate segments / path overlays).
- **Scalar companions** to metrics we have: 2b (soma distance-to-surface), 3b (max node distance from mesh).
- **Deprioritized**: 8's whitespace-consistency check — our parser is delimiter-agnostic, so it's pedantic for this pipeline.

**Framework work:** a cycle/loops integrity check, QC-specific HTML/structured report adapters, tuning the `local_tortuosity` threshold against a labelled set, and eventually retiring the archived `Standardizer`.
