from __future__ import annotations

from dataclasses import dataclass
from typing import Any

Point = tuple[int, int]


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "level_id": self.level_id,
            "grid_size": self.grid_size,
            "difficulty": self.difficulty,
            "dots": [dot.to_dict() for dot in self.dots],
            "metrics": self.metrics,
        }
