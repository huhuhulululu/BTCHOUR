"""Research harness: synthetic tapes, out-of-sample splits, bootstrap metrics.

Nothing here trades. It exists so a claim about EV carries a sample size and a
confidence interval instead of an anecdote.
"""

from btchour.research.metrics import bootstrap_ci, summarize_takes
from btchour.research.sim import SimConfig, simulate_tape, simulate_tapes

__all__ = [
    "SimConfig",
    "simulate_tape",
    "simulate_tapes",
    "bootstrap_ci",
    "summarize_takes",
]
