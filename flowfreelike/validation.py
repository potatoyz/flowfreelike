from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from flowfreelike.models import Dot, SolveResult
from flowfreelike.registry import build_level_fingerprint, build_level_groups, find_duplicate_level, iter_level_files, load_level_definition
from flowfreelike.solver import solve_puzzle

MIN_PATH_CELLS = 3


@dataclass(slots=True)
class LevelValidationReport:
    path: Path | None
    level_id: str
    valid: bool
    issues: list[str]
    solve_result: SolveResult | None
    duplicate_of: Path | None = None
    partial_solution_result: SolveResult | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path) if self.path else None,
            "level_id": self.level_id,
            "valid": self.valid,
            "issues": self.issues,
            "duplicate_of": str(self.duplicate_of) if self.duplicate_of else None,
            "solve_status": self.solve_result.status if self.solve_result else None,
            "solution_count": self.solve_result.solution_count if self.solve_result else None,
            "metrics": self.solve_result.stats.to_dict() if self.solve_result else None,
            "partial_solve_status": self.partial_solution_result.status if self.partial_solution_result else None,
            "partial_solution_count": self.partial_solution_result.solution_count if self.partial_solution_result else None,
        }


def validate_puzzle(
    size: int,
    dots: list[Dot],
    level_id: str = "candidate",
    path: Path | None = None,
    level_index: dict | None = None,
) -> LevelValidationReport:
    issues: list[str] = []
    duplicate_of = None

    if size <= 0:
        issues.append("grid_size must be a positive integer.")

    if not dots:
        issues.append("dots must contain at least one color pair.")

    seen_points: dict[tuple[int, int], int] = {}
    for dot in dots:
        if dot.p1 == dot.p2:
            issues.append(f"color {dot.color_id} uses the same endpoint twice.")
        for point in (dot.p1, dot.p2):
            if point[0] < 0 or point[0] >= size or point[1] < 0 or point[1] >= size:
                issues.append(f"endpoint {point} is outside the {size}x{size} grid.")
                continue
            if point in seen_points:
                issues.append(
                    f"endpoint {point} is reused by color {seen_points[point]} and {dot.color_id}."
                )
            else:
                seen_points[point] = dot.color_id

    if not issues and level_index is not None:
        duplicate_of = find_duplicate_level(size=size, dots=dots, level_index=level_index)
        if duplicate_of is not None:
            issues.append(f"duplicates existing level {duplicate_of}.")

    solve_result = None
    partial_solution_result = None
    if not issues:
        solve_result = solve_puzzle(size=size, dots=dots, solution_limit=2, completion_mode="full")
        if solve_result.status != "unique":
            issues.append(
                f"solver status is {solve_result.status} with {solve_result.solution_count} full-cover solution(s)."
            )
        elif not solve_result.solutions:
            issues.append("solver returned unique without a materialized full-cover solution.")
        else:
            solution = solve_result.solutions[0]
            min_path_cells = min(len(path_points) for path_points in solution.values())
            coverage_cells = len({point for path_points in solution.values() for point in path_points})
            if min_path_cells < MIN_PATH_CELLS:
                issues.append(
                    f"shortest solved path is {min_path_cells} cells; minimum is {MIN_PATH_CELLS}."
                )
            if coverage_cells != size * size:
                issues.append(
                    f"solution coverage is {coverage_cells}/{size * size}; expected full coverage."
                )

    if not issues:
        partial_solution_result = solve_puzzle(
            size=size,
            dots=dots,
            solution_limit=1,
            completion_mode="partial",
        )
        if partial_solution_result.solution_count > 0:
            issues.append("puzzle has a connect-all solution whose coverage is below 100%.")

    return LevelValidationReport(
        path=path,
        level_id=level_id,
        valid=not issues,
        issues=issues,
        solve_result=solve_result,
        duplicate_of=duplicate_of,
        partial_solution_result=partial_solution_result,
    )


def validate_level_collection(source: Path) -> list[LevelValidationReport]:
    files = iter_level_files(source)
    groups = build_level_groups([source])
    reports: list[LevelValidationReport] = []

    for level_file in files:
        definition = load_level_definition(level_file)
        if definition is None:
            reports.append(
                LevelValidationReport(
                    path=level_file,
                    level_id=level_file.stem,
                    valid=False,
                    issues=["failed to parse level JSON."],
                    solve_result=None,
                )
            )
            continue

        data, size, dots = definition
        level_id = str(data.get("level_id", level_file.stem))
        report = validate_puzzle(size=size, dots=dots, level_id=level_id, path=level_file)

        fingerprint = build_level_fingerprint(size=size, dots=dots)
        duplicate_paths = [
            candidate
            for candidate in groups.get(fingerprint, [])
            if candidate.resolve() != level_file.resolve()
        ]
        if duplicate_paths:
            report.valid = False
            report.duplicate_of = duplicate_paths[0]
            report.issues.append(f"duplicates sibling level {duplicate_paths[0]}.")

        reports.append(report)

    return reports
