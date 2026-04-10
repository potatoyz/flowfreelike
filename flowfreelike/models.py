from __future__ import annotations

from dataclasses import dataclass
from typing import Any, TypeAlias

Point = tuple[int, int]
Solution: TypeAlias = dict[int, tuple[Point, ...]]


@dataclass(frozen=True, slots=True)
class Dot:
    color_id: int
    p1: Point
    p2: Point

    def to_dict(self) -> dict[str, Any]:
        return {
            "color_id": self.color_id,
            "p1": [self.p1[0], self.p1[1]],
            "p2": [self.p2[0], self.p2[1]],
        }


def solution_to_dict(solution: Solution) -> list[dict[str, Any]]:
    return [
        {
            "color_id": color_id,
            "path": [[point[0], point[1]] for point in path],
        }
        for color_id, path in sorted(solution.items())
    ]


def solution_from_dict(value: object) -> Solution | None:
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError("solution must be a list of color paths.")

    solution: Solution = {}
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("solution entries must be objects.")
        if "color_id" not in item or "path" not in item:
            raise ValueError("solution entries must contain color_id and path.")

        color_id = int(item["color_id"])
        raw_path = item["path"]
        if not isinstance(raw_path, list | tuple) or len(raw_path) < 2:
            raise ValueError("solution paths must contain at least two points.")
        if color_id in solution:
            raise ValueError(f"solution duplicates color_id {color_id}.")

        solution[color_id] = tuple(_point_from_value(point) for point in raw_path)

    return solution


def _point_from_value(value: object) -> Point:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("solution points must be two-item lists or tuples.")
    return int(value[0]), int(value[1])


@dataclass(slots=True)
class SolverStats:
    search_nodes: int = 0
    backtracks: int = 0
    forced_moves: int = 0
    branch_points: int = 0
    candidate_paths_considered: int = 0
    max_depth: int = 0

    @property
    def forced_move_ratio(self) -> float:
        decisions = self.forced_moves + self.branch_points
        if decisions == 0:
            return 1.0
        return self.forced_moves / decisions

    def to_dict(self) -> dict[str, Any]:
        return {
            "solver_search_nodes": self.search_nodes,
            "solver_backtracks": self.backtracks,
            "solver_forced_moves": self.forced_moves,
            "solver_branch_points": self.branch_points,
            "solver_candidate_paths": self.candidate_paths_considered,
            "solver_max_depth": self.max_depth,
            "forced_move_ratio": round(self.forced_move_ratio, 3),
        }


@dataclass(slots=True)
class SolveResult:
    status: str
    solution_count: int
    stats: SolverStats
    solutions: list[dict[int, tuple[Point, ...]]]

    @property
    def is_unique(self) -> bool:
        return self.status == "unique" and self.solution_count == 1


@dataclass(slots=True)
class Puzzle:
    level_id: str
    grid_size: int
    difficulty: str
    dots: list[Dot]
    metrics: dict[str, Any]
    solution: Solution | None = None

    def to_dict(self) -> dict[str, Any]:
        data = {
            "level_id": self.level_id,
            "grid_size": self.grid_size,
            "difficulty": self.difficulty,
            "dots": [dot.to_dict() for dot in self.dots],
            "metrics": self.metrics,
        }
        if self.solution is not None:
            data["solution"] = solution_to_dict(self.solution)
        return data
