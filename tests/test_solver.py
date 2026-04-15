from __future__ import annotations

import json
import unittest
from pathlib import Path

from flowfreelike.models import Dot
from flowfreelike.solver import solve_puzzle


class SolverRegressionTests(unittest.TestCase):
    def test_large_sample_level_solves_uniquely(self) -> None:
        root = Path(__file__).resolve().parents[1]
        data = json.loads((root / "levels" / "0001.json").read_text(encoding="utf-8"))
        dots = [
            Dot(
                color_id=item["color_id"],
                p1=tuple(item["p1"]),
                p2=tuple(item["p2"]),
            )
            for item in data["dots"]
        ]

        result = solve_puzzle(
            size=data["grid_size"],
            dots=dots,
            solution_limit=2,
            completion_mode="full",
            path_cap=768,
        )

        self.assertTrue(result.is_unique)
        self.assertEqual(len(result.solutions), 1)


if __name__ == "__main__":
    unittest.main()
