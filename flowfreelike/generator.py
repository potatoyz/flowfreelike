from __future__ import annotations

import json
import random
from collections.abc import Iterable
from pathlib import Path

from flowfreelike.geometry import GRID_SYMMETRIES
from flowfreelike.models import Dot, Point, Puzzle, SolveResult
from flowfreelike.registry import LevelFingerprint, build_level_index, find_duplicate_level
from flowfreelike.solver import solve_puzzle

MIN_PATH_CELLS = 3
PATH_COVER_RESTARTS_BASE = 6
PATH_COVER_SAMPLE_COUNT_BASE = 4
SHORTEST_PATH_CAP = 12
HIGH_FLEX_PATH_THRESHOLD = 6
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
    duplicate_rejections = 0
    partial_solution_rejections = 0
    aesthetic_rejections = 0
    ambiguity_rejections = 0
    search_limit_retries = 0

    for attempt in range(1, max_attempts + 1):
        try:
            segment_candidates = _generate_segment_cover_candidates(size, rng, target_difficulty)
        except ValueError:
            continue

        for segments in segment_candidates:
            turn_metrics = _path_cover_metrics(segments)
            ambiguity_metrics = _partition_ambiguity_metrics(size, segments)
            length_metrics = _segment_length_metrics(size, segments)
            gate_failures = _candidate_gate_failures(
                segments,
                turn_metrics,
                ambiguity_metrics,
                length_metrics,
                size,
                target_difficulty,
            )
            if gate_failures:
                if any(
                    reason in {"turns", "straight", "zero_turns", "too_many_short_segments", "missing_anchor_segment"}
                    for reason in gate_failures
                ):
                    aesthetic_rejections += 1
                if any(reason in {"shortest_choices", "high_flex", "loose_endpoints", "path_slack"} for reason in gate_failures):
                    ambiguity_rejections += 1
                continue

            dots = [
                Dot(color_id=index + 1, p1=segment[0], p2=segment[-1])
                for index, segment in enumerate(segments)
            ]
            known_solution = {
                index + 1: segment
                for index, segment in enumerate(segments)
            }

            duplicate_of = find_duplicate_level(size=size, dots=dots, level_index=level_index)
            if duplicate_of is not None:
                duplicate_rejections += 1
                continue

            alternative_result = solve_puzzle(
                size=size,
                dots=dots,
                solution_limit=1,
                completion_mode="full",
                path_cap=BASE_SOLVER_PATH_CAP,
                known_solution=known_solution,
                exclude_known_solution=True,
            )
            if alternative_result.status == "search_limit" and _should_retry_search_limit(size, segments, ambiguity_metrics, length_metrics):
                search_limit_retries += 1
                alternative_result = solve_puzzle(
                    size=size,
                    dots=dots,
                    solution_limit=1,
                    completion_mode="full",
                    path_cap=EXTENDED_SOLVER_PATH_CAP,
                    known_solution=known_solution,
                    exclude_known_solution=True,
                )
            if alternative_result.status == "search_limit" or alternative_result.solution_count > 0:
                continue

            result = SolveResult(
                status="unique",
                solution_count=1,
                stats=alternative_result.stats,
                solutions=[known_solution],
            )

            partial_result = solve_puzzle(
                size=size,
                dots=dots,
                solution_limit=1,
                completion_mode="partial",
            )
            if partial_result.solution_count > 0:
                partial_solution_rejections += 1
                continue

            solution = known_solution
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
                "partial_solution_rejections": partial_solution_rejections,
                "aesthetic_rejections": aesthetic_rejections,
                "ambiguity_rejections": ambiguity_rejections,
                "search_limit_retries": search_limit_retries,
                "verification_mode": "known_solution_alternative_search",
                "generation_mode": "multi_path_forest",
                "candidate_batch_size": len(segment_candidates),
                "min_solution_path_cells": min_solution_path,
                "solution_coverage_cells": coverage_cells,
                "solution_coverage_ratio": round(coverage_cells / (size * size), 3),
                "segment_turns_total": turn_metrics["total_turns"],
                "segment_zero_turns": turn_metrics["zero_turn_segments"],
                "segment_longest_straight_run": turn_metrics["longest_straight_run"],
                "segment_short_count": length_metrics["short_segments"],
                "segment_anchor_count": length_metrics["anchor_segments"],
                "segment_length_spread": length_metrics["length_spread"],
                "segment_max_length": length_metrics["max_length"],
                "segment_total_shortest_path_choices": ambiguity_metrics["total_shortest_path_choices"],
                "segment_max_shortest_path_choices": ambiguity_metrics["max_shortest_path_choices"],
                "segment_total_path_slack": ambiguity_metrics["total_path_slack"],
                "segment_high_flex_count": ambiguity_metrics["high_flex_segments"],
                "segment_loose_endpoint_count": ambiguity_metrics["loose_endpoint_count"],
                **result.stats.to_dict(),
            }
            puzzle = Puzzle(
                level_id=level_id,
                grid_size=size,
                difficulty=difficulty,
                dots=dots,
                metrics=metrics,
                solution=known_solution,
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


def _generate_segment_cover_candidates(
    size: int,
    rng: random.Random,
    target_difficulty: str | None,
) -> list[list[tuple[Point, ...]]]:
    total_cells = size * size
    candidates: list[tuple[float, list[tuple[Point, ...]]]] = []

    for _ in range(_path_cover_sample_count(size)):
        color_count = _pick_color_count(total_cells, rng, target_difficulty)
        segments = _build_path_cover(size, color_count, rng, target_difficulty)
        if segments is None:
            continue
        segments = _apply_random_symmetry(segments, size, rng)
        rng.shuffle(segments)
        score = _segment_partition_score(size, segments, target_difficulty)
        candidates.append((score, segments))

    if not candidates:
        raise ValueError("Failed to construct a valid multi-path cover.")

    return [segments for _, segments in sorted(candidates, key=lambda item: item[0], reverse=True)]


def _build_path_cover(
    size: int,
    color_count: int,
    rng: random.Random,
    target_difficulty: str | None,
) -> list[tuple[Point, ...]] | None:
    cell_count = size * size
    neighbor_indices = tuple(
        tuple(_neighbor_indexes(index, size))
        for index in range(cell_count)
    )

    for _ in range(_path_cover_restart_count(size)):
        parent = list(range(cell_count))
        component_sizes = [1] * cell_count
        members: dict[int, set[int]] = {index: {index} for index in range(cell_count)}
        adjacency: list[set[int]] = [set() for _ in range(cell_count)]
        degrees = [0] * cell_count
        component_count = cell_count
        failed = False

        while component_count > color_count:
            candidates = _merge_candidates(
                size=size,
                parent=parent,
                component_sizes=component_sizes,
                adjacency=adjacency,
                degrees=degrees,
                neighbor_indices=neighbor_indices,
                component_count=component_count,
                target_component_count=color_count,
                rng=rng,
                target_difficulty=target_difficulty,
            )
            if not candidates:
                failed = True
                break

            _, u, v = _pick_merge_candidate(candidates, rng)
            adjacency[u].add(v)
            adjacency[v].add(u)
            degrees[u] += 1
            degrees[v] += 1
            _union_components(parent, component_sizes, members, u, v)
            component_count -= 1

            if not _forest_state_viable(
                target_component_count=color_count,
                parent=parent,
                component_sizes=component_sizes,
                members=members,
                adjacency=adjacency,
                degrees=degrees,
                neighbor_indices=neighbor_indices,
            ):
                failed = True
                break

        if failed or component_count != color_count:
            continue

        if any(component_sizes[root] < MIN_PATH_CELLS for root in members):
            continue

        segments = _materialize_segments(size=size, adjacency=adjacency, members=members)
        if segments is None:
            continue
        if len(segments) != color_count:
            continue
        if min(len(segment) for segment in segments) < MIN_PATH_CELLS:
            continue
        return segments

    return None


def _merge_candidates(
    size: int,
    parent: list[int],
    component_sizes: list[int],
    adjacency: list[set[int]],
    degrees: list[int],
    neighbor_indices: tuple[tuple[int, ...], ...],
    component_count: int,
    target_component_count: int,
    rng: random.Random,
    target_difficulty: str | None,
) -> list[tuple[float, int, int]]:
    candidates: list[tuple[float, int, int]] = []

    for u in range(len(parent)):
        if degrees[u] >= 2:
            continue
        root_u = _find_root(parent, u)
        for v in neighbor_indices[u]:
            if v <= u or degrees[v] >= 2:
                continue
            root_v = _find_root(parent, v)
            if root_u == root_v:
                continue
            score = _merge_score(
                size=size,
                u=u,
                v=v,
                root_u=root_u,
                root_v=root_v,
                component_sizes=component_sizes,
                adjacency=adjacency,
                degrees=degrees,
                component_count=component_count,
                target_component_count=target_component_count,
                target_difficulty=target_difficulty,
            )
            score += rng.random() * 0.25
            candidates.append((score, u, v))

    return candidates


def _pick_merge_candidate(
    candidates: list[tuple[float, int, int]],
    rng: random.Random,
) -> tuple[float, int, int]:
    candidates.sort(key=lambda item: item[0], reverse=True)
    top_count = min(len(candidates), 10)
    top_candidates = candidates[:top_count]
    floor = min(score for score, _, _ in top_candidates)
    weights = [score - floor + 1.0 for score, _, _ in top_candidates]
    return rng.choices(top_candidates, weights=weights, k=1)[0]


def _merge_score(
    size: int,
    u: int,
    v: int,
    root_u: int,
    root_v: int,
    component_sizes: list[int],
    adjacency: list[set[int]],
    degrees: list[int],
    component_count: int,
    target_component_count: int,
    target_difficulty: str | None,
) -> float:
    size_u = component_sizes[root_u]
    size_v = component_sizes[root_v]
    merged_size = size_u + size_v
    remaining_merges = component_count - target_component_count
    score = 0.0

    score += (max(0, MIN_PATH_CELLS - size_u) + max(0, MIN_PATH_CELLS - size_v)) * 12
    if merged_size == 2:
        score -= 8
    score += min(merged_size, size + 3) * 1.5
    score += _turn_extension_bonus(u, v, adjacency) * 2.0
    score += _turn_extension_bonus(v, u, adjacency) * 2.0
    score += _centrality_bonus(u, size) + _centrality_bonus(v, size)

    if remaining_merges <= max(2, size // 2):
        score += min(merged_size, size * 2) * 1.2

    difficulty = (target_difficulty or "").lower()
    if difficulty == "easy":
        score += min(merged_size, size + 2)
        score -= max(0, merged_size - (size + 2)) * 1.5
    elif difficulty == "hard":
        score += max(0, merged_size - size) * 2.5
        score += _turn_extension_bonus(u, v, adjacency) * 1.5
        score += _turn_extension_bonus(v, u, adjacency) * 1.5
    else:
        score += max(0, merged_size - size // 2)

    if degrees[u] == 0 and degrees[v] == 0:
        score -= 2.5

    return score


def _forest_state_viable(
    target_component_count: int,
    parent: list[int],
    component_sizes: list[int],
    members: dict[int, set[int]],
    adjacency: list[set[int]],
    degrees: list[int],
    neighbor_indices: tuple[tuple[int, ...], ...],
) -> bool:
    sealed_count = 0

    for root, component_members in members.items():
        can_merge = _component_can_merge(
            root=root,
            component_members=component_members,
            parent=parent,
            degrees=degrees,
            neighbor_indices=neighbor_indices,
        )
        if not can_merge:
            sealed_count += 1
            if component_sizes[root] < MIN_PATH_CELLS:
                return False

    if sealed_count > target_component_count:
        return False
    return True


def _component_can_merge(
    root: int,
    component_members: set[int],
    parent: list[int],
    degrees: list[int],
    neighbor_indices: tuple[tuple[int, ...], ...],
) -> bool:
    for cell in component_members:
        if degrees[cell] >= 2:
            continue
        for neighbor in neighbor_indices[cell]:
            if degrees[neighbor] >= 2:
                continue
            if _find_root(parent, neighbor) != root:
                return True
    return False


def _materialize_segments(
    size: int,
    adjacency: list[set[int]],
    members: dict[int, set[int]],
) -> list[tuple[Point, ...]] | None:
    index_to_point = tuple((x, y) for y in range(size) for x in range(size))
    segments: list[tuple[Point, ...]] = []

    for component_members in members.values():
        if not component_members:
            return None
        if len(component_members) < MIN_PATH_CELLS:
            return None

        endpoints = [cell for cell in component_members if len(adjacency[cell]) <= 1]
        if len(endpoints) != 2:
            return None

        start = endpoints[0]
        ordered = [start]
        previous = -1
        current = start

        while True:
            next_cells = [neighbor for neighbor in adjacency[current] if neighbor != previous]
            if not next_cells:
                break
            previous, current = current, next_cells[0]
            ordered.append(current)

        if len(ordered) != len(component_members):
            return None

        segments.append(tuple(index_to_point[index] for index in ordered))

    return segments


def _apply_random_symmetry(
    segments: list[tuple[Point, ...]],
    size: int,
    rng: random.Random,
) -> list[tuple[Point, ...]]:
    transform = rng.choice(GRID_SYMMETRIES)
    return [tuple(transform(point, size) for point in segment) for segment in segments]


def _find_root(parent: list[int], index: int) -> int:
    while parent[index] != index:
        parent[index] = parent[parent[index]]
        index = parent[index]
    return index


def _union_components(
    parent: list[int],
    component_sizes: list[int],
    members: dict[int, set[int]],
    a: int,
    b: int,
) -> int:
    root_a = _find_root(parent, a)
    root_b = _find_root(parent, b)
    if root_a == root_b:
        return root_a

    if component_sizes[root_a] < component_sizes[root_b]:
        root_a, root_b = root_b, root_a

    parent[root_b] = root_a
    component_sizes[root_a] += component_sizes[root_b]
    component_sizes[root_b] = 0
    members[root_a].update(members.pop(root_b))
    return root_a


def _neighbor_indexes(index: int, size: int) -> list[int]:
    x = index % size
    y = index // size
    neighbors: list[int] = []
    if x > 0:
        neighbors.append(index - 1)
    if x + 1 < size:
        neighbors.append(index + 1)
    if y > 0:
        neighbors.append(index - size)
    if y + 1 < size:
        neighbors.append(index + size)
    return neighbors


def _turn_extension_bonus(cell: int, neighbor: int, adjacency: list[set[int]]) -> float:
    if len(adjacency[cell]) != 1:
        return 0.0
    existing = next(iter(adjacency[cell]))
    ax, ay = existing, cell
    bx, by = cell, neighbor
    existing_direction = _index_direction(ax, ay)
    next_direction = _index_direction(bx, by)
    return 1.0 if existing_direction != next_direction else -0.5


def _index_direction(a: int, b: int) -> int:
    if b == a + 1:
        return 0
    if b == a - 1:
        return 1
    if b > a:
        return 2
    return 3


def _centrality_bonus(index: int, size: int) -> float:
    x = index % size
    y = index // size
    center = (size - 1) / 2
    return max(0.0, size / 2 - (abs(x - center) + abs(y - center)) / 2)


def _segment_partition_score(
    size: int,
    segments: list[tuple[Point, ...]],
    target_difficulty: str | None,
) -> float:
    metrics = _path_cover_metrics(segments)
    ambiguity = _partition_ambiguity_metrics(size, segments)
    lengths = _segment_length_metrics(size, segments)

    score = (
        metrics["total_turns"] * 3
        + metrics["winding_segments"] * 5
        - metrics["zero_turn_segments"] * 5
        - metrics["longest_straight_run"] * 2
        + min(2, lengths["anchor_segments"]) * 9
        + min(size + 3, lengths["length_spread"]) * 1.5
        + min(size * 2, lengths["max_length"]) * 0.5
        - lengths["short_segments"] * 6
        - max(0, lengths["anchor_segments"] - 2) * 5
        - ambiguity["total_shortest_path_choices"] * 5
        - ambiguity["total_path_slack"] * 4
        - ambiguity["high_flex_segments"] * 12
        - ambiguity["loose_endpoint_count"] * 5
        + ambiguity["edge_endpoint_count"] * 2
    )

    if target_difficulty:
        difficulty = target_difficulty.lower()
        if difficulty == "easy":
            score += lengths["short_segments"] * 2
            score -= lengths["anchor_segments"] * 2
        elif difficulty == "hard":
            score += lengths["anchor_segments"] * 4
            score -= lengths["short_segments"] * 2

    return score


def _should_retry_search_limit(
    size: int,
    segments: list[tuple[Point, ...]],
    ambiguity: dict[str, int],
    lengths: dict[str, int],
) -> bool:
    return (
        ambiguity["total_path_slack"] <= size + 2
        and ambiguity["max_shortest_path_choices"] <= 2
        and ambiguity["high_flex_segments"] == 0
        and lengths["short_segments"] <= max(1, len(segments) // 4)
        and lengths["anchor_segments"] >= 1
    )


def _candidate_gate_failures(
    segments: list[tuple[Point, ...]],
    metrics: dict[str, int],
    ambiguity: dict[str, int],
    lengths: dict[str, int],
    size: int,
    target_difficulty: str | None,
) -> list[str]:
    failures: list[str] = []
    difficulty = (target_difficulty or "").lower()

    max_zero_turn_segments = max(1, len(segments) // 3)
    min_total_turns = max(size - 1, len(segments) - 1)
    max_straight_run = max(3, size)
    max_shortest_path_choices = max(4, size + size // 2)
    max_high_flex_segments = max(1, len(segments) // 4)
    max_loose_endpoints = max(2, len(segments) - 1)
    max_path_slack = max(size + len(segments), size * 2 - 2)
    max_short_segments = max(1, len(segments) // 2)
    min_anchor_segments = 1 if size >= 6 and difficulty != "easy" else 0

    if difficulty == "hard":
        max_short_segments = max(1, len(segments) // 3)
        min_anchor_segments = 1
        max_path_slack = max(size + len(segments) - 2, size * 2 - 4)
    elif difficulty == "easy":
        max_short_segments = max(2, (len(segments) * 2) // 3)
        min_anchor_segments = 0

    if metrics["zero_turn_segments"] > max_zero_turn_segments:
        failures.append("zero_turns")
    if metrics["total_turns"] < min_total_turns:
        failures.append("turns")
    if metrics["longest_straight_run"] > max_straight_run:
        failures.append("straight")
    if lengths["short_segments"] > max_short_segments:
        failures.append("too_many_short_segments")
    if lengths["anchor_segments"] < min_anchor_segments:
        failures.append("missing_anchor_segment")
    if ambiguity["max_shortest_path_choices"] > max_shortest_path_choices:
        failures.append("shortest_choices")
    if ambiguity["high_flex_segments"] > max_high_flex_segments:
        failures.append("high_flex")
    if ambiguity["loose_endpoint_count"] > max_loose_endpoints:
        failures.append("loose_endpoints")
    if ambiguity["total_path_slack"] > max_path_slack:
        failures.append("path_slack")
    return failures


def _path_cover_metrics(segments: list[tuple[Point, ...]]) -> dict[str, int]:
    total_turns = 0
    zero_turn_segments = 0
    winding_segments = 0
    longest_straight_run = 0

    for segment in segments:
        turns = _count_turns(segment)
        straight_run = _longest_straight_run(segment)
        total_turns += turns
        longest_straight_run = max(longest_straight_run, straight_run)
        if turns == 0:
            zero_turn_segments += 1
        if turns >= 2:
            winding_segments += 1

    return {
        "total_turns": total_turns,
        "zero_turn_segments": zero_turn_segments,
        "winding_segments": winding_segments,
        "longest_straight_run": longest_straight_run,
    }


def _segment_length_metrics(size: int, segments: list[tuple[Point, ...]]) -> dict[str, int]:
    lengths = [len(segment) for segment in segments]
    short_threshold = MIN_PATH_CELLS
    anchor_threshold = max(MIN_PATH_CELLS + 3, size + 1)
    short_segments = sum(1 for length in lengths if length <= short_threshold)
    anchor_segments = sum(1 for length in lengths if length >= anchor_threshold)
    max_length = max(lengths)
    min_length = min(lengths)
    return {
        "short_segments": short_segments,
        "anchor_segments": anchor_segments,
        "max_length": max_length,
        "min_length": min_length,
        "length_spread": max_length - min_length,
    }


def _partition_ambiguity_metrics(size: int, segments: list[tuple[Point, ...]]) -> dict[str, int]:
    all_endpoints = {segment[0] for segment in segments} | {segment[-1] for segment in segments}
    total_shortest_path_choices = 0
    max_shortest_path_choices = 0
    total_path_slack = 0
    high_flex_segments = 0
    loose_endpoint_count = 0
    edge_endpoint_count = 0

    for segment in segments:
        start = segment[0]
        end = segment[-1]
        blocked = set(all_endpoints)
        blocked.discard(start)
        blocked.discard(end)
        shortest_distance, shortest_path_count = _shortest_path_stats(
            size=size,
            start=start,
            end=end,
            blocked=blocked,
            cap=SHORTEST_PATH_CAP,
        )
        actual_distance = len(segment) - 1
        shortest_path_choices = max(0, shortest_path_count - 1)
        path_slack = max(0, actual_distance - shortest_distance)

        total_shortest_path_choices += shortest_path_choices
        max_shortest_path_choices = max(max_shortest_path_choices, shortest_path_choices)
        total_path_slack += path_slack
        if shortest_path_count >= HIGH_FLEX_PATH_THRESHOLD:
            high_flex_segments += 1

        for endpoint in (start, end):
            degree = sum(1 for neighbor in _neighbors(endpoint, size) if neighbor not in blocked)
            if degree >= 3:
                loose_endpoint_count += 1
            if endpoint[0] in (0, size - 1) or endpoint[1] in (0, size - 1):
                edge_endpoint_count += 1

    return {
        "total_shortest_path_choices": total_shortest_path_choices,
        "max_shortest_path_choices": max_shortest_path_choices,
        "total_path_slack": total_path_slack,
        "high_flex_segments": high_flex_segments,
        "loose_endpoint_count": loose_endpoint_count,
        "edge_endpoint_count": edge_endpoint_count,
    }


def _shortest_path_stats(
    size: int,
    start: Point,
    end: Point,
    blocked: set[Point],
    cap: int,
) -> tuple[int, int]:
    distances = {start: 0}
    queue = [start]
    cursor = 0

    while cursor < len(queue):
        point = queue[cursor]
        cursor += 1
        for neighbor in _neighbors(point, size):
            if neighbor in blocked or neighbor in distances:
                continue
            distances[neighbor] = distances[point] + 1
            queue.append(neighbor)

    if end not in distances:
        return size * size, cap

    ordered_points = sorted(distances, key=distances.get)
    path_counts = {start: 1}

    for point in ordered_points[1:]:
        distance = distances[point]
        count = 0
        for neighbor in _neighbors(point, size):
            if distances.get(neighbor) != distance - 1:
                continue
            count += path_counts.get(neighbor, 0)
            if count >= cap:
                count = cap
                break
        path_counts[point] = count

    return distances[end], min(cap, path_counts.get(end, 0))


def _count_turns(segment: tuple[Point, ...]) -> int:
    if len(segment) < 3:
        return 0

    turns = 0
    previous_direction = _direction(segment[0], segment[1])
    for index in range(1, len(segment) - 1):
        current_direction = _direction(segment[index], segment[index + 1])
        if current_direction != previous_direction:
            turns += 1
        previous_direction = current_direction
    return turns


def _longest_straight_run(segment: tuple[Point, ...]) -> int:
    if len(segment) < 2:
        return len(segment)

    longest = 2
    current_run = 2
    previous_direction = _direction(segment[0], segment[1])

    for index in range(1, len(segment) - 1):
        current_direction = _direction(segment[index], segment[index + 1])
        if current_direction == previous_direction:
            current_run += 1
        else:
            longest = max(longest, current_run)
            current_run = 2
        previous_direction = current_direction

    return max(longest, current_run)


def _direction(a: Point, b: Point) -> Point:
    return b[0] - a[0], b[1] - a[1]


def _neighbors(point: Point, size: int) -> list[Point]:
    x, y = point
    neighbors: list[Point] = []
    if x > 0:
        neighbors.append((x - 1, y))
    if x + 1 < size:
        neighbors.append((x + 1, y))
    if y > 0:
        neighbors.append((x, y - 1))
    if y + 1 < size:
        neighbors.append((x, y + 1))
    return neighbors


def _path_cover_sample_count(size: int) -> int:
    return max(PATH_COVER_SAMPLE_COUNT_BASE, size + 4)


def _path_cover_restart_count(size: int) -> int:
    return max(PATH_COVER_RESTARTS_BASE, size)


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
            lower = max(3, size - 1)
            upper = min(upper, size + 2)
        elif difficulty == "hard":
            lower = max(2, size // 2)
            upper = min(upper, max(lower, size))
        else:
            lower = max(3, size // 2 + 1)
            upper = min(upper, size + 1)
    else:
        lower = max(3, size // 2 + 1)
        upper = min(upper, size + 1)

    lower = min(lower, upper)
    if lower == upper:
        return lower

    first = rng.randint(lower, upper)
    second = rng.randint(lower, upper)
    third = rng.randint(lower, upper)

    if target_difficulty:
        difficulty = target_difficulty.lower()
        if difficulty == "easy":
            return max(first, second, third)
        if difficulty == "hard":
            return min(first, second, third)

    return sorted((first, second, third))[1]


def _grade(result: SolveResult) -> str:
    ratio = result.stats.forced_move_ratio
    score = result.stats.backtracks + result.stats.branch_points * 3 + max(0.0, 0.65 - ratio) * 20

    if score < 12:
        return "Easy"
    if score < 45:
        return "Medium"
    return "Hard"



