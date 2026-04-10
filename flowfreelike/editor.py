from __future__ import annotations

import json
import re
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from flowfreelike.models import Dot, Solution, solution_from_dict, solution_to_dict
from flowfreelike.registry import build_level_index, iter_level_files, load_level_definition
from flowfreelike.solver import solve_puzzle
from flowfreelike.validation import MIN_PATH_CELLS, validate_puzzle

DEFAULT_EDITOR_PORT = 0
DEFAULT_EDITOR_HOST = "127.0.0.1"
DEFAULT_LEVELS_DIR = Path("levels")
DEFAULT_EDITOR_COLOR_COUNT = 4
MIN_EDITOR_SIZE = 3
MAX_EDITOR_SIZE = 14
HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
PURE_LEVEL_NUMBER_PATTERN = re.compile(r"^(\d+)$")
LEGACY_LEVEL_NUMBER_PATTERN = re.compile(r"^lvl_(\d+)(?:_|$)")
DEFAULT_LEVEL_NUMBER_WIDTH = 4

DEFAULT_EDITOR_PALETTE = (
    {"name": "Sunset", "hex": "#ef6c57"},
    {"name": "Lagoon", "hex": "#2d7ff9"},
    {"name": "Citrus", "hex": "#e3b505"},
    {"name": "Mint", "hex": "#2aa876"},
    {"name": "Plum", "hex": "#8b5cf6"},
    {"name": "Berry", "hex": "#d94680"},
    {"name": "Slate", "hex": "#475569"},
    {"name": "Copper", "hex": "#c56a2d"},
)


def launch_editor(
    *,
    size: int = 5,
    input_path: Path | None = None,
    output_path: Path | None = None,
    levels_dir: Path = DEFAULT_LEVELS_DIR,
    host: str = DEFAULT_EDITOR_HOST,
    port: int = DEFAULT_EDITOR_PORT,
    open_browser: bool = True,
) -> None:
    if not MIN_EDITOR_SIZE <= size <= MAX_EDITOR_SIZE:
        raise ValueError(f"editor size must be between {MIN_EDITOR_SIZE} and {MAX_EDITOR_SIZE}.")

    cwd = Path.cwd()
    resolved_input = _resolve_path(input_path, cwd)
    resolved_levels_dir = _resolve_path(levels_dir, cwd) or (cwd / DEFAULT_LEVELS_DIR)
    bootstrap = build_editor_bootstrap(
        size=size,
        input_path=resolved_input,
        output_path=output_path,
        levels_dir=resolved_levels_dir,
        cwd=cwd,
    )

    handler = _build_editor_handler(bootstrap=bootstrap, levels_dir=resolved_levels_dir, cwd=cwd)
    with ThreadingHTTPServer((host, port), handler) as server:
        address, actual_port = server.server_address[:2]
        url = f"http://{address}:{actual_port}/"
        print(f"Manual level editor running at {url}")
        print("Press Ctrl+C to stop the editor server.")
        if open_browser:
            threading.Timer(0.2, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\nEditor stopped.")


def format_level_number(level_number: int) -> str:
    return f"{level_number:0{DEFAULT_LEVEL_NUMBER_WIDTH}d}"


def build_level_filename(level_number: int) -> str:
    return f"{format_level_number(level_number)}.json"


def parse_level_number(value: str | Path | None) -> int | None:
    if value is None:
        return None

    if isinstance(value, Path):
        stem = value.stem
    else:
        text = str(value).strip()
        if not text:
            return None
        stem = Path(text).stem if text.endswith(".json") or "/" in text or "\\" in text else text

    for pattern in (PURE_LEVEL_NUMBER_PATTERN, LEGACY_LEVEL_NUMBER_PATTERN):
        match = pattern.match(stem)
        if match:
            return int(match.group(1))
    return None


def find_max_level_number(levels_dir: Path) -> int:
    numbers = [
        number
        for path in iter_level_files(levels_dir)
        if (number := parse_level_number(path)) is not None
    ]
    return max(numbers, default=0)


def build_level_path(levels_dir: Path, level_number: int) -> Path:
    return levels_dir / build_level_filename(level_number)


def find_next_level_number(levels_dir: Path, current_number: int | None = None) -> int:
    if current_number is not None:
        return current_number + 1
    return find_max_level_number(levels_dir) + 1


def build_editor_bootstrap(
    *,
    size: int,
    input_path: Path | None,
    output_path: Path | None,
    levels_dir: Path,
    cwd: Path,
    template_palette: list[dict[str, Any]] | None = None,
    template_difficulty: str | None = None,
    template_notice: str | None = None,
) -> dict[str, Any]:
    palette = _normalize_palette(template_palette) if template_palette else build_default_palette(DEFAULT_EDITOR_COLOR_COUNT)
    paths: list[dict[str, Any]] = []
    notice: str | None = template_notice
    resolved_output = _resolve_path(output_path, cwd)
    if resolved_output is None and input_path is None:
        resolved_output = build_level_path(levels_dir, find_next_level_number(levels_dir))

    level_id = _default_level_id(output_path=resolved_output, input_path=input_path, size=size, cwd=cwd)
    difficulty = _normalize_editor_difficulty(template_difficulty, fallback="auto")
    output_label = _default_output_label(
        output_path=resolved_output,
        input_path=input_path,
        size=size,
        cwd=cwd,
        levels_dir=levels_dir,
    )
    source_label = _to_display_path(input_path, cwd) if input_path is not None else ""

    if input_path is not None:
        definition = load_level_definition(input_path)
        if definition is None:
            raise ValueError(f"failed to parse level JSON: {input_path}")
        data, size, dots = definition
        palette = _load_palette_from_data(data=data, dot_count=max(len(dots), DEFAULT_EDITOR_COLOR_COUNT))
        level_id = str(data.get("level_id", _default_level_id(input_path=input_path, output_path=input_path, size=size, cwd=cwd)))
        difficulty = _normalize_editor_difficulty(data.get("difficulty"), fallback="auto")
        paths, notice = _load_solution_paths_from_data(data=data, size=size, dots=dots)
        if resolved_output is None:
            output_label = _to_display_path(input_path, cwd)

    draft = {
        "size": size,
        "level_id": level_id,
        "difficulty": difficulty,
        "output_path": output_label,
        "source_path": source_label,
        "palette": palette,
        "paths": paths,
    }
    return {
        **draft,
        "notice": notice,
        "analysis": analyze_editor_draft(
            draft,
            cwd=cwd,
            levels_dir=levels_dir,
            default_output_path=output_label,
        ),
    }


def analyze_editor_draft(
    payload: dict[str, Any],
    *,
    cwd: Path | None = None,
    levels_dir: Path | None = None,
    default_output_path: str | None = None,
) -> dict[str, Any]:
    base_dir = cwd or Path.cwd()
    levels_root = _resolve_path(levels_dir, base_dir) or (base_dir / DEFAULT_LEVELS_DIR)

    size = int(payload.get("size", 5))
    difficulty_request = _normalize_editor_difficulty(payload.get("difficulty"), fallback="auto")
    output_label = str(
        payload.get("output_path")
        or default_output_path
        or _default_output_label(size=size, cwd=base_dir, levels_dir=levels_root)
    )
    source_label = str(payload.get("source_path") or "").strip()
    level_id = str(
        payload.get("level_id")
        or _default_level_id(
            output_path=_resolve_user_output_path(output_label, cwd=base_dir),
            input_path=_resolve_user_output_path(source_label, cwd=base_dir),
            size=size,
            cwd=base_dir,
        )
    ).strip()

    palette = _normalize_palette(payload.get("palette"))
    path_entries = _normalize_path_entries(payload.get("paths"))
    structural_issues, normalized_paths, coverage_cells = _validate_path_entries(path_entries=path_entries, size=size)
    summary = {
        "size": size,
        "path_count": len(normalized_paths),
        "coverage_cells": coverage_cells,
        "coverage_ratio": round(coverage_cells / (size * size), 3) if size > 0 else 0.0,
        "total_cells": size * size,
        "min_path_cells": min((len(path) for _, path in normalized_paths), default=0),
    }

    if not normalized_paths or structural_issues:
        return {
            "structural_issues": structural_issues,
            "validation": None,
            "level": None,
            "json_text": "",
            "summary": summary,
            "output_path": output_label,
            "source_path": source_label,
        }

    dots = [Dot(color_id=color_id, p1=path[0], p2=path[-1]) for color_id, path in normalized_paths]
    manual_solution: Solution = {color_id: path for color_id, path in normalized_paths}
    embedded_solution = None
    if coverage_cells == size * size and summary["min_path_cells"] >= MIN_PATH_CELLS:
        embedded_solution = manual_solution

    save_target = _resolve_user_output_path(output_label, cwd=base_dir)
    source_target = _resolve_user_output_path(source_label, cwd=base_dir)
    exclude_paths = [
        path
        for path in (save_target, source_target)
        if path is not None and path.exists()
    ]
    level_index = build_level_index([levels_root], exclude_paths=exclude_paths)
    report = validate_puzzle(size=size, dots=dots, level_id=level_id, level_index=level_index)

    final_difficulty = difficulty_request
    if difficulty_request == "auto":
        final_difficulty = _estimate_difficulty(report.to_dict())

    metrics = {
        "target_moves": size * size,
        "color_count": len(dots),
        "generation_mode": "manual_editor",
        "editor_path_count": len(normalized_paths),
        "editor_coverage_cells": coverage_cells,
        "editor_coverage_ratio": summary["coverage_ratio"],
        "editor_manual_solution_embedded": embedded_solution is not None,
    }
    if report.solve_result is not None:
        metrics.update(report.solve_result.stats.to_dict())
    if embedded_solution is not None:
        metrics["min_solution_path_cells"] = summary["min_path_cells"]
        metrics["solution_coverage_cells"] = coverage_cells
        metrics["solution_coverage_ratio"] = summary["coverage_ratio"]

    level_data: dict[str, Any] = {
        "level_id": level_id,
        "grid_size": size,
        "difficulty": final_difficulty,
        "dots": [dot.to_dict() for dot in dots],
        "metrics": metrics,
        "palette": [
            item
            for item in palette
            if any(color_id == item["color_id"] for color_id, _ in normalized_paths)
        ],
    }
    if embedded_solution is not None:
        level_data["solution"] = solution_to_dict(embedded_solution)

    return {
        "structural_issues": [],
        "validation": report.to_dict(),
        "level": level_data,
        "json_text": json.dumps(level_data, indent=2, ensure_ascii=False),
        "summary": summary,
        "output_path": output_label,
        "source_path": source_label,
    }


def build_default_palette(count: int) -> list[dict[str, Any]]:
    palette: list[dict[str, Any]] = []
    for index in range(count):
        sample = DEFAULT_EDITOR_PALETTE[index % len(DEFAULT_EDITOR_PALETTE)]
        palette.append({"color_id": index + 1, "name": sample["name"], "hex": sample["hex"]})
    return palette


def _normalize_palette(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return build_default_palette(DEFAULT_EDITOR_COLOR_COUNT)

    normalized: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        color_id = int(item.get("color_id", index + 1))
        if color_id in seen_ids:
            continue
        seen_ids.add(color_id)
        color_hex = _normalize_hex_color(item.get("hex"), fallback=DEFAULT_EDITOR_PALETTE[index % len(DEFAULT_EDITOR_PALETTE)]["hex"])
        name = str(item.get("name") or f"Color {color_id}").strip() or f"Color {color_id}"
        normalized.append({"color_id": color_id, "name": name, "hex": color_hex})
    return sorted(normalized, key=lambda item: item["color_id"]) or build_default_palette(DEFAULT_EDITOR_COLOR_COUNT)


def _normalize_path_entries(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            continue
        raw_cells = item.get("cells", [])
        if not isinstance(raw_cells, list):
            continue
        try:
            cells = [(int(point[0]), int(point[1])) for point in raw_cells]
        except (TypeError, ValueError, IndexError):
            continue
        entries.append({"color_id": int(item.get("color_id", index + 1)), "cells": cells})
    return entries


def _validate_path_entries(
    *,
    path_entries: list[dict[str, Any]],
    size: int,
) -> tuple[list[str], list[tuple[int, tuple[tuple[int, int], ...]]], int]:
    issues: list[str] = []
    occupied: dict[tuple[int, int], int] = {}
    normalized_paths: list[tuple[int, tuple[tuple[int, int], ...]]] = []
    seen_colors: set[int] = set()

    if not MIN_EDITOR_SIZE <= size <= MAX_EDITOR_SIZE:
        issues.append(f"grid size must be between {MIN_EDITOR_SIZE} and {MAX_EDITOR_SIZE}.")

    for item in sorted(path_entries, key=lambda candidate: candidate["color_id"]):
        color_id = item["color_id"]
        cells = item["cells"]
        if color_id in seen_colors:
            issues.append(f"color {color_id} appears more than once.")
            continue
        seen_colors.add(color_id)
        if len(cells) < 2:
            issues.append(f"color {color_id} must contain at least 2 cells.")
            continue

        seen_in_path: set[tuple[int, int]] = set()
        previous = None
        for cell in cells:
            x, y = cell
            if not (0 <= x < size and 0 <= y < size):
                issues.append(f"color {color_id} uses out-of-bounds cell {cell}.")
                break
            if cell in seen_in_path:
                issues.append(f"color {color_id} repeats cell {cell}.")
                break
            owner = occupied.get(cell)
            if owner is not None and owner != color_id:
                issues.append(f"cell {cell} is shared by color {owner} and color {color_id}.")
                break
            if previous is not None and abs(cell[0] - previous[0]) + abs(cell[1] - previous[1]) != 1:
                issues.append(f"color {color_id} has a non-adjacent step at {previous} -> {cell}.")
                break
            occupied[cell] = color_id
            seen_in_path.add(cell)
            previous = cell
        else:
            normalized_paths.append((color_id, tuple(cells)))

    return issues, normalized_paths, len(occupied)


def _load_palette_from_data(data: dict[str, Any], dot_count: int) -> list[dict[str, Any]]:
    raw_palette = data.get("palette")
    if isinstance(raw_palette, list):
        palette = _normalize_palette(raw_palette)
        if palette:
            return palette
    return build_default_palette(max(dot_count, DEFAULT_EDITOR_COLOR_COUNT))


def _load_solution_paths_from_data(
    *,
    data: dict[str, Any],
    size: int,
    dots: list[Dot],
) -> tuple[list[dict[str, Any]], str | None]:
    embedded_issue = None
    try:
        solution = solution_from_dict(data.get("solution"))
    except ValueError as exc:
        solution = None
        embedded_issue = str(exc)

    if solution is not None:
        normalized_solution, embedded_issue = _normalize_loaded_solution(size=size, dots=dots, solution=solution)
        if normalized_solution is not None:
            return [
                {"color_id": color_id, "cells": [list(point) for point in path]}
                for color_id, path in sorted(normalized_solution.items())
            ], None

    result = solve_puzzle(size=size, dots=dots, solution_limit=1, completion_mode="full")
    if result.is_unique and result.solutions:
        normalized_solution, solver_issue = _normalize_loaded_solution(
            size=size,
            dots=dots,
            solution=result.solutions[0],
        )
        if normalized_solution is not None:
            notice = "Loaded solved board from solver fallback."
            if embedded_issue:
                notice = f"Embedded solution was ignored ({embedded_issue}); loaded solver fallback instead."
            return [
                {"color_id": color_id, "cells": [list(point) for point in path]}
                for color_id, path in sorted(normalized_solution.items())
            ], notice
        embedded_issue = solver_issue or embedded_issue

    if embedded_issue:
        return [], f"Embedded solution was ignored ({embedded_issue})."
    return [], "This level does not embed a reusable solution, so the editor starts from a blank board."


def _normalize_loaded_solution(
    *,
    size: int,
    dots: list[Dot],
    solution: Solution,
) -> tuple[Solution | None, str | None]:
    dot_by_color = {dot.color_id: dot for dot in dots}
    if set(solution) != set(dot_by_color):
        return None, "solution colors do not match the stored dots"

    occupied: set[tuple[int, int]] = set()
    normalized: Solution = {}
    for color_id, dot in dot_by_color.items():
        path = solution[color_id]
        if path[0] == dot.p2 and path[-1] == dot.p1:
            path = tuple(reversed(path))
        elif path[0] != dot.p1 or path[-1] != dot.p2:
            return None, f"solution endpoints for color {color_id} do not match"

        seen_in_path: set[tuple[int, int]] = set()
        previous = None
        for point in path:
            if not (0 <= point[0] < size and 0 <= point[1] < size):
                return None, f"solution uses out-of-bounds point {point}"
            if point in seen_in_path:
                return None, f"solution repeats point {point}"
            if point in occupied:
                return None, f"solution overlaps at point {point}"
            if previous is not None and abs(point[0] - previous[0]) + abs(point[1] - previous[1]) != 1:
                return None, f"solution has a non-adjacent step in color {color_id}"
            occupied.add(point)
            seen_in_path.add(point)
            previous = point
        normalized[color_id] = path

    if len(occupied) != size * size:
        return None, f"solution covers {len(occupied)}/{size * size} cells"
    return normalized, None


def _estimate_difficulty(report: dict[str, Any]) -> str:
    if not report.get("valid"):
        return "Custom"
    metrics = report.get("metrics") or {}
    backtracks = int(metrics.get("solver_backtracks", 0))
    branch_points = int(metrics.get("solver_branch_points", 0))
    ratio = float(metrics.get("forced_move_ratio", 1.0))
    score = backtracks + branch_points * 3 + max(0.0, 0.65 - ratio) * 20
    if score < 12:
        return "Easy"
    if score < 45:
        return "Medium"
    return "Hard"


def _normalize_editor_difficulty(value: object, *, fallback: str) -> str:
    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    lower = text.lower()
    if lower in {"auto", "easy", "medium", "hard", "custom"}:
        return lower
    return text.title()


def _normalize_hex_color(value: object, *, fallback: str) -> str:
    text = str(value or "").strip()
    return text.lower() if HEX_COLOR_PATTERN.match(text) else fallback.lower()


def _default_level_id(
    *,
    output_path: Path | None = None,
    input_path: Path | None = None,
    size: int = 5,
    cwd: Path | None = None,
) -> str:
    target = output_path or input_path
    if target is not None:
        if (level_number := parse_level_number(target)) is not None:
            return format_level_number(level_number)
        return target.stem
    return f"custom_{size}x{size}"


def _default_output_label(
    *,
    output_path: Path | None = None,
    input_path: Path | None = None,
    size: int = 5,
    cwd: Path | None = None,
    levels_dir: Path | None = None,
) -> str:
    base_dir = cwd or Path.cwd()
    levels_root = _resolve_path(levels_dir, base_dir) or (base_dir / DEFAULT_LEVELS_DIR)
    target = output_path or input_path
    if target is None:
        target = build_level_path(levels_root, find_next_level_number(levels_root))
    resolved = _resolve_path(target, base_dir) or (base_dir / target)
    return _to_display_path(resolved, base_dir)


def _resolve_user_output_path(value: str | None, *, cwd: Path) -> Path | None:
    if not value:
        return None
    target = Path(value)
    return target if target.is_absolute() else (cwd / target).resolve()


def _resolve_path(path: Path | None, cwd: Path) -> Path | None:
    if path is None:
        return None
    return path if path.is_absolute() else (cwd / path).resolve()


def _to_display_path(path: Path, cwd: Path) -> str:
    try:
        return str(path.relative_to(cwd))
    except ValueError:
        return str(path)


def load_editor_document_from_path(
    *,
    target_path: Path,
    levels_dir: Path,
    cwd: Path,
) -> dict[str, Any]:
    if not target_path.exists():
        raise FileNotFoundError(target_path)
    return build_editor_bootstrap(
        size=5,
        input_path=target_path,
        output_path=None,
        levels_dir=levels_dir,
        cwd=cwd,
    )


def load_next_or_create_editor_document(
    payload: dict[str, Any],
    *,
    levels_dir: Path,
    cwd: Path,
) -> dict[str, Any]:
    current_reference = _resolve_user_output_path(
        str(payload.get("output_path") or payload.get("source_path") or ""),
        cwd=cwd,
    )
    current_number = parse_level_number(current_reference)
    next_number = find_next_level_number(levels_dir, current_number=current_number)
    next_path = build_level_path(levels_dir, next_number)

    if next_path.exists():
        return load_editor_document_from_path(target_path=next_path, levels_dir=levels_dir, cwd=cwd)

    return build_editor_bootstrap(
        size=int(payload.get("size", 5)),
        input_path=None,
        output_path=next_path,
        levels_dir=levels_dir,
        cwd=cwd,
        template_palette=_normalize_palette(payload.get("palette")),
        template_difficulty=str(payload.get("difficulty") or "auto"),
        template_notice=f"Created draft for level {format_level_number(next_number)}.",
    )


def _build_editor_handler(
    *,
    bootstrap: dict[str, Any],
    levels_dir: Path,
    cwd: Path,
):
    class EditorHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path in {"/", "/index.html"}:
                self._send_html(_render_editor_page(bootstrap))
                return
            if self.path == "/api/bootstrap":
                self._send_json(bootstrap)
                return
            if self.path == "/favicon.ico":
                self.send_response(HTTPStatus.NO_CONTENT)
                self.end_headers()
                return
            self.send_error(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {"/api/validate", "/api/save", "/api/load", "/api/next-level"}:
                self.send_error(HTTPStatus.NOT_FOUND)
                return

            payload = self._read_json_body()
            if self.path == "/api/load":
                target_label = str(payload.get("target_path") or payload.get("output_path") or "").strip()
                target_path = _resolve_user_output_path(target_label, cwd=cwd)
                if target_path is None or not target_path.exists():
                    self._send_json(
                        {"error": f"Level file not found: {target_label or '(empty path)'}"},
                        status=HTTPStatus.NOT_FOUND,
                    )
                    return
                try:
                    document = load_editor_document_from_path(target_path=target_path, levels_dir=levels_dir, cwd=cwd)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(document)
                return

            if self.path == "/api/next-level":
                try:
                    document = load_next_or_create_editor_document(payload, levels_dir=levels_dir, cwd=cwd)
                except ValueError as exc:
                    self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
                    return
                self._send_json(document)
                return

            analysis = analyze_editor_draft(
                payload,
                cwd=cwd,
                levels_dir=levels_dir,
                default_output_path=bootstrap["output_path"],
            )
            if self.path == "/api/save":
                if analysis["level"] is None:
                    self._send_json(
                        {**analysis, "error": "Current draft has structural issues and cannot be saved yet."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
                save_path = _resolve_user_output_path(str(analysis["output_path"]), cwd=cwd)
                if save_path is None:
                    self._send_json({"error": "Missing output path."}, status=HTTPStatus.BAD_REQUEST)
                    return
                save_path.parent.mkdir(parents=True, exist_ok=True)
                save_path.write_text(analysis["json_text"], encoding="utf-8")
                analysis = {
                    **analysis,
                    "saved_path": str(save_path),
                    "source_path": _to_display_path(save_path, cwd),
                }
            self._send_json(analysis)

        def log_message(self, format: str, *args: object) -> None:
            return

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else "{}"
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("JSON request body must be an object.")
            return parsed

        def _send_html(self, html: str) -> None:
            encoded = html.encode("utf-8")
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def _send_json(self, payload: dict[str, Any], *, status: HTTPStatus = HTTPStatus.OK) -> None:
            encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    return EditorHandler


def _render_editor_page(bootstrap: dict[str, Any]) -> str:
    template_path = Path(__file__).with_name("editor_page.html")
    template = template_path.read_text(encoding="utf-8")
    return template.replace("__BOOTSTRAP_JSON__", json.dumps(bootstrap, ensure_ascii=False))
