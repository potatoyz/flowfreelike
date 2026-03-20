from __future__ import annotations

import json
import random
from collections.abc import Iterable
from pathlib import Path

from flowfreelike.geometry import GRID_SYMMETRIES
from flowfreelike.models import Dot, Puzzle, SolveResult
from flowfreelike.registry import LevelFingerprint, build_level_index, find_duplicate_level
from flowfreelike.solver import solve_puzzle

MIN_PATH_CELLS = 3


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
    duplicate_rejections = 0

    for attempt in range(1, max_attempts + 1):
        path = _generate_covering_path(size, rng)
        try:
            segments = _split_path(path, rng, target_difficulty)
        except ValueError:
            continue

        dots = [
            Dot(color_id=index + 1, p1=segment[0], p2=segment[-1])
            for index, segment in enumerate(segments)
        ]

        duplicate_of = find_duplicate_level(size=size, dots=dots, level_index=level_index)
        if duplicate_of is not None:
            duplicate_rejections += 1
            continue

        result = solve_puzzle(size=size, dots=dots, solution_limit=2)
        if not result.is_unique:
            continue

        solution = result.solutions[0]
        min_solution_path = min(len(path_points) for path_points in solution.values())
        coverage_cells = len({point for path_points in solution.values() for point in path_points})
        if min_solution_path < MIN_PATH_CELLS or coverage_cells != size * size:
            continue

        difficulty = _grade(result)
        if target_difficulty and difficulty.lower() != target_difficulty.lower():
            continue

        level_id = f"candidate_{attempt:03d}_{size}x{size}_{difficulty.lower()}"
        metrics = {
            "target_moves": size * size,
            "color_count": len(dots),
            "existing_level_pool": len(level_index),
            "duplicate_rejections": duplicate_rejections,
            "min_solution_path_cells": min_solution_path,
            "solution_coverage_cells": coverage_cells,
            "solution_coverage_ratio": round(coverage_cells / (size * size), 3),
            **result.stats.to_dict(),
        }
        puzzle = Puzzle(
            level_id=level_id,
            grid_size=size,
            difficulty=difficulty,
            dots=dots,
            metrics=metrics,
        )
        return puzzle, result

    raise RuntimeError(
        f"Failed to generate a unique non-duplicate {size}x{size} level within {max_attempts} attempts."
    )


def export_level(puzzle: Puzzle, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(puzzle.to_dict(), indent=2),
        encoding="utf-8",
    )


def make_level_id(level_number: int, size: int, difficulty: str) -> str:
    return f"lvl_{level_number:04d}_{size}x{size}_{difficulty.lower()}"


def _generate_covering_path(size: int, rng: random.Random) -> list[tuple[int, int]]:
    path = []
    horizontal = rng.choice([True, False])

    if horizontal:
        for y in range(size):
            xs = range(size) if y % 2 == 0 else range(size - 1, -1, -1)
            for x in xs:
                path.append((x, y))
    else:
        for x in range(size):
            ys = range(size) if x % 2 == 0 else range(size - 1, -1, -1)
            for y in ys:
                path.append((x, y))

    transform = rng.choice(GRID_SYMMETRIES)
    return [transform(point, size) for point in path]


def _split_path(
    path: list[tuple[int, int]],
    rng: random.Random,
    target_difficulty: str | None,
) -> list[tuple[tuple[int, int], ...]]:
    total_cells = len(path)
    color_count = _pick_color_count(total_cells, rng, target_difficulty)
    lengths = _random_composition(
        total=total_cells,
        parts=color_count,
        minimum=MIN_PATH_CELLS,
        maximum=_segment_max_len(target_difficulty, total_cells),
        rng=rng,
    )

    segments = []
    cursor = 0
    for length in lengths:
        segment = tuple(path[cursor : cursor + length])
        segments.append(segment)
        cursor += length
    return segments


def _pick_color_count(
    total_cells: int,
    rng: random.Random,
    target_difficulty: str | None,
) -> int:
    size = int(total_cells ** 0.5)
    upper = max(1, min(total_cells // MIN_PATH_CELLS, size + 3))

    if target_difficulty:
        difficulty = target_difficulty.lower()
        if difficulty == "easy":
            lower = max(3, size)
            upper = min(upper, size + 4)
        elif difficulty == "hard":
            lower = max(2, size // 2)
            upper = min(upper, max(lower, size))
        else:
            lower = max(3, size - 1)
    else:
        lower = max(3, size - 1)

    lower = min(lower, upper)
    return rng.randint(lower, upper)


def _segment_max_len(target_difficulty: str | None, total_cells: int) -> int:
    if not target_difficulty:
        return max(MIN_PATH_CELLS + 1, total_cells // 2)

    difficulty = target_difficulty.lower()
    if difficulty == "easy":
        return max(MIN_PATH_CELLS, total_cells // 3)
    if difficulty == "hard":
        return max(MIN_PATH_CELLS + 2, total_cells)
    return max(MIN_PATH_CELLS + 1, total_cells // 2)


def _random_composition(
    total: int,
    parts: int,
    minimum: int,
    maximum: int,
    rng: random.Random,
) -> list[int]:
    lengths = [minimum] * parts
    remaining = total - minimum * parts

    while remaining > 0:
        candidates = [index for index, value in enumerate(lengths) if value < maximum]
        if not candidates:
            raise ValueError("Cannot distribute cells with the requested segment constraints.")
        index = rng.choice(candidates)
        lengths[index] += 1
        remaining -= 1

    rng.shuffle(lengths)
    return lengths


def _grade(result: SolveResult) -> str:
    ratio = result.stats.forced_move_ratio
    score = result.stats.backtracks + result.stats.branch_points * 3 + max(0.0, 0.65 - ratio) * 20

    if score < 12:
        return "Easy"
    if score < 45:
        return "Medium"
    return "Hard"
