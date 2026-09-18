import unittest

from btchour.fees import Edge, ev_from_cost, fill_cost, max_cost_for_ev
from btchour.model import required_p


class EvIdentityTests(unittest.TestCase):
    def test_ev_collapses_to_price_over_probability(self):
        for price in (0.05, 0.25, 0.42, 0.62, 0.83):
            cost = fill_cost(price, 1.0, taker=True).cost
            for p in (0.2, 0.5, 0.9, 0.999):
                direct = Edge.from_parts(p, fill_cost(price, 1.0, taker=True).if_win_roi).ev
                self.assertAlmostEqual(direct, ev_from_cost(p, cost), places=9)

    def test_twenty_percent_gate_is_a_sixteen_percent_discount(self):
        for p in (0.3, 0.6, 0.95, 1.0):
            cap = max_cost_for_ev(0.20, p)
            self.assertAlmostEqual(cap, p / 1.2, places=12)
            self.assertAlmostEqual(ev_from_cost(p, cap), 0.20, places=12)

    def test_gate_caps_the_payable_price_at_83_cents(self):
        # p cannot exceed 1, so no contract costing more than 1/1.2 ever clears
        # a 20% gate -- the lock playbook's $0.82/$0.83 ceiling is this bound.
        self.assertAlmostEqual(max_cost_for_ev(0.20, 1.0), 0.8333, places=4)

    def test_required_p_agrees_with_the_cost_form(self):
        for price in (0.25, 0.52, 0.82):
            cost = fill_cost(price, 1.0, taker=True)
            self.assertAlmostEqual(
                required_p(0.20, cost.if_win_roi),
                1.2 * cost.cost,
                places=9,
            )


if __name__ == "__main__":
    unittest.main()
