from __future__ import annotations

import json
import random
from collections.abc import Iterable
from pathlib import Path

from flowfreelike.models import Dot, Puzzle, SolveResult
from flowfreelike.registry import LevelFingerprint, build_level_index, find_duplicate_level
from flowfreelike.solver import solve_puzzle
from flowfreelike.tube_generator import build_tube_candidate, tube_pair_bounds

MIN_PATH_CELLS = 3
BASE_SOLVER_PATH_CAP = 256
EXTENDED_SOLVER_PATH_CAP = 768


def generate_level(
    size: int,
    seed: int | None = None,
    max_attempts: int = 300,
    target_difficulty: str | None = None,
    existing_level_sources: Iterable[Path] | None = None,
    existing_level_index: dict[LevelFingerprint, Path] | None = None,
) -> tuple[Puzzle, SolveResult]:
    rng = random.Random(seed)
    level_index = existing_level_index
    if level_index is None:
        level_index = build_level_index(existing_level_sources or [])

    min_pairs, max_pairs = tube_pair_bounds(size=size, target_difficulty=target_difficulty)
    duplicate_rejections = 0
    candidate_build_failures = 0
    strict_full_rejections = 0
    partial_solution_rejections = 0
    difficulty_rejections = 0

    for attempt in range(1, max_attempts + 1):
        try:
            candidate = build_tube_candidate(
                size=size,
                rng=rng,
                min_pairs=min_pairs,
                max_pairs=max_pairs,
                max_attempts=1,
            )
        except RuntimeError:
            candidate_build_failures += 1
            continue

        dots = candidate.dots
        verification_result = solve_puzzle(
            size=size,
            dots=dots,
            solution_limit=2,
            completion_mode="full",
            path_cap=EXTENDED_SOLVER_PATH_CAP,
        )
        if verification_result.status != "unique" or not verification_result.solutions:
            strict_full_rejections += 1
            continue

        partial_result = solve_puzzle(
            size=size,
            dots=dots,
            solution_limit=1,
            completion_mode="partial",
            path_cap=EXTENDED_SOLVER_PATH_CAP,
        )
        if partial_result.solution_count > 0:
            partial_solution_rejections += 1
            continue

        blind_result = _solve_blind_for_grading(size=size, dots=dots)
        if blind_result.solutions:
            blind_solution = blind_result.solutions[0]
            result_stats = blind_result.stats
            difficulty = _grade(blind_result)
        else:
            blind_solution = verification_result.solutions[0]
            result_stats = verification_result.stats
            difficulty = _grade(verification_result)

        if target_difficulty and difficulty.lower() != target_difficulty.lower():
            difficulty_rejections += 1
            continue

        duplicate_of = find_duplicate_level(size=size, dots=dots, level_index=level_index)
        if duplicate_of is not None:
            duplicate_rejections += 1
            continue

        min_solution_path = min(len(path_points) for path_points in blind_solution.values())
        coverage_cells = len({point for path_points in blind_solution.values() for point in path_points})
        if min_solution_path < MIN_PATH_CELLS or coverage_cells != size * size:
            strict_full_rejections += 1
            continue

        result = SolveResult(
            status="unique",
            solution_count=1,
            stats=result_stats,
            solutions=[blind_solution],
        )

        puzzle = Puzzle(
            level_id=f"candidate_{attempt:03d}_{size}x{size}_{difficulty.lower()}",
            grid_size=size,
            difficulty=difficulty,
            dots=dots,
            metrics={
                "target_moves": size * size,
                "color_count": len(dots),
                "existing_level_pool": len(level_index),
                "duplicate_rejections": duplicate_rejections,
                "candidate_build_failures": candidate_build_failures,
                "strict_full_rejections": strict_full_rejections,
                "partial_solution_rejections": partial_solution_rejections,
                "difficulty_rejections": difficulty_rejections,
                "verification_mode": "strict_full_uniqueness_search",
                "grading_mode": "blind_first_solution_search",
                "generation_mode": "tube_loop_growth",
                "tube_pair_count": candidate.pair_count,
                "min_solution_path_cells": min_solution_path,
                "solution_coverage_cells": coverage_cells,
                "solution_coverage_ratio": round(coverage_cells / (size * size), 3),
                **result.stats.to_dict(),
                **_prefix_stats("verification_", verification_result.stats.to_dict()),
            },
            solution=blind_solution,
        )
        return puzzle, result

    raise RuntimeError(
        f"Failed to generate a unique non-duplicate {size}x{size} level within {max_attempts} attempts."
    )


def export_level(puzzle: Puzzle, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(puzzle.to_dict(), indent=2), encoding="utf-8")


def _solve_blind_for_grading(size: int, dots: list[Dot]) -> SolveResult:
    blind_result = solve_puzzle(
        size=size,
        dots=dots,
        solution_limit=1,
        completion_mode="full",
        path_cap=BASE_SOLVER_PATH_CAP,
    )
    if blind_result.status != "search_limit":
        return blind_result

    return solve_puzzle(
        size=size,
        dots=dots,
        solution_limit=1,
        completion_mode="full",
        path_cap=EXTENDED_SOLVER_PATH_CAP,
    )


def _prefix_stats(prefix: str, metrics: dict[str, object]) -> dict[str, object]:
    return {
        f"{prefix}{key}": value
        for key, value in metrics.items()
    }


def _grade(result: SolveResult) -> str:
    ratio = result.stats.forced_move_ratio
    score = result.stats.backtracks + result.stats.branch_points * 3 + max(0.0, 0.65 - ratio) * 20

    if score < 12:
        return "Easy"
    if score < 45:
        return "Medium"
    return "Hard"
