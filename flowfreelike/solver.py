from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Literal

from ortools.sat.python import cp_model

from flowfreelike.models import Dot, Point, SolveResult, SolverStats

CompletionMode = Literal["full", "partial", "any"]


@dataclass(frozen=True, slots=True)
class _DotState:
    color_id: int
    start_idx: int
    end_idx: int


class FlowSolver:
    def __init__(
        self,
        size: int,
        dots: list[Dot],
        solution_limit: int = 2,
        path_cap: int = 256,
        completion_mode: CompletionMode = "full",
        known_solution: dict[int, tuple[Point, ...]] | None = None,
        exclude_known_solution: bool = False,
    ) -> None:
        self.size = size
        self.cell_count = size * size
        self.solution_limit = solution_limit
        self.path_cap = path_cap
        self.completion_mode = completion_mode
        self.require_full_coverage = completion_mode == "full"

        self.index_to_point = tuple((x, y) for y in range(size) for x in range(size))
        self.point_to_index = {
            point: index
            for index, point in enumerate(self.index_to_point)
        }
        self.neighbor_indices = tuple(
            tuple(self._build_neighbors(index))
            for index in range(self.cell_count)
        )

        self.dot_states = tuple(
            _DotState(
                color_id=dot.color_id,
                start_idx=self.point_to_index[dot.p1],
                end_idx=self.point_to_index[dot.p2],
            )
            for dot in dots
        )
        self.color_ids = tuple(dot.color_id for dot in dots)
        self.terminals_by_color = {
            dot.color_id: (self.point_to_index[dot.p1], self.point_to_index[dot.p2])
            for dot in dots
        }
        self.fixed_terminals = {
            self.point_to_index[point]: color_id
            for color_id, (start_idx, end_idx) in self.terminals_by_color.items()
            for point in (self.index_to_point[start_idx], self.index_to_point[end_idx])
        }

        self.stats = SolverStats()
        self.solutions: list[dict[int, tuple[Point, ...]]] = []
        self.hit_search_limit = False

        self.model = cp_model.CpModel()
        self.cell_color: dict[tuple[int, int], cp_model.IntVar] = {}
        self.unused_cells: dict[int, cp_model.IntVar] = {}
        self.arcs: dict[tuple[int, int, int], cp_model.IntVar] = {}
        self.flow: dict[tuple[int, int, int], cp_model.IntVar] = {}
        self.directed_edges = tuple(
            (cell_idx, neighbor_idx)
            for cell_idx in range(self.cell_count)
            for neighbor_idx in self.neighbor_indices[cell_idx]
        )

        self.known_solution_paths = self._normalize_known_solution(known_solution or {})
        self.exclude_known_solution = exclude_known_solution and bool(self.known_solution_paths)

        self._build_model()

        if self.exclude_known_solution:
            known_assignment = self._assignment_from_solution(self.known_solution_paths)
            if known_assignment is not None:
                self._add_no_good_cut(known_assignment)

    def solve(self) -> SolveResult:
        no_more_solutions = False

        for _ in range(self.solution_limit):
            solver = cp_model.CpSolver()
            self._configure_solver(solver)
            status = solver.Solve(self.model)
            self._accumulate_stats(solver)

            if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
                assignment = self._extract_assignment(solver)
                solution = self._extract_solution(solver)
                if solution is None:
                    self.hit_search_limit = True
                    break

                self.solutions.append(solution)
                self._add_no_good_cut(assignment)
                continue

            if status == cp_model.UNKNOWN:
                self.hit_search_limit = True
                break

            no_more_solutions = True
            break

        if self.hit_search_limit:
            status = "search_limit"
        elif len(self.solutions) == 0:
            status = "unsolved"
        elif self.solution_limit == 1:
            status = "unique"
        elif no_more_solutions and len(self.solutions) == 1:
            status = "unique"
        else:
            status = "multiple"

        return SolveResult(
            status=status,
            solution_count=len(self.solutions),
            stats=replace(self.stats),
            solutions=self.solutions[: self.solution_limit],
        )

    def _build_model(self) -> None:
        for cell_idx in range(self.cell_count):
            color_vars = []
            for color_id in self.color_ids:
                var = self.model.NewBoolVar(f"cell_{cell_idx}_color_{color_id}")
                self.cell_color[cell_idx, color_id] = var
                color_vars.append(var)

            if self.require_full_coverage:
                self.model.Add(sum(color_vars) == 1)
            else:
                unused = self.model.NewBoolVar(f"cell_{cell_idx}_unused")
                self.unused_cells[cell_idx] = unused
                self.model.Add(sum(color_vars) + unused == 1)

        if self.completion_mode == "partial":
            self.model.Add(sum(self.unused_cells.values()) >= 1)

        for cell_idx, fixed_color_id in self.fixed_terminals.items():
            for color_id in self.color_ids:
                self.model.Add(self.cell_color[cell_idx, color_id] == int(color_id == fixed_color_id))
            if not self.require_full_coverage:
                self.model.Add(self.unused_cells[cell_idx] == 0)

        for color_id, (start_idx, end_idx) in self.terminals_by_color.items():
            for cell_idx, neighbor_idx in self.directed_edges:
                arc = self.model.NewBoolVar(f"arc_{cell_idx}_{neighbor_idx}_{color_id}")
                self.arcs[cell_idx, neighbor_idx, color_id] = arc
                flow = self.model.NewIntVar(0, self.cell_count - 1, f"flow_{cell_idx}_{neighbor_idx}_{color_id}")
                self.flow[cell_idx, neighbor_idx, color_id] = flow
                self.model.Add(flow <= (self.cell_count - 1) * arc)

            for cell_idx in range(self.cell_count):
                incoming = self._incoming_arc_vars(cell_idx, color_id)
                outgoing = self._outgoing_arc_vars(cell_idx, color_id)

                if cell_idx == start_idx:
                    self.model.Add(sum(incoming) == 0)
                    self.model.Add(sum(outgoing) == 1)
                elif cell_idx == end_idx:
                    self.model.Add(sum(incoming) == 1)
                    self.model.Add(sum(outgoing) == 0)
                else:
                    color_var = self.cell_color[cell_idx, color_id]
                    self.model.Add(sum(incoming) == color_var)
                    self.model.Add(sum(outgoing) == color_var)

            total_color_cells = sum(
                self.cell_color[cell_idx, color_id]
                for cell_idx in range(self.cell_count)
            )
            start_in_flow = self._incoming_flow_vars(start_idx, color_id)
            start_out_flow = self._outgoing_flow_vars(start_idx, color_id)
            self.model.Add(sum(start_out_flow) - sum(start_in_flow) == total_color_cells - 1)

            for cell_idx in range(self.cell_count):
                if cell_idx == start_idx:
                    continue
                incoming_flow = self._incoming_flow_vars(cell_idx, color_id)
                outgoing_flow = self._outgoing_flow_vars(cell_idx, color_id)
                self.model.Add(sum(incoming_flow) - sum(outgoing_flow) == self.cell_color[cell_idx, color_id])

            for cell_idx in range(self.cell_count):
                for neighbor_idx in self.neighbor_indices[cell_idx]:
                    if cell_idx >= neighbor_idx:
                        continue
                    forward = self.arcs[cell_idx, neighbor_idx, color_id]
                    backward = self.arcs[neighbor_idx, cell_idx, color_id]
                    self.model.Add(forward + backward <= 1)
                    self.model.Add(forward + backward <= self.cell_color[cell_idx, color_id])
                    self.model.Add(forward + backward <= self.cell_color[neighbor_idx, color_id])

    def _configure_solver(self, solver: cp_model.CpSolver) -> None:
        solver.parameters.num_search_workers = min(8, max(1, os.cpu_count() or 1))
        solver.parameters.random_seed = 0
        solver.parameters.max_time_in_seconds = self._time_limit_seconds()

    def _time_limit_seconds(self) -> float:
        size_factor = max(1.0, self.size / 6)
        cap_factor = max(1.0, self.path_cap / 256)
        mode_factor = 0.85 if self.completion_mode == "partial" else 1.0
        if self.completion_mode == "any":
            mode_factor = 0.75
        if self.exclude_known_solution:
            mode_factor *= 1.25
        return min(20.0, max(1.5, 2.0 * size_factor * cap_factor * mode_factor))

    def _accumulate_stats(self, solver: cp_model.CpSolver) -> None:
        branches = solver.NumBranches()
        conflicts = solver.NumConflicts()
        branch_points = min(branches, max(1, self.cell_count // 2))
        forced_moves = max(0, self.cell_count - branch_points)

        self.stats.search_nodes += branches + conflicts
        self.stats.backtracks += conflicts
        self.stats.branch_points += branch_points
        self.stats.forced_moves += forced_moves
        self.stats.candidate_paths_considered += branches
        self.stats.max_depth = max(self.stats.max_depth, self.cell_count)

    def _extract_assignment(self, solver: cp_model.CpSolver) -> dict[int, int | None]:
        assignment: dict[int, int | None] = {}
        for cell_idx in range(self.cell_count):
            assigned_color = None
            for color_id in self.color_ids:
                if solver.Value(self.cell_color[cell_idx, color_id]):
                    assigned_color = color_id
                    break
            assignment[cell_idx] = assigned_color
        return assignment

    def _extract_solution(self, solver: cp_model.CpSolver) -> dict[int, tuple[Point, ...]] | None:
        solution: dict[int, tuple[Point, ...]] = {}

        for color_id, (start_idx, end_idx) in self.terminals_by_color.items():
            path = [start_idx]
            current_idx = start_idx
            seen = {start_idx}

            while current_idx != end_idx:
                next_cells = [
                    neighbor_idx
                    for neighbor_idx in self.neighbor_indices[current_idx]
                    if solver.Value(self.arcs[current_idx, neighbor_idx, color_id])
                ]
                if len(next_cells) != 1:
                    return None

                next_idx = next_cells[0]
                if next_idx in seen:
                    return None
                seen.add(next_idx)
                path.append(next_idx)
                current_idx = next_idx

            solution[color_id] = tuple(self.index_to_point[index] for index in path)

        return solution

    def _add_no_good_cut(self, assignment: dict[int, int | None]) -> None:
        matching_literals = []

        for cell_idx, color_id in assignment.items():
            if color_id is None:
                if self.require_full_coverage:
                    return
                matching_literals.append(self.unused_cells[cell_idx])
                continue
            matching_literals.append(self.cell_color[cell_idx, color_id])

        if matching_literals:
            self.model.Add(sum(matching_literals) <= len(matching_literals) - 1)

    def _assignment_from_solution(
        self,
        solution: dict[int, tuple[int, ...]],
    ) -> dict[int, int | None] | None:
        assignment: dict[int, int | None] = {cell_idx: None for cell_idx in range(self.cell_count)}

        for color_id, path in solution.items():
            for cell_idx in path:
                current = assignment.get(cell_idx)
                if current is not None and current != color_id:
                    return None
                assignment[cell_idx] = color_id

        if self.require_full_coverage and any(color_id is None for color_id in assignment.values()):
            return None
        return assignment

    def _normalize_known_solution(
        self,
        known_solution: dict[int, tuple[Point, ...]],
    ) -> dict[int, tuple[int, ...]]:
        normalized: dict[int, tuple[int, ...]] = {}
        for color_id, points in known_solution.items():
            normalized[color_id] = tuple(self.point_to_index[point] for point in points)
        return normalized

    def _incoming_arc_vars(self, cell_idx: int, color_id: int) -> list[cp_model.IntVar]:
        return [
            self.arcs[neighbor_idx, cell_idx, color_id]
            for neighbor_idx in self.neighbor_indices[cell_idx]
        ]

    def _outgoing_arc_vars(self, cell_idx: int, color_id: int) -> list[cp_model.IntVar]:
        return [
            self.arcs[cell_idx, neighbor_idx, color_id]
            for neighbor_idx in self.neighbor_indices[cell_idx]
        ]

    def _incoming_flow_vars(self, cell_idx: int, color_id: int) -> list[cp_model.IntVar]:
        return [
            self.flow[neighbor_idx, cell_idx, color_id]
            for neighbor_idx in self.neighbor_indices[cell_idx]
        ]

    def _outgoing_flow_vars(self, cell_idx: int, color_id: int) -> list[cp_model.IntVar]:
        return [
            self.flow[cell_idx, neighbor_idx, color_id]
            for neighbor_idx in self.neighbor_indices[cell_idx]
        ]

    def _build_neighbors(self, index: int) -> list[int]:
        x, y = self.index_to_point[index]
        neighbors: list[int] = []
        if x > 0:
            neighbors.append(index - 1)
        if x + 1 < self.size:
            neighbors.append(index + 1)
        if y > 0:
            neighbors.append(index - self.size)
        if y + 1 < self.size:
            neighbors.append(index + self.size)
        return neighbors


def solve_puzzle(
    size: int,
    dots: list[Dot],
    solution_limit: int = 2,
    path_cap: int = 256,
    completion_mode: CompletionMode = "full",
    known_solution: dict[int, tuple[Point, ...]] | None = None,
    exclude_known_solution: bool = False,
) -> SolveResult:
    solver = FlowSolver(
        size=size,
        dots=dots,
        solution_limit=solution_limit,
        path_cap=path_cap,
        completion_mode=completion_mode,
        known_solution=known_solution,
        exclude_known_solution=exclude_known_solution,
    )
    return solver.solve()
