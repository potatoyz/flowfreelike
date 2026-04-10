from __future__ import annotations

import unittest

from flowfreelike.generator import _grade, generate_level
from flowfreelike.solver import solve_puzzle


class GeneratorTests(unittest.TestCase):
    def test_generation_grades_from_blind_solve(self) -> None:
        puzzle, result = generate_level(size=5, seed=0, max_attempts=300)

        blind_result = solve_puzzle(
            size=puzzle.grid_size,
            dots=puzzle.dots,
            solution_limit=1,
            completion_mode="full",
        )
        unique_result = solve_puzzle(
            size=puzzle.grid_size,
            dots=puzzle.dots,
            solution_limit=2,
            completion_mode="full",
        )

        self.assertEqual(unique_result.status, "unique")
        self.assertEqual(puzzle.difficulty, _grade(blind_result))
        self.assertEqual(result.stats.backtracks, blind_result.stats.backtracks)
        self.assertIn("verification_solver_backtracks", puzzle.metrics)


if __name__ == "__main__":
    unittest.main()
