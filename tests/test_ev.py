from __future__ import annotations

import unittest

from btchour.cli import main
from btchour.fees import Edge, ev, fill_cost


class EvFormulaTests(unittest.TestCase):
    def test_user_formula(self):
        p, b = 0.99, 0.25
        self.assertAlmostEqual(ev(p, b), p * b - (1 - p))
        self.assertAlmostEqual(ev(p, b), 0.2375)

    def test_coin_flip_zero_edge(self):
        self.assertAlmostEqual(ev(0.5, 1.0), 0.0)

    def test_edge_object(self):
        edge = Edge.from_parts(0.99, 0.25)
        self.assertEqual(edge.p, 0.99)
        self.assertEqual(edge.b, 0.25)
        self.assertAlmostEqual(edge.ev, 0.2375)
        self.assertGreaterEqual(edge.ev, 0.20)

    def test_same_as_expected_roi(self):
        cost = fill_cost(0.80, taker=True)
        edge = cost.edge(0.99)
        self.assertAlmostEqual(edge.b, cost.if_win_roi)
        self.assertAlmostEqual(edge.ev, cost.expected_roi(0.99))
        self.assertAlmostEqual(edge.ev, (0.99 - cost.cost) / cost.cost)

    def test_cli(self):
        self.assertEqual(main(["ev", "--p", "0.99", "--b", "0.25"]), 0)
