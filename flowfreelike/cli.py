from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from flowfreelike.generator import export_level, generate_level, make_level_id
from flowfreelike.models import Dot, Solution, solution_from_dict
from flowfreelike.registry import build_level_fingerprint, build_level_index, load_level_definition
from flowfreelike.solver import solve_puzzle
from flowfreelike.validation import validate_level_collection, validate_puzzle

DEFAULT_LEVELS_DIR = Path("levels")
LEVEL_ID_PATTERN = re.compile(r"lvl_(\d+)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Flow Free-like level generator and solver.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate_parser = subparsers.add_parser("generate", help="Generate one or more validated levels.")
    generate_parser.add_argument("--size", type=int, default=5, help="Grid size, e.g. 5 for 5x5.")
    generate_parser.add_argument("--count", type=int, default=1, help="How many validated levels to generate.")
    generate_parser.add_argument("--seed", type=int, default=None, help="Optional base random seed.")
    generate_parser.add_argument(
        "--difficulty",
        choices=["easy", "medium", "hard"],
        default=None,
        help="Optional target difficulty to bias generation.",
    )
    generate_parser.add_argument(
        "--max-attempts",
        type=int,
        default=300,
        help="Maximum generation attempts per level before skipping it.",
    )
    generate_parser.add_argument(
        "--existing-levels",
        type=Path,
        default=DEFAULT_LEVELS_DIR,
        help="Existing level JSON file or directory used for duplicate detection.",
    )
    generate_parser.add_argument(
        "--skip-duplicate-check",
        action="store_true",
        help="Disable duplicate detection against existing levels.",
    )
    generate_parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_LEVELS_DIR,
        help="Directory used when writing generated levels in batch mode.",
    )
    generate_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional explicit JSON output path for single-level generation.",
    )
    generate_parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print a single validated level to stdout instead of writing a file.",
    )

    solve_parser = subparsers.add_parser("solve", help="Verify a level JSON file with the solver.")
    solve_parser.add_argument("input", type=Path, help="Path to a level JSON file.")

    preview_parser = subparsers.add_parser("preview", help="Preview a level JSON file in ASCII.")
    preview_parser.add_argument("input", type=Path, help="Path to a level JSON file.")
    preview_parser.add_argument(
        "--hide-solution",
        action="store_true",
        help="Only render the endpoint layout and skip the solved board.",
    )

    validate_parser = subparsers.add_parser("validate", help="Validate a level JSON file or directory.")
    validate_parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        default=DEFAULT_LEVELS_DIR,
        help="Level JSON file or directory to validate. Defaults to levels/.",
    )

    args = parser.parse_args()

    if args.command == "generate":
        _run_generate(args, parser)
        return

    if args.command == "solve":
        data = json.loads(args.input.read_text(encoding="utf-8"))
        dots = [
            Dot(
                color_id=item["color_id"],
                p1=tuple(item["p1"]),
                p2=tuple(item["p2"]),
            )
            for item in data["dots"]
        ]
        result = solve_puzzle(size=data["grid_size"], dots=dots, solution_limit=2)
        print(
            json.dumps(
                {
                    "status": result.status,
                    "solution_count": result.solution_count,
                    "metrics": result.stats.to_dict(),
                },
                indent=2,
            )
        )
        return

    if args.command == "preview":
        _run_preview(args)
        return

    if args.command == "validate":
        _run_validate(args)
        return


def _run_generate(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.count < 1:
        parser.error("--count must be at least 1.")
    if args.output and args.count != 1:
        parser.error("--output can only be used when --count is 1.")
    if args.stdout and args.count != 1:
        parser.error("--stdout can only be used when --count is 1.")
    if args.output and args.stdout:
        parser.error("--output and --stdout cannot be used together.")
    if args.output and args.output.exists():
        parser.error(f"Output file already exists: {args.output}")

    duplicate_sources = []
    if not args.skip_duplicate_check:
        duplicate_sources = _unique_paths([args.existing_levels, args.output_dir])
    level_index = build_level_index(duplicate_sources)

    if args.stdout:
        puzzle, result = generate_level(
            size=args.size,
            seed=args.seed,
            max_attempts=args.max_attempts,
            target_difficulty=args.difficulty,
            existing_level_index=level_index,
        )
        report = validate_puzzle(
            size=puzzle.grid_size,
            dots=puzzle.dots,
            level_id=puzzle.level_id,
            level_index=level_index,
        )
        if not report.valid:
            raise SystemExit(f"Generated level failed validation: {'; '.join(report.issues)}")
        print(json.dumps(puzzle.to_dict(), indent=2))
        print(
            "status="
            f"{result.status} difficulty={puzzle.difficulty} "
            f"colors={len(puzzle.dots)} backtracks={result.stats.backtracks} "
            f"duplicates={puzzle.metrics['duplicate_rejections']} "
            f"forced_ratio={result.stats.forced_move_ratio:.3f}"
        )
        return

    written_paths: list[Path] = []
    failed_reports: list[str] = []
    next_level_number = _next_level_number(args.output_dir)

    for batch_index in range(args.count):
        level_seed = None if args.seed is None else args.seed + batch_index
        try:
            puzzle, result = generate_level(
                size=args.size,
                seed=level_seed,
                max_attempts=args.max_attempts,
                target_difficulty=args.difficulty,
                existing_level_index=level_index,
            )
        except RuntimeError as exc:
            failed_reports.append(str(exc))
            continue

        report = validate_puzzle(
            size=puzzle.grid_size,
            dots=puzzle.dots,
            level_id=puzzle.level_id,
            level_index=level_index,
        )
        if not report.valid:
            failed_reports.append(f"validation failed: {'; '.join(report.issues)}")
            continue

        if args.output:
            output_path = args.output
            puzzle.level_id = args.output.stem
        else:
            next_level_number, output_path = _reserve_output_path(
                output_dir=args.output_dir,
                puzzle=puzzle,
                next_level_number=next_level_number,
            )

        export_level(puzzle, output_path)
        fingerprint = build_level_fingerprint(size=puzzle.grid_size, dots=puzzle.dots)
        level_index[fingerprint] = output_path.resolve()
        written_paths.append(output_path)

        print(
            f"Wrote {output_path} "
            f"difficulty={puzzle.difficulty} colors={len(puzzle.dots)} "
            f"backtracks={result.stats.backtracks}"
        )

    summary = {
        "requested": args.count,
        "written": len(written_paths),
        "skipped": args.count - len(written_paths),
        "output_dir": str(args.output.parent if args.output else args.output_dir),
        "files": [str(path) for path in written_paths],
        "failures": failed_reports,
    }
    print(json.dumps(summary, indent=2))

    if len(written_paths) != args.count:
        raise SystemExit(1)


def _run_validate(args: argparse.Namespace) -> None:
    reports = validate_level_collection(args.target)
    summary = {
        "target": str(args.target),
        "checked": len(reports),
        "valid": sum(1 for report in reports if report.valid),
        "invalid": sum(1 for report in reports if not report.valid),
        "reports": [report.to_dict() for report in reports],
    }
    print(json.dumps(summary, indent=2))

    if summary["invalid"] > 0:
        raise SystemExit(1)


def _run_preview(args: argparse.Namespace) -> None:
    definition = load_level_definition(args.input)
    if definition is None:
        raise SystemExit(f"Failed to parse level JSON: {args.input}")

    data, size, dots = definition
    level_id = str(data.get("level_id", args.input.stem))
    difficulty = str(data.get("difficulty", "Unknown"))

    print(f"{level_id} ({size}x{size}, {difficulty})")
    print(f"colors={len(dots)} source={args.input}")
    print()
    print("Endpoints")
    print(_render_board(_endpoint_cells(size, dots)))

    if args.hide_solution:
        return

    solution, source_label = _load_preview_solution(data, size, dots)
    print()
    if solution is None:
        print("Solution")
        print(f"unavailable ({source_label})")
        return

    print(f"Solution [{source_label}]")
    print(_render_board(_solution_cells(size, solution)))


def _load_preview_solution(
    data: dict[str, object],
    size: int,
    dots: list[Dot],
) -> tuple[Solution | None, str]:
    embedded_issue = None
    try:
        embedded_solution = solution_from_dict(data.get("solution"))
    except ValueError as exc:
        embedded_solution = None
        embedded_issue = str(exc)

    if embedded_solution is not None:
        normalized_solution, embedded_issue = _normalize_preview_solution(size, dots, embedded_solution)
        if normalized_solution is not None:
            return normalized_solution, "embedded"

    result = solve_puzzle(size=size, dots=dots, solution_limit=1, completion_mode="full")
    if result.is_unique and result.solutions:
        source = "solver fallback" if embedded_issue else "solver"
        return result.solutions[0], source
    if embedded_issue:
        return None, f"invalid embedded solution; solver={result.status}"
    return None, result.status


def _endpoint_cells(size: int, dots: list[Dot]) -> list[list[str]]:
    board = [["." for _ in range(size)] for _ in range(size)]
    for dot in dots:
        label = str(dot.color_id)
        for x, y in (dot.p1, dot.p2):
            board[y][x] = label
    return board


def _solution_cells(size: int, solution: Solution) -> list[list[str]]:
    board = [["." for _ in range(size)] for _ in range(size)]
    for color_id, path in sorted(solution.items()):
        label = str(color_id)
        for x, y in path:
            board[y][x] = label
    return board


def _render_board(board: list[list[str]]) -> str:
    size = len(board)
    labels = [cell for row in board for cell in row if cell != "."]
    cell_width = max(
        1,
        len(str(size - 1)),
        max((len(label) for label in labels), default=1),
    )

    header = " " * (cell_width + 1) + " ".join(f"{index:>{cell_width}}" for index in range(size))
    rows = [header]
    for y, row in enumerate(board):
        rows.append(f"{y:>{cell_width}} " + " ".join(f"{cell:>{cell_width}}" for cell in row))
    return "\n".join(rows)


def _normalize_preview_solution(
    size: int,
    dots: list[Dot],
    solution: Solution,
) -> tuple[Solution | None, str | None]:
    dot_by_color = {dot.color_id: dot for dot in dots}
    if set(solution) != set(dot_by_color):
        return None, "solution colors do not match dots."

    normalized: Solution = {}
    occupied: set[tuple[int, int]] = set()

    for color_id, dot in dot_by_color.items():
        path = solution[color_id]
        if path[0] == dot.p2 and path[-1] == dot.p1:
            path = tuple(reversed(path))
        elif path[0] != dot.p1 or path[-1] != dot.p2:
            return None, f"color {color_id} endpoints do not match."

        seen_in_path: set[tuple[int, int]] = set()
        previous_point = None
        for point in path:
            if not (0 <= point[0] < size and 0 <= point[1] < size):
                return None, f"color {color_id} uses out-of-bounds point {point}."
            if point in seen_in_path:
                return None, f"color {color_id} repeats point {point}."
            if point in occupied:
                return None, f"solution overlaps at point {point}."
            if previous_point is not None:
                distance = abs(point[0] - previous_point[0]) + abs(point[1] - previous_point[1])
                if distance != 1:
                    return None, f"color {color_id} has a non-adjacent step."
            seen_in_path.add(point)
            occupied.add(point)
            previous_point = point

        normalized[color_id] = path

    if len(occupied) != size * size:
        return None, f"solution covers {len(occupied)}/{size * size} cells."
    return normalized, None


def _next_level_number(output_dir: Path) -> int:
    max_number = 0
    if output_dir.exists():
        for path in output_dir.glob("*.json"):
            match = LEVEL_ID_PATTERN.search(path.stem)
            if match:
                max_number = max(max_number, int(match.group(1)))
    return max_number + 1


def _reserve_output_path(output_dir: Path, puzzle, next_level_number: int) -> tuple[int, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    level_number = next_level_number
    while True:
        level_id = make_level_id(
            level_number=level_number,
            size=puzzle.grid_size,
            difficulty=puzzle.difficulty,
        )
        output_path = output_dir / f"{level_id}.json"
        if not output_path.exists():
            puzzle.level_id = level_id
            return level_number + 1, output_path
        level_number += 1


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(path)
    return unique
