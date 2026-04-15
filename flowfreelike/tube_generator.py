from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from flowfreelike.models import Dot

if TYPE_CHECKING:
    import random

TURN_STRAIGHT = 0
TURN_LEFT = 1
TURN_RIGHT = 2


@dataclass(frozen=True, slots=True)
class TubeCandidate:
    dots: list[Dot]
    pair_count: int


class _UnionFind:
    def __init__(self) -> None:
        self._parents: dict[tuple[int, int], tuple[int, int]] = {}

    def find(self, key: tuple[int, int]) -> tuple[int, int]:
        parent = self._parents.get(key, key)
        if parent == key:
            return key
        root = self.find(parent)
        self._parents[key] = root
        return root

    def union(self, a: tuple[int, int], b: tuple[int, int]) -> None:
        self._parents[self.find(a)] = self.find(b)


@dataclass(frozen=True, slots=True)
class _TurnPath:
    steps: tuple[int, ...]

    def points(self, dx: int = 0, dy: int = 1) -> Iterator[tuple[int, int]]:
        x = 0
        y = 0
        yield (x, y)
        for step in self.steps:
            x += dx
            y += dy
            yield (x, y)
            if step == TURN_LEFT:
                dx, dy = -dy, dx
            elif step == TURN_RIGHT:
                dx, dy = dy, -dx
            else:
                x += dx
                y += dy
                yield (x, y)

    def is_simple(self) -> bool:
        points = tuple(self.points())
        return len(points) == len(set(points))

    def is_loop(self) -> bool:
        points = tuple(self.points())
        return (
            len(points) == len(set(points))
            or (len(points) == len(set(points)) + 1 and points[0] == points[-1])
        )

    def winding(self) -> int:
        return self.steps.count(TURN_RIGHT) - self.steps.count(TURN_LEFT)


def _unrotate(x: int, y: int, dx: int, dy: int) -> tuple[int, int]:
    while (dx, dy) != (0, 1):
        x, y, dx, dy = -y, x, -dy, dx
    return x, y


class _PathSampler:
    def __init__(self, rng: random.Random, turn_price: int = 2, straight_price: int = 1) -> None:
        self._rng = rng
        self._turn_price = turn_price
        self._straight_price = straight_price
        self._lookup: dict[tuple[int, int, int, int], list[tuple[int, ...]]] = defaultdict(list)
        self._prefixes: list[tuple[tuple[int, ...], int, int, int, int]] = []

    def prepare(self, budget: int) -> None:
        if self._prefixes:
            return
        for steps, state in self._enumerate_good_paths(0, 0, 0, 1, budget, seen=set()):
            x, y, dx, dy = state
            self._prefixes.append((steps, x, y, dx, dy))
            self._lookup[x, y, dx, dy].append(steps)

    def random_path(self, target_x: int, target_y: int, target_dx: int, target_dy: int) -> _TurnPath:
        while True:
            prefix, x, y, dx, dy = self._rng.choice(self._prefixes)
            suffixes = self._lookup_paths(dx, dy, target_x - x, target_y - y, target_dx, target_dy)
            if not suffixes:
                continue
            path = _TurnPath(prefix + self._rng.choice(suffixes))
            if path.is_simple():
                return path

    def random_path_with_walk(
        self,
        target_x: int,
        target_y: int,
        target_dx: int,
        target_dy: int,
    ) -> _TurnPath:
        seen: set[tuple[int, int]] = set()
        steps: list[int] = []

        while True:
            seen.clear()
            steps.clear()
            x = 0
            y = 0
            dx = 0
            dy = 1
            seen.add((x, y))

            budget = 2 * (abs(target_x) + abs(target_y))
            for _ in range(budget):
                step = self._rng.choices(
                    [TURN_LEFT, TURN_RIGHT, TURN_STRAIGHT],
                    [
                        1 / self._turn_price,
                        1 / self._turn_price,
                        2 / self._straight_price,
                    ],
                    k=1,
                )[0]
                steps.append(step)
                x += dx
                y += dy
                if (x, y) in seen:
                    break
                seen.add((x, y))

                if step == TURN_LEFT:
                    dx, dy = -dy, dx
                elif step == TURN_RIGHT:
                    dx, dy = dy, -dx
                else:
                    x += dx
                    y += dy
                    if (x, y) in seen:
                        break
                    seen.add((x, y))

                if (x, y) == (target_x, target_y):
                    return _TurnPath(tuple(steps))

                suffixes = self._lookup_paths(dx, dy, target_x - x, target_y - y, target_dx, target_dy)
                if suffixes:
                    return _TurnPath(tuple(steps) + self._rng.choice(suffixes))

    def random_loop(self, clockwise: int = 0) -> _TurnPath:
        while True:
            prefix, x, y, dx, dy = self._rng.choice(self._prefixes)
            suffixes = self._lookup_paths(dx, dy, -x, -y, 0, 1)
            if not suffixes:
                continue
            path = _TurnPath(prefix + self._rng.choice(suffixes))
            if clockwise and path.winding() != clockwise * 4:
                continue
            if path.is_loop():
                return path

    def _lookup_paths(
        self,
        dx: int,
        dy: int,
        target_x: int,
        target_y: int,
        target_dx: int,
        target_dy: int,
    ) -> list[tuple[int, ...]]:
        rotated_x, rotated_y = _unrotate(target_x, target_y, dx, dy)
        rotated_dx, rotated_dy = _unrotate(target_dx, target_dy, dx, dy)
        return self._lookup[rotated_x, rotated_y, rotated_dx, rotated_dy]

    def _enumerate_good_paths(
        self,
        x: int,
        y: int,
        dx: int,
        dy: int,
        budget: int,
        seen: set[tuple[int, int]],
    ) -> Iterator[tuple[tuple[int, ...], tuple[int, int, int, int]]]:
        if budget >= 0:
            yield (), (x, y, dx, dy)
        if budget <= 0:
            return

        seen.add((x, y))
        next_x = x + dx
        next_y = y + dy
        if (next_x, next_y) not in seen:
            for suffix, end_state in self._enumerate_good_paths(
                next_x,
                next_y,
                -dy,
                dx,
                budget - self._turn_price,
                seen,
            ):
                yield (TURN_LEFT,) + suffix, end_state

            for suffix, end_state in self._enumerate_good_paths(
                next_x,
                next_y,
                dy,
                -dx,
                budget - self._turn_price,
                seen,
            ):
                yield (TURN_RIGHT,) + suffix, end_state

            seen.add((next_x, next_y))
            far_x = next_x + dx
            far_y = next_y + dy
            if (far_x, far_y) not in seen:
                for suffix, end_state in self._enumerate_good_paths(
                    far_x,
                    far_y,
                    dx,
                    dy,
                    budget - self._straight_price,
                    seen,
                ):
                    yield (TURN_STRAIGHT,) + suffix, end_state
            seen.remove((next_x, next_y))
        seen.remove((x, y))


class _TubeGrid:
    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._cells: dict[tuple[int, int], str] = {}

    def __getitem__(self, key: tuple[int, int]) -> str:
        return self._cells.get(key, " ")

    def __setitem__(self, key: tuple[int, int], value: str) -> None:
        self._cells[key] = value

    def __contains__(self, key: tuple[int, int]) -> bool:
        return key in self._cells

    def items(self):
        return self._cells.items()

    def clear(self) -> None:
        self._cells.clear()

    def shrink(self) -> _TubeGrid:
        shrunk = _TubeGrid(self.width // 2, self.height // 2)
        for y in range(shrunk.height):
            for x in range(shrunk.width):
                shrunk[x, y] = self[2 * x + 1, 2 * y + 1]
        return shrunk

    def can_draw(self, path: _TurnPath, x0: int, y0: int, dx0: int = 0, dy0: int = 1) -> bool:
        for x, y in path.points(dx0, dy0):
            px = x0 - x + y
            py = y0 + x + y
            if not (0 <= px < self.width and 0 <= py < self.height):
                return False
            if (px, py) in self:
                return False
        return True

    def draw(self, path: _TurnPath, x0: int, y0: int, dx0: int = 0, dy0: int = 1, *, loop: bool = False) -> None:
        points = list(path.points(dx0, dy0))
        if loop:
            if points[0] != points[-1]:
                raise ValueError("Loop path must end where it starts.")
            points.append(points[1])

        for index in range(1, len(points) - 1):
            prev_x, prev_y = points[index - 1]
            x, y = points[index]
            next_x, next_y = points[index + 1]
            key = (x0 - x + y, y0 + x + y)
            signature = (
                next_x - prev_x,
                next_y - prev_y,
                _sign((x - prev_x) * (next_y - y) - (next_x - x) * (y - prev_y)),
            )
            self[key] = {
                (1, 1, 1): "<",
                (-1, -1, -1): "<",
                (1, 1, -1): ">",
                (-1, -1, 1): ">",
                (-1, 1, 1): "v",
                (1, -1, -1): "v",
                (-1, 1, -1): "^",
                (1, -1, 1): "^",
                (0, 2, 0): "\\",
                (0, -2, 0): "\\",
                (2, 0, 0): "/",
                (-2, 0, 0): "/",
            }[signature]

    def tube_view(self) -> tuple[_TubeGrid, _UnionFind]:
        uf = _UnionFind()
        tube = _TubeGrid(self.width, self.height)

        for x in range(self.width):
            direction = "-"
            for y in range(self.height):
                for dx, dy in {
                    "/-": [(0, 1)],
                    "\\-": [(1, 0), (0, 1)],
                    "/|": [(1, 0)],
                    " -": [(1, 0)],
                    " |": [(0, 1)],
                    "v|": [(0, 1)],
                    ">|": [(1, 0)],
                    "v-": [(0, 1)],
                    ">-": [(1, 0)],
                }.get(self[x, y] + direction, []):
                    uf.union((x, y), (x + dx, y + dy))

                tube[x, y] = {
                    "/-": "┐",
                    "\\-": "┌",
                    "/|": "└",
                    "\\|": "┘",
                    " -": "-",
                    " |": "|",
                }.get(self[x, y] + direction, "x")

                if self[x, y] in "\\/v^":
                    direction = "|" if direction == "-" else "-"

        return tube, uf

    def clear_inside_loop(self, path: _TurnPath, x0: int, y0: int) -> None:
        path_grid = _TubeGrid(self.width, self.height)
        path_grid.draw(path, x0, y0, loop=True)
        for key, value in path_grid.tube_view()[0].items():
            if value == "|":
                self._cells.pop(key, None)


def build_tube_candidate(
    *,
    size: int,
    rng: random.Random,
    min_pairs: int,
    max_pairs: int,
    max_attempts: int = 120,
    loop_tries: int = 1000,
) -> TubeCandidate:
    sampler = _PathSampler(rng)
    sampler.prepare(min(20, max(size, 6)))

    for _ in range(max_attempts):
        candidate = _grow_candidate(size=size, rng=rng, sampler=sampler, min_pairs=min_pairs, max_pairs=max_pairs, loop_tries=loop_tries)
        if candidate is not None:
            return candidate

    raise RuntimeError(f"Failed to construct a tube candidate for {size}x{size}.")


def _grow_candidate(
    *,
    size: int,
    rng: random.Random,
    sampler: _PathSampler,
    min_pairs: int,
    max_pairs: int,
    loop_tries: int,
) -> TubeCandidate | None:
    grid = _TubeGrid(2 * size + 1, 2 * size + 1)

    left_path = sampler.random_path_with_walk(size, size, 0, -1)
    if not grid.can_draw(left_path, 0, 0):
        return None
    grid.draw(left_path, 0, 0)
    grid[0, 0] = "\\"
    grid[0, 2 * size] = "/"

    right_path = sampler.random_path_with_walk(size, size, 0, -1)
    if not grid.can_draw(right_path, 2 * size, 2 * size, 0, -1):
        return None
    grid.draw(right_path, 2 * size, 2 * size, 0, -1)
    grid[2 * size, 0] = "/"
    grid[2 * size, 2 * size] = "\\"

    if _candidate_ready(grid, min_pairs=min_pairs, max_pairs=max_pairs):
        return _extract_candidate(grid)

    tube_view, _ = grid.tube_view()
    for _ in range(loop_tries):
        x = 2 * rng.randrange(size)
        y = 2 * rng.randrange(size)
        if tube_view[x, y] not in "-|":
            continue

        loop = sampler.random_loop(clockwise=1 if tube_view[x, y] == "-" else -1)
        if not grid.can_draw(loop, x, y):
            continue

        grid.clear_inside_loop(loop, x, y)
        grid.draw(loop, x, y, loop=True)
        tube_view, _ = grid.tube_view()

        if _candidate_ready(grid, min_pairs=min_pairs, max_pairs=max_pairs):
            return _extract_candidate(grid)

        shrunk = grid.shrink()
        shrunk_tube, uf = shrunk.tube_view()
        if sum(value == "x" for _, value in shrunk_tube.items()) // 2 > max_pairs:
            return None

    return None


def _candidate_ready(grid: _TubeGrid, *, min_pairs: int, max_pairs: int) -> bool:
    shrunk = grid.shrink()
    tube, uf = shrunk.tube_view()
    pair_count = sum(value == "x" for _, value in tube.items()) // 2
    return (
        min_pairs <= pair_count <= max_pairs
        and not _has_detached_loops(shrunk, uf)
        and not _has_adjacent_same_endpoints(tube, uf)
        and not _has_self_touch(shrunk, uf)
    )


def _extract_candidate(grid: _TubeGrid) -> TubeCandidate:
    shrunk = grid.shrink()
    _, uf = shrunk.tube_view()
    endpoints_by_component: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)

    for y in range(shrunk.height):
        for x in range(shrunk.width):
            root = uf.find((x, y))
            if shrunk[x, y] in "v^<>":
                endpoints_by_component[root].append((x, y))

    dots: list[Dot] = []
    color_id = 1
    for endpoints in sorted(endpoints_by_component.values()):
        if len(endpoints) != 2:
            continue
        dots.append(Dot(color_id=color_id, p1=endpoints[0], p2=endpoints[1]))
        color_id += 1

    return TubeCandidate(dots=dots, pair_count=len(dots))


def _has_detached_loops(grid: _TubeGrid, uf: _UnionFind) -> bool:
    groups = len({uf.find((x, y)) for y in range(grid.height) for x in range(grid.width)})
    endpoints = sum(grid[x, y] in "v^<>" for y in range(grid.height) for x in range(grid.width))
    return endpoints != 2 * groups


def _has_adjacent_same_endpoints(tube: _TubeGrid, uf: _UnionFind) -> bool:
    for y in range(tube.height):
        for x in range(tube.width):
            for dx, dy in ((1, 0), (0, 1)):
                nx = x + dx
                ny = y + dy
                if nx >= tube.width or ny >= tube.height:
                    continue
                if tube[x, y] == tube[nx, ny] == "x" and uf.find((x, y)) == uf.find((nx, ny)):
                    return True
    return False


def _has_self_touch(grid: _TubeGrid, uf: _UnionFind) -> bool:
    for y in range(grid.height):
        for x in range(grid.width):
            root = uf.find((x, y))
            same_neighbors = 0
            for dx, dy in ((1, 0), (0, 1), (-1, 0), (0, -1)):
                nx = x + dx
                ny = y + dy
                if 0 <= nx < grid.width and 0 <= ny < grid.height and uf.find((nx, ny)) == root:
                    same_neighbors += 1
            if same_neighbors >= 3:
                return True
    return False


def _sign(value: int) -> int:
    if value == 0:
        return 0
    return -1 if value < 0 else 1


def tube_pair_bounds(size: int, target_difficulty: str | None) -> tuple[int, int]:
    baseline = max(4, int(size**0.5 * 2))
    difficulty = (target_difficulty or "").lower()
    if difficulty == "easy":
        return baseline, baseline + 1
    if difficulty == "hard":
        return max(4, baseline - 1), min(size + 2, baseline + 2)
    return baseline, min(size + 2, baseline + 2)
