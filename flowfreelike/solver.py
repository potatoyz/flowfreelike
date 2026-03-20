from __future__ import annotations

from collections import deque
from dataclasses import replace

from flowfreelike.models import Dot, Point, SolveResult, SolverStats


class FlowSolver:
    def __init__(
        self,
        size: int,
        dots: list[Dot],
        solution_limit: int = 2,
        path_cap: int = 256,
    ) -> None:
        self.size = size
        self.dots = dots
        self.solution_limit = solution_limit
        self.path_cap = path_cap
        self.all_cells = {(x, y) for y in range(size) for x in range(size)}
        self.stats = SolverStats()
        self.solutions: list[dict[int, tuple[Point, ...]]] = []
        self.hit_search_limit = False

    def solve(self) -> SolveResult:
        if not self._state_viable(tuple(self.dots), set()):
            return SolveResult(
                status="invalid",
                solution_count=0,
                stats=self.stats,
                solutions=[],
            )

        self._search(tuple(self.dots), set(), {})

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
        remaining: tuple[Dot, ...],
        occupied: set[Point],
        chosen_paths: dict[int, tuple[Point, ...]],
    ) -> None:
        if self.hit_search_limit or len(self.solutions) >= self.solution_limit:
            return

        self.stats.search_nodes += 1
        depth = len(chosen_paths)
        self.stats.max_depth = max(self.stats.max_depth, depth)

        if not remaining:
            if len(occupied) == len(self.all_cells):
                self.solutions.append(dict(sorted(chosen_paths.items())))
            else:
                self.stats.backtracks += 1
            return

        choice = self._select_next_dot(remaining, occupied)
        if choice is None:
            self.stats.backtracks += 1
            return

        dot, paths = choice
        if len(paths) == 1:
            self.stats.forced_moves += 1
        else:
            self.stats.branch_points += 1

        next_remaining = tuple(other for other in remaining if other.color_id != dot.color_id)

        for path in paths:
            if self.hit_search_limit or len(self.solutions) >= self.solution_limit:
                return

            self.stats.candidate_paths_considered += 1
            next_occupied = occupied | set(path)
            if not self._state_viable(next_remaining, next_occupied):
                self.stats.backtracks += 1
                continue

            next_paths = dict(chosen_paths)
            next_paths[dot.color_id] = path
            before = len(self.solutions)
            self._search(next_remaining, next_occupied, next_paths)
            if len(self.solutions) == before:
                self.stats.backtracks += 1

    def _select_next_dot(
        self,
        remaining: tuple[Dot, ...],
        occupied: set[Point],
    ) -> tuple[Dot, list[tuple[Point, ...]]] | None:
        best_dot: Dot | None = None
        best_paths: list[tuple[Point, ...]] | None = None

        for dot in remaining:
            paths, overflow = self._enumerate_paths(dot, remaining, occupied)
            if overflow:
                self.hit_search_limit = True
                return None
            if not paths:
                return None
            if best_paths is None or len(paths) < len(best_paths):
                best_dot = dot
                best_paths = paths

        if best_dot is None or best_paths is None:
            return None
        return best_dot, best_paths

    def _enumerate_paths(
        self,
        dot: Dot,
        remaining: tuple[Dot, ...],
        occupied: set[Point],
    ) -> tuple[list[tuple[Point, ...]], bool]:
        start, end = dot.p1, dot.p2
        reserved = {
            point
            for other in remaining
            if other.color_id != dot.color_id
            for point in (other.p1, other.p2)
        }
        blocked = occupied | reserved
        allowed = self.all_cells - blocked

        if start not in allowed or end not in allowed:
            return [], False

        paths: list[tuple[Point, ...]] = []
        visited = {start}
        path = [start]

        def dfs(current: Point) -> bool:
            if len(paths) >= self.path_cap:
                return True

            if current == end:
                paths.append(tuple(path))
                return False

            neighbors = []
            for neighbor in self._neighbors(current):
                if neighbor == end:
                    neighbors.append(neighbor)
                elif neighbor in allowed and neighbor not in visited:
                    neighbors.append(neighbor)

            neighbors.sort(key=lambda point: self._manhattan(point, end))

            for neighbor in neighbors:
                if neighbor != end:
                    visited.add(neighbor)
                    path.append(neighbor)
                    if self._can_reach_end(neighbor, end, allowed, visited):
                        if dfs(neighbor):
                            return True
                    path.pop()
                    visited.remove(neighbor)
                else:
                    path.append(neighbor)
                    if dfs(neighbor):
                        return True
                    path.pop()
            return False

        overflow = dfs(start)
        return paths, overflow

    def _can_reach_end(
        self,
        current: Point,
        end: Point,
        allowed: set[Point],
        visited: set[Point],
    ) -> bool:
        queue = deque([current])
        seen = {current}

        while queue:
            cell = queue.popleft()
            if cell == end:
                return True
            for neighbor in self._neighbors(cell):
                if neighbor in seen:
                    continue
                if neighbor == end or (neighbor in allowed and neighbor not in visited):
                    seen.add(neighbor)
                    queue.append(neighbor)
        return False

    def _state_viable(self, remaining: tuple[Dot, ...], occupied: set[Point]) -> bool:
        free_cells = self.all_cells - occupied
        endpoint_to_color = {dot.p1: dot.color_id for dot in remaining}
        endpoint_to_color.update({dot.p2: dot.color_id for dot in remaining})

        if any(dot.p1 not in free_cells or dot.p2 not in free_cells for dot in remaining):
            return False

        for cell in free_cells:
            degree = sum(1 for neighbor in self._neighbors(cell) if neighbor in free_cells)
            if cell in endpoint_to_color:
                if degree < 1:
                    return False
            else:
                if degree < 2:
                    return False

        component_index: dict[Point, int] = {}
        index = 0
        for cell in free_cells:
            if cell in component_index:
                continue

            queue = deque([cell])
            component_index[cell] = index
            found_endpoint = False

            while queue:
                current = queue.popleft()
                if current in endpoint_to_color:
                    found_endpoint = True
                for neighbor in self._neighbors(current):
                    if neighbor in free_cells and neighbor not in component_index:
                        component_index[neighbor] = index
                        queue.append(neighbor)

            if not found_endpoint:
                return False

            index += 1

        for dot in remaining:
            if component_index[dot.p1] != component_index[dot.p2]:
                return False

        return True

    def _neighbors(self, point: Point) -> list[Point]:
        x, y = point
        neighbors: list[Point] = []
        if x > 0:
            neighbors.append((x - 1, y))
        if x + 1 < self.size:
            neighbors.append((x + 1, y))
        if y > 0:
            neighbors.append((x, y - 1))
        if y + 1 < self.size:
            neighbors.append((x, y + 1))
        return neighbors

    @staticmethod
    def _manhattan(a: Point, b: Point) -> int:
        return abs(a[0] - b[0]) + abs(a[1] - b[1])


def solve_puzzle(
    size: int,
    dots: list[Dot],
    solution_limit: int = 2,
    path_cap: int = 256,
) -> SolveResult:
    solver = FlowSolver(size=size, dots=dots, solution_limit=solution_limit, path_cap=path_cap)
    return solver.solve()
