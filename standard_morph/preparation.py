"""Shared morphology preparation for the QC framework.

`PreparedMorphology` is the single structure every morphology-metric consumes. An SWC is a
rooted tree where each node has exactly one parent, so the whole topology is
described by one integer per node. We reindex nodes to a contiguous ``0..N-1``
index space and back that with numpy arrays:

* ``parent``      -- (N,) parent *index*, or ``ROOT`` / ``MISSING`` sentinels
* ``xyz``         -- (N, 3) coordinates for vectorized geometry
* ``compartment`` -- (N,) SWC type codes
* ``children``    -- adjacency derived once from ``parent``

This gives O(1) parent lookup and cache-friendly traversal without a heavyweight
graph object, while the accompanying ``df`` keeps a tabular face for vectorized
column math and report rows. Multi-hop primitives (segments, components) are
computed once and cached, and are shared across metrics.
"""
import numpy as np
import pandas as pd

SWC_COLUMN_NAMES = ["node_id", "compartment", "x", "y", "z", "r", "parent"]


class PreparedMorphology:
    """Array-backed, contiguous-indexed view of an SWC morphology."""

    #: Sentinel: node is a root (SWC parent == -1).
    ROOT = -1
    #: Sentinel: node references a parent id that does not exist in the file.
    MISSING = -2
    #: SWC compartment code for the soma.
    SOMA_COMPARTMENT = 1

    def __init__(self, *, node_id, parent, orig_parent, xyz, compartment, radius, df=None):
        self.node_id = np.asarray(node_id, dtype=np.int64)  # index -> original SWC id
        self.parent = np.asarray(parent, dtype=np.int64)     # index -> parent index / sentinel
        self.orig_parent = np.asarray(orig_parent, dtype=np.int64)
        self.xyz = np.asarray(xyz, dtype=float)
        self.compartment = np.asarray(compartment, dtype=np.int64)
        self.radius = np.asarray(radius, dtype=float)
        self.n = int(self.node_id.shape[0])

        self._id_to_index = {int(nid): i for i, nid in enumerate(self.node_id)}
        self._children = self._build_children()
        self.child_counts = np.array([len(c) for c in self._children], dtype=np.int64)
        self.df = df
        self._segments = None
        self._component_labels = None

    # ------------------------------------------------------------------ build
    @classmethod
    def from_dataframe(cls, df, coordinate_scale=(1.0, 1.0, 1.0)):
        """Build from a DataFrame with the standard SWC columns.

        Expects columns ``node_id, compartment, x, y, z, r, parent``. Node ids
        need not be contiguous or sorted; they are remapped internally.

        Parameters
        ----------
        df : pandas.DataFrame
            Raw SWC table.
        coordinate_scale : tuple of float, length 3
            Per-axis multiplier ``(sx, sy, sz)`` applied to raw XYZ before
            storing. Pass ``context.coordinate_scale`` to convert voxel
            coordinates to microns or to undo tissue expansion. Default is
            no-op; see :class:`~standard_morph.models.qc_context.QCContext`
            for usage examples.
        """
        missing_cols = [c for c in SWC_COLUMN_NAMES if c not in df.columns]
        if missing_cols:
            raise ValueError(f"DataFrame is missing required SWC columns: {missing_cols}")

        df = df.reset_index(drop=True)
        node_id = df["node_id"].to_numpy(dtype=np.int64)
        orig_parent = df["parent"].to_numpy(dtype=np.int64)
        xyz = df[["x", "y", "z"]].to_numpy(dtype=float) * np.array(coordinate_scale)
        compartment = df["compartment"].to_numpy(dtype=np.int64)
        radius = df["r"].to_numpy(dtype=float)  # radius is a required SWC column

        id_to_index = {int(nid): i for i, nid in enumerate(node_id)}
        parent = np.empty(len(df), dtype=np.int64)
        for i, p in enumerate(orig_parent):
            p = int(p)
            if p == -1:
                parent[i] = cls.ROOT
            else:
                parent[i] = id_to_index.get(p, cls.MISSING)

        return cls(
            node_id=node_id,
            parent=parent,
            orig_parent=orig_parent,
            xyz=xyz,
            compartment=compartment,
            radius=radius,
            df=df,
        )

    def _build_children(self):
        children = [[] for _ in range(len(self.node_id))]
        for i, p in enumerate(self.parent):
            if p >= 0:  # skip ROOT (-1) and MISSING (-2)
                children[int(p)].append(i)
        return children

    # ------------------------------------------------------------- accessors
    def children(self, i):
        """Return the list of child indices for node index ``i``."""
        return self._children[i]

    def index_of(self, original_node_id):
        """Return the contiguous index for an original SWC node id."""
        return self._id_to_index[int(original_node_id)]

    @property
    def roots(self):
        """Indices of root nodes (SWC parent == -1)."""
        return np.flatnonzero(self.parent == self.ROOT)

    @property
    def soma_roots(self):
        """Indices of soma nodes: compartment == soma **and** a root (parent == -1).

        This is the canonical definition of "the soma" for the whole library.
        Compartment alone is not enough: a malformed file can carry a stray type-1
        node mid-tree, which is *not* the soma. A well-formed file has exactly one
        soma root (enforced by ``single_root_node``); this returns the set so it
        degrades sensibly on malformed input (0 or >1 soma roots).
        """
        return np.flatnonzero(
            (self.compartment == self.SOMA_COMPARTMENT) & (self.parent == self.ROOT)
        )

    @property
    def orphans(self):
        """Indices of nodes whose parent id is absent from the file."""
        return np.flatnonzero(self.parent == self.MISSING)

    @property
    def tips(self):
        """Indices of leaf nodes (no children)."""
        return np.flatnonzero(self.child_counts == 0)

    @property
    def branch_points(self):
        """Indices of branch nodes (two or more children)."""
        return np.flatnonzero(self.child_counts >= 2)

    @property
    def segments(self):
        """Unbranched segments, computed once and cached.

        A segment is a maximal path whose interior nodes each have exactly one
        child. Each segment is a list of node *indices* running from one
        boundary node (root or branch point) to the next boundary node (branch
        point or tip), inclusive. Adjacent segments therefore share their
        branch-point endpoints. This is the shared primitive for metrics that
        reason along paths (tortuosity, node-type transitions, edge lengths).
        """
        if self._segments is None:
            self._segments = self._compute_segments()
        return self._segments

    def _compute_segments(self):
        # Segment starts are roots and branch points. Walk each of their
        # children down single-child chains until the next boundary node.
        start_nodes = np.union1d(self.roots, self.branch_points)  # sorted, deterministic
        segments = []
        for s in start_nodes:
            for c in self._children[int(s)]:
                seg = [int(s), int(c)]
                cur = c
                while self.child_counts[cur] == 1:
                    cur = self._children[cur][0]
                    seg.append(int(cur))
                segments.append(seg)
        return segments

    @property
    def component_labels(self):
        """(N,) array labelling each node with its connected-component id.

        Connectivity is undirected over parent-child edges. Nodes whose parent
        id is missing (orphans) are not joined to any tree and therefore form
        their own component -- which is exactly what a broken reconstruction
        should look like. Labels are contiguous ``0..k-1``.
        """
        if self._component_labels is None:
            self._component_labels = self._compute_components()
        return self._component_labels

    @property
    def n_components(self):
        """Number of connected components (1 for a well-formed single tree)."""
        if self.n == 0:
            return 0
        return int(self.component_labels.max()) + 1

    def _compute_components(self):
        # Union-find over parent-child edges with iterative path compression.
        uf = np.arange(self.n, dtype=np.int64)

        def find(x):
            root = x
            while uf[root] != root:
                root = uf[root]
            while uf[x] != root:  # path compression
                uf[x], x = root, uf[x]
            return root

        for i, p in enumerate(self.parent):
            if p >= 0:
                ri, rp = find(i), find(int(p))
                if ri != rp:
                    uf[ri] = rp

        roots = np.array([find(i) for i in range(self.n)], dtype=np.int64)
        # Remap arbitrary root ids to contiguous 0..k-1, ordered by first appearance.
        _, labels = np.unique(roots, return_inverse=True)
        return labels.astype(np.int64)
