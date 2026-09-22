import unittest

import numpy as np
import pandas as pd

from standard_morph.preparation import PreparedMorphology


def _df(rows):
    """rows: list of (node_id, compartment, x, y, z, r, parent)."""
    return pd.DataFrame(rows, columns=["node_id", "compartment", "x", "y", "z", "r", "parent"])


class TestPreparedMorphology(unittest.TestCase):
    def _y_tree(self):
        # 1 -> 2 -> 3(branch) -> {4 -> 5, 6 -> 7}
        return _df([
            (1, 1, 0.0, 0.0, 0.0, 1.0, -1),
            (2, 3, 1.0, 0.0, 0.0, 1.0, 1),
            (3, 3, 2.0, 0.0, 0.0, 1.0, 2),
            (4, 3, 3.0, 1.0, 0.0, 1.0, 3),
            (5, 3, 4.0, 1.0, 0.0, 1.0, 4),
            (6, 3, 3.0, -1.0, 0.0, 1.0, 3),
            (7, 3, 4.0, -1.0, 0.0, 1.0, 6),
        ])

    def test_topology_counts(self):
        pm = PreparedMorphology.from_dataframe(self._y_tree())
        self.assertEqual(pm.n, 7)
        self.assertEqual(pm.node_id[pm.roots].tolist(), [1])
        self.assertEqual(sorted(pm.node_id[pm.tips].tolist()), [5, 7])
        self.assertEqual(pm.node_id[pm.branch_points].tolist(), [3])
        self.assertEqual(pm.orphans.size, 0)

    def test_segments(self):
        pm = PreparedMorphology.from_dataframe(self._y_tree())
        seg_ids = {tuple(int(pm.node_id[i]) for i in seg) for seg in pm.segments}
        self.assertEqual(seg_ids, {(1, 2, 3), (3, 4, 5), (3, 6, 7)})

    def test_soma_roots_requires_type_and_root(self):
        # The soma is type 1 AND a root -- a stray mid-tree type-1 node is not it.
        df = _df([
            (1, 1, 0.0, 0.0, 0.0, 1.0, -1),   # the soma root
            (2, 3, 1.0, 0.0, 0.0, 1.0, 1),
            (3, 1, 2.0, 0.0, 0.0, 1.0, 2),    # stray type-1 node, has a parent
        ])
        pm = PreparedMorphology.from_dataframe(df)
        self.assertEqual(pm.node_id[pm.soma_roots].tolist(), [1])

    def test_non_contiguous_ids_are_remapped(self):
        # Same shape as the y-tree but with arbitrary, unsorted node ids.
        df = _df([
            (100, 1, 0.0, 0.0, 0.0, 1.0, -1),
            (50, 3, 1.0, 0.0, 0.0, 1.0, 100),
            (7, 3, 2.0, 0.0, 0.0, 1.0, 50),
        ])
        pm = PreparedMorphology.from_dataframe(df)
        self.assertEqual(pm.node_id[pm.roots].tolist(), [100])
        self.assertEqual(pm.node_id[pm.tips].tolist(), [7])
        seg_ids = {tuple(int(pm.node_id[i]) for i in seg) for seg in pm.segments}
        self.assertEqual(seg_ids, {(100, 50, 7)})

    def test_missing_parent_is_flagged_as_orphan(self):
        df = _df([
            (1, 1, 0.0, 0.0, 0.0, 1.0, -1),
            (2, 3, 1.0, 0.0, 0.0, 1.0, 999),  # parent 999 does not exist
        ])
        pm = PreparedMorphology.from_dataframe(df)
        self.assertEqual(pm.node_id[pm.orphans].tolist(), [2])


class TestCoordinateScale(unittest.TestCase):
    def _simple_df(self):
        return _df([
            (1, 1, 1.0, 2.0, 3.0, 1.0, -1),
            (2, 3, 4.0, 5.0, 6.0, 1.0, 1),
        ])

    def test_default_identity_leaves_xyz_unchanged(self):
        pm = PreparedMorphology.from_dataframe(self._simple_df())
        np.testing.assert_array_equal(pm.xyz[0], [1.0, 2.0, 3.0])
        np.testing.assert_array_equal(pm.xyz[1], [4.0, 5.0, 6.0])

    def test_uniform_scale_multiplies_all_axes(self):
        pm = PreparedMorphology.from_dataframe(self._simple_df(), coordinate_scale=(2.0, 2.0, 2.0))
        np.testing.assert_array_equal(pm.xyz[0], [2.0, 4.0, 6.0])
        np.testing.assert_array_equal(pm.xyz[1], [8.0, 10.0, 12.0])

    def test_per_axis_scale_multiplies_independently(self):
        pm = PreparedMorphology.from_dataframe(self._simple_df(), coordinate_scale=(1.0, 2.0, 3.0))
        np.testing.assert_array_equal(pm.xyz[0], [1.0, 4.0, 9.0])
        np.testing.assert_array_equal(pm.xyz[1], [4.0, 10.0, 18.0])

    def test_raw_df_is_unchanged(self):
        df = self._simple_df()
        PreparedMorphology.from_dataframe(df, coordinate_scale=(10.0, 10.0, 10.0))
        # The original DataFrame must not be modified.
        self.assertAlmostEqual(df["x"].iloc[0], 1.0)
        self.assertAlmostEqual(df["y"].iloc[0], 2.0)
        self.assertAlmostEqual(df["z"].iloc[0], 3.0)

    def test_int_scale_elements_work(self):
        pm = PreparedMorphology.from_dataframe(self._simple_df(), coordinate_scale=(2, 2, 2))
        np.testing.assert_array_equal(pm.xyz[0], [2.0, 4.0, 6.0])


if __name__ == "__main__":
    unittest.main()
