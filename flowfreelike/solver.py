from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from typing import Literal

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
        self.all_mask = (1 << self.cell_count) - 1
        self.neighbor_indices = tuple(
            tuple(self._build_neighbors(index))
            for index in range(self.cell_count)
        )
        self.neighbor_masks = tuple(
            self._mask_from_indices(neighbors)
            for neighbors in self.neighbor_indices
        )
        self.distance_matrix = tuple(
            tuple(self._point_manhattan(a, b) for b in self.index_to_point)
            for a in self.index_to_point
        )

        self.dot_states = tuple(
            _DotState(
                color_id=dot.color_id,
                start_idx=self.point_to_index[dot.p1],
                end_idx=self.point_to_index[dot.p2],
            )
            for dot in dots
        )

        self.known_solution_paths = self._normalize_known_solution(known_solution or {})
        self.exclude_known_solution = exclude_known_solution and bool(self.known_solution_paths)

        self.stats = SolverStats()
        self.solutions: list[dict[int, tuple[Point, ...]]] = []
        self.hit_search_limit = False
        self.dead_state_cache: set[tuple[int, tuple[int, ...], bool]] = set()
        self.viability_cache: dict[tuple[int, tuple[int, ...], bool], bool] = {}
        self.path_cache: dict[
            tuple[int, tuple[int, ...], int, int],
            tuple[tuple[tuple[int, ...], ...], bool, bool],
        ] = {}

    def solve(self) -> SolveResult:
        if not self._state_viable(self.dot_states, 0):
            return SolveResult(
                status="invalid",
                solution_count=0,
                stats=self.stats,
                solutions=[],
            )

        self._search(
            self.dot_states,
            0,
            {},
            has_diverged=not self.exclude_known_solution,
        )

        if self.hit_search_limit:
            status = "search_limit"
        elif len(self.solutions) == 0:
            status = "unsolved"
        elif len(self.solutions) == 1:
            status = "unique"
        else:
            status = "multiple"

        return SolveResult(
            status=status,
            solution_count=len(self.solutions),
            stats=replace(self.stats),
            solutions=self.solutions[: self.solution_limit],
        )

    def _search(
        self,
        remaining: tuple[_DotState, ...],
        occupied_mask: int,
        chosen_paths: dict[int, tuple[int, ...]],
        has_diverged: bool,
    ) -> None:
        if self.hit_search_limit or len(self.solutions) >= self.solution_limit:
            return

        state_key = (occupied_mask, self._remaining_ids(remaining), has_diverged)
        if state_key in self.dead_state_cache:
            self.stats.backtracks += 1
            return

        self.stats.search_nodes += 1
        depth = len(chosen_paths)
        self.stats.max_depth = max(self.stats.max_depth, depth)

        if not remaining:
            if self._is_goal_state(occupied_mask) and has_diverged:
                self.solutions.append(self._materialize_solution(chosen_paths))
            else:
                self.stats.backtracks += 1
                self.dead_state_cache.add(state_key)
            return

        choice = self._select_next_dot(remaining, occupied_mask, has_diverged)
        if choice is None:
            self.stats.backtracks += 1
            self.dead_state_cache.add(state_key)
            return

        dot, paths = choice
        ordered_paths = self._ordered_paths(dot.color_id, paths, has_diverged)
        if len(ordered_paths) == 1:
            self.stats.forced_moves += 1
        else:
            self.stats.branch_points += 1

        next_remaining = tuple(other for other in remaining if other.color_id != dot.color_id)
        found_solution = False

        for path in ordered_paths:
            if self.hit_search_limit or len(self.solutions) >= self.solution_limit:
                return

            self.stats.candidate_paths_considered += 1
            next_occupied = occupied_mask | self._mask_from_indices(path)
            if not self._state_viable(next_remaining, next_occupied):
                self.stats.backtracks += 1
                continue

            next_paths = dict(chosen_paths)
            next_paths[dot.color_id] = path
            before = len(self.solutions)
            self._search(
                next_remaining,
                next_occupied,
                next_paths,
                has_diverged or self._path_differs_from_known(dot.color_id, path),
            )
            if len(self.solutions) > before:
                found_solution = True
            else:
                self.stats.backtracks += 1

        if not found_solution:
            self.dead_state_cache.add(state_key)

    def _is_goal_state(self, occupied_mask: int) -> bool:
        occupied_count = occupied_mask.bit_count()
        if self.completion_mode == "full":
            return occupied_count == self.cell_count
        if self.completion_mode == "partial":
            return occupied_count < self.cell_count
        return True

    def _select_next_dot(
        self,
        remaining: tuple[_DotState, ...],
        occupied_mask: int,
        has_diverged: bool,
    ) -> tuple[_DotState, list[tuple[int, ...]]] | None:
        best_dot: _DotState | None = None
        best_paths: list[tuple[int, ...]] | None = None
        best_count = self.path_cap + 1
        best_overflow = False

        candidate_dots = sorted(
            remaining,
            key=lambda dot: self._dot_priority(dot, remaining, occupied_mask),
        )

        for dot in candidate_dots:
            stop_after = 1 if best_count == 1 else min(self.path_cap, best_count)
            paths, truncated, overflow = self._enumerate_paths(
                dot,
                remaining,
                occupied_mask,
                stop_after=stop_after,
            )
            if not paths:
                return None

            candidate_paths = list(paths)
            if not has_diverged:
                candidate_paths = self._ensure_known_path(dot.color_id, candidate_paths)

            comparable_count = stop_after if (truncated or overflow) else len(candidate_paths)
            if best_dot is None or comparable_count < best_count:
                best_dot = dot
                best_paths = candidate_paths
                best_count = comparable_count
                best_overflow = overflow

        if best_dot is None or best_paths is None:
            return None
        if best_overflow and best_count >= self.path_cap:
            self.hit_search_limit = True
            return None
        return best_dot, best_paths

    def _enumerate_paths(
        self,
        dot: _DotState,
        remaining: tuple[_DotState, ...],
        occupied_mask: int,
        stop_after: int,
    ) -> tuple[tuple[tuple[int, ...], ...], bool, bool]:
        cache_key = (
            occupied_mask,
            self._remaining_ids(remaining),
            dot.color_id,
            stop_after,
        )
        cached = self.path_cache.get(cache_key)
        if cached is not None:
            return cached

        reserved_mask = 0
        for other in remaining:
            if other.color_id == dot.color_id:
                continue
            reserved_mask |= (1 << other.start_idx) | (1 << other.end_idx)

        blocked_mask = occupied_mask | reserved_mask
        allowed_mask = self.all_mask & ~blocked_mask
        start_bit = 1 << dot.start_idx
        end_bit = 1 << dot.end_idx
        if (allowed_mask & start_bit) == 0 or (allowed_mask & end_bit) == 0:
            result = (tuple(), False, False)
            self.path_cache[cache_key] = result
            return result

        paths: list[tuple[int, ...]] = []
        path = [dot.start_idx]
        truncated = False
        overflow = False

        def dfs(current_idx: int, visited_mask: int) -> bool:
            nonlocal truncated, overflow

            if current_idx == dot.end_idx:
                paths.append(tuple(path))
                if len(paths) >= self.path_cap:
                    overflow = True
                    return True
                if len(paths) >= stop_after:
                    truncated = True
                    return True
                return False

            neighbors = []
            for neighbor in self.neighbor_indices[current_idx]:
                neighbor_bit = 1 << neighbor
                if neighbor == dot.end_idx:
                    neighbors.append(neighbor)
                elif (allowed_mask & neighbor_bit) and not (visited_mask & neighbor_bit):
                    neighbors.append(neighbor)

            neighbors.sort(key=lambda index: self.distance_matrix[index][dot.end_idx])

            for neighbor in neighbors:
                neighbor_bit = 1 << neighbor
                path.append(neighbor)
                if neighbor == dot.end_idx:
                    if dfs(neighbor, visited_mask):
                        return True
                else:
                    next_visited = visited_mask | neighbor_bit
                    if self._can_reach_end(neighbor, dot.end_idx, allowed_mask, next_visited):
                        if dfs(neighbor, next_visited):
                            return True
                path.pop()
            return False

        dfs(dot.start_idx, start_bit)
        result = (tuple(paths), truncated, overflow)
        self.path_cache[cache_key] = result
        return result

    def _can_reach_end(
        self,
        current_idx: int,
        end_idx: int,
        allowed_mask: int,
        visited_mask: int,
    ) -> bool:
        queue = deque([current_idx])
        seen_mask = 1 << current_idx

        while queue:
            cell_idx = queue.popleft()
            if cell_idx == end_idx:
                return True
            for neighbor in self.neighbor_indices[cell_idx]:
                neighbor_bit = 1 << neighbor
                if seen_mask & neighbor_bit:
                    continue
                if neighbor == end_idx or ((allowed_mask & neighbor_bit) and not (visited_mask & neighbor_bit)):
                    seen_mask |= neighbor_bit
                    queue.append(neighbor)
        return False

    def _state_viable(self, remaining: tuple[_DotState, ...], occupied_mask: int) -> bool:
        key = (occupied_mask, self._remaining_ids(remaining), self.require_full_coverage)
        cached = self.viability_cache.get(key)
        if cached is not None:
            return cached

        free_mask = self.all_mask & ~occupied_mask
        endpoint_mask = 0
        min_required_cells = 0

        for dot in remaining:
            start_bit = 1 << dot.start_idx
            end_bit = 1 << dot.end_idx
            if (free_mask & start_bit) == 0 or (free_mask & end_bit) == 0:
                self.viability_cache[key] = False
                return False
            endpoint_mask |= start_bit | end_bit
            min_required_cells += self.distance_matrix[dot.start_idx][dot.end_idx] + 1

        if min_required_cells > free_mask.bit_count():
            self.viability_cache[key] = False
            return False

        if self.require_full_coverage:
            for cell_idx in self._iter_bits(free_mask):
                degree = (self.neighbor_masks[cell_idx] & free_mask).bit_count()
                if endpoint_mask & (1 << cell_idx):
                    if degree < 1:
                        self.viability_cache[key] = False
                        return False
                elif degree < 2:
                    self.viability_cache[key] = False
                    return False

        endpoint_component: dict[int, int] = {}
        component_id = 0
        unvisited_mask = free_mask

        while unvisited_mask:
            start_bit = unvisited_mask & -unvisited_mask
            start_idx = start_bit.bit_length() - 1
            queue = deque([start_idx])
            unvisited_mask ^= start_bit
            has_endpoint = bool(endpoint_mask & start_bit)
            if has_endpoint:
                endpoint_component[start_idx] = component_id

            while queue:
                current_idx = queue.popleft()
                neighbor_mask = self.neighbor_masks[current_idx] & unvisited_mask
                while neighbor_mask:
                    next_bit = neighbor_mask & -neighbor_mask
                    neighbor_idx = next_bit.bit_length() - 1
                    neighbor_mask ^= next_bit
                    unvisited_mask ^= next_bit
                    queue.append(neighbor_idx)
                    if endpoint_mask & next_bit:
                        has_endpoint = True
                        endpoint_component[neighbor_idx] = component_id

            if self.require_full_coverage and not has_endpoint:
                self.viability_cache[key] = False
                return False
            component_id += 1

        for dot in remaining:
            if endpoint_component.get(dot.start_idx) != endpoint_component.get(dot.end_idx):
                self.viability_cache[key] = False
                return False

        self.viability_cache[key] = True
        return True

    def _dot_priority(
        self,
        dot: _DotState,
        remaining: tuple[_DotState, ...],
        occupied_mask: int,
    ) -> tuple[int, int, int, int]:
        reserved_mask = 0
        for other in remaining:
            if other.color_id == dot.color_id:
                continue
            reserved_mask |= (1 << other.start_idx) | (1 << other.end_idx)

        free_mask = self.all_mask & ~(occupied_mask | reserved_mask)
        start_degree = (self.neighbor_masks[dot.start_idx] & free_mask).bit_count()
        end_degree = (self.neighbor_masks[dot.end_idx] & free_mask).bit_count()
        start_point = self.index_to_point[dot.start_idx]
        end_point = self.index_to_point[dot.end_idx]
        box_area = (abs(start_point[0] - end_point[0]) + 1) * (abs(start_point[1] - end_point[1]) + 1)
        return (
            start_degree + end_degree,
            box_area,
            self.distance_matrix[dot.start_idx][dot.end_idx],
            dot.color_id,
        )

    def _ordered_paths(
        self,
        color_id: int,
        paths: list[tuple[int, ...]],
        has_diverged: bool,
    ) -> list[tuple[int, ...]]:
        if has_diverged:
            return paths
        known_path = self.known_solution_paths.get(color_id)
        if known_path is None:
            return paths
        return sorted(paths, key=lambda path: path == known_path)

    def _ensure_known_path(
        self,
        color_id: int,
        paths: list[tuple[int, ...]],
    ) -> list[tuple[int, ...]]:
        known_path = self.known_solution_paths.get(color_id)
        if known_path is None or known_path in paths:
            return paths
        paths.append(known_path)
        return paths

    def _path_differs_from_known(
        self,
        color_id: int,
        path: tuple[int, ...],
    ) -> bool:
        known_path = self.known_solution_paths.get(color_id)
        if known_path is None:
            return True
        return path != known_path

    def _normalize_known_solution(
        self,
        known_solution: dict[int, tuple[Point, ...]],
    ) -> dict[int, tuple[int, ...]]:
        normalized: dict[int, tuple[int, ...]] = {}
        for color_id, points in known_solution.items():
            normalized[color_id] = tuple(self.point_to_index[point] for point in points)
        return normalized

    def _materialize_solution(
        self,
        chosen_paths: dict[int, tuple[int, ...]],
    ) -> dict[int, tuple[Point, ...]]:
        return {
            color_id: tuple(self.index_to_point[index] for index in path)
            for color_id, path in sorted(chosen_paths.items())
        }

    def _remaining_ids(self, remaining: tuple[_DotState, ...]) -> tuple[int, ...]:
        return tuple(dot.color_id for dot in remaining)

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

    def _mask_from_indices(self, indices: tuple[int, ...] | list[int]) -> int:
        mask = 0
        for index in indices:
            mask |= 1 << index
        return mask

    def _iter_bits(self, mask: int):
        while mask:
            bit = mask & -mask
            yield bit.bit_length() - 1
            mask ^= bit

    @staticmethod
    def _point_manhattan(a: Point, b: Point) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])


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
