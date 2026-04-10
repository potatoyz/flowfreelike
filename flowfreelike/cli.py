from __future__ import annotations

import argparse
import json
from pathlib import Path

from flowfreelike.editor import build_level_filename, format_level_number, launch_editor, parse_level_number
from flowfreelike.generator import export_level, generate_level
from flowfreelike.models import Dot
from flowfreelike.registry import build_level_fingerprint, build_level_index
from flowfreelike.solver import solve_puzzle
from flowfreelike.validation import validate_level_collection, validate_puzzle

DEFAULT_LEVELS_DIR = Path("levels")


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

    validate_parser = subparsers.add_parser("validate", help="Validate a level JSON file or directory.")
    validate_parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        default=DEFAULT_LEVELS_DIR,
        help="Level JSON file or directory to validate. Defaults to levels/.",
    )

    editor_parser = subparsers.add_parser("editor", help="Launch a browser-based manual level editor.")
    editor_parser.add_argument("--size", type=int, default=5, help="Initial grid size for the editor.")
    editor_parser.add_argument(
        "--input",
        type=Path,
        default=None,
        help="Optional existing level JSON file to preload into the editor.",
    )
    editor_parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional default output path shown in the editor save field.",
    )
    editor_parser.add_argument(
        "--levels-dir",
        type=Path,
        default=DEFAULT_LEVELS_DIR,
        help="Directory used for duplicate checks while validating editor drafts.",
    )
    editor_parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host interface used for the local editor web server.",
    )
    editor_parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="Port used for the local editor web server. Defaults to an automatic free port.",
    )
    editor_parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Start the editor server without opening a browser tab automatically.",
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

    if args.command == "validate":
        _run_validate(args)
        return

    if args.command == "editor":
        launch_editor(
            size=args.size,
            input_path=args.input,
            output_path=args.output,
            levels_dir=args.levels_dir,
            host=args.host,
            port=args.port,
            open_browser=not args.no_browser,
        )
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


def _next_level_number(output_dir: Path) -> int:
    max_number = 0
    if output_dir.exists():
        for path in output_dir.glob("*.json"):
            if (level_number := parse_level_number(path)) is not None:
                max_number = max(max_number, level_number)
    return max_number + 1


def _reserve_output_path(output_dir: Path, puzzle, next_level_number: int) -> tuple[int, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    level_number = next_level_number
    while True:
        level_id = format_level_number(level_number)
        output_path = output_dir / build_level_filename(level_number)
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
