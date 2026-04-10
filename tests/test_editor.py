from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from flowfreelike.editor import (
    analyze_editor_draft,
    build_default_palette,
    build_editor_bootstrap,
    load_next_or_create_editor_document,
)


class EditorAnalysisTests(unittest.TestCase):
    def test_full_cover_draft_builds_level_and_solution(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            draft = {
                "size": 3,
                "level_id": "0001",
                "difficulty": "auto",
                "output_path": "levels/0001.json",
                "palette": build_default_palette(3),
                "paths": [
                    {"color_id": 1, "cells": [[0, 0], [0, 1], [0, 2]]},
                    {"color_id": 2, "cells": [[1, 0], [1, 1], [1, 2]]},
                    {"color_id": 3, "cells": [[2, 0], [2, 1], [2, 2]]},
                ],
            }

            analysis = analyze_editor_draft(draft, cwd=base, levels_dir=base / "levels")

            self.assertEqual(analysis["structural_issues"], [])
            self.assertTrue(analysis["validation"]["valid"])
            self.assertEqual(analysis["summary"]["coverage_cells"], 9)
            self.assertEqual(analysis["level"]["grid_size"], 3)
            self.assertIn("solution", analysis["level"])
            self.assertEqual(len(analysis["level"]["palette"]), 3)

    def test_non_adjacent_path_is_reported_as_structural_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            draft = {
                "size": 5,
                "level_id": "broken_path",
                "difficulty": "auto",
                "output_path": "levels/0002.json",
                "palette": build_default_palette(1),
                "paths": [
                    {"color_id": 1, "cells": [[0, 0], [2, 0]]},
                ],
            }

            analysis = analyze_editor_draft(draft, cwd=base, levels_dir=base / "levels")

            self.assertFalse(analysis["validation"])
            self.assertIsNone(analysis["level"])
            self.assertIn("non-adjacent", analysis["structural_issues"][0])

    def test_source_path_is_excluded_from_duplicate_detection(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            levels_dir = base / "levels"
            levels_dir.mkdir(parents=True, exist_ok=True)
            source_path = levels_dir / "0001.json"
            level_data = {
                "level_id": "0001",
                "grid_size": 3,
                "difficulty": "Easy",
                "dots": [
                    {"color_id": 1, "p1": [0, 0], "p2": [0, 2]},
                    {"color_id": 2, "p1": [1, 0], "p2": [1, 2]},
                    {"color_id": 3, "p1": [2, 0], "p2": [2, 2]},
                ],
                "solution": [
                    {"color_id": 1, "path": [[0, 0], [0, 1], [0, 2]]},
                    {"color_id": 2, "path": [[1, 0], [1, 1], [1, 2]]},
                    {"color_id": 3, "path": [[2, 0], [2, 1], [2, 2]]},
                ],
                "metrics": {"target_moves": 9, "color_count": 3},
                "palette": build_default_palette(3),
            }
            source_path.write_text(json.dumps(level_data, indent=2), encoding="utf-8")

            draft = {
                "size": 3,
                "level_id": "0002",
                "difficulty": "auto",
                "output_path": "levels/0002.json",
                "source_path": "levels/0001.json",
                "palette": build_default_palette(3),
                "paths": [
                    {"color_id": 1, "cells": [[0, 0], [0, 1], [0, 2]]},
                    {"color_id": 2, "cells": [[1, 0], [1, 1], [1, 2]]},
                    {"color_id": 3, "cells": [[2, 0], [2, 1], [2, 2]]},
                ],
            }

            analysis = analyze_editor_draft(draft, cwd=base, levels_dir=levels_dir)

            self.assertTrue(analysis["validation"]["valid"])

    def test_blank_bootstrap_uses_next_numeric_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            levels_dir = base / "levels"
            levels_dir.mkdir(parents=True, exist_ok=True)
            (levels_dir / "0001.json").write_text("{}", encoding="utf-8")
            (levels_dir / "0002.json").write_text("{}", encoding="utf-8")

            bootstrap = build_editor_bootstrap(
                size=5,
                input_path=None,
                output_path=None,
                levels_dir=levels_dir,
                cwd=base,
            )

            self.assertEqual(bootstrap["output_path"], "levels\\0003.json" if "\\" in bootstrap["output_path"] else "levels/0003.json")
            self.assertEqual(bootstrap["level_id"], "0003")

    def test_loading_existing_level_uses_solver_fallback_when_solution_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            levels_dir = base / "levels"
            levels_dir.mkdir(parents=True, exist_ok=True)
            source_path = levels_dir / "0001.json"
            level_data = {
                "level_id": "0001",
                "grid_size": 3,
                "difficulty": "Easy",
                "dots": [
                    {"color_id": 1, "p1": [0, 0], "p2": [0, 2]},
                    {"color_id": 2, "p1": [1, 0], "p2": [1, 2]},
                    {"color_id": 3, "p1": [2, 0], "p2": [2, 2]},
                ],
                "metrics": {"target_moves": 9, "color_count": 3},
            }
            source_path.write_text(json.dumps(level_data, indent=2), encoding="utf-8")

            bootstrap = build_editor_bootstrap(
                size=5,
                input_path=source_path,
                output_path=None,
                levels_dir=levels_dir,
                cwd=base,
            )

            self.assertEqual(len(bootstrap["paths"]), 3)
            self.assertIn("solver fallback", bootstrap["notice"].lower())

    def test_next_level_loads_existing_file_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            levels_dir = base / "levels"
            levels_dir.mkdir(parents=True, exist_ok=True)

            for number in (1, 2):
                path = levels_dir / f"{number:04d}.json"
                data = {
                    "level_id": f"{number:04d}",
                    "grid_size": 3,
                    "difficulty": "Easy",
                    "dots": [
                        {"color_id": 1, "p1": [0, 0], "p2": [0, 2]},
                        {"color_id": 2, "p1": [1, 0], "p2": [1, 2]},
                        {"color_id": 3, "p1": [2, 0], "p2": [2, 2]},
                    ],
                    "solution": [
                        {"color_id": 1, "path": [[0, 0], [0, 1], [0, 2]]},
                        {"color_id": 2, "path": [[1, 0], [1, 1], [1, 2]]},
                        {"color_id": 3, "path": [[2, 0], [2, 1], [2, 2]]},
                    ],
                    "metrics": {"target_moves": 9, "color_count": 3},
                    "palette": build_default_palette(3),
                }
                path.write_text(json.dumps(data, indent=2), encoding="utf-8")

            payload = {
                "size": 3,
                "level_id": "0001",
                "difficulty": "auto",
                "output_path": "levels/0001.json",
                "source_path": "levels/0001.json",
                "palette": build_default_palette(3),
                "paths": [],
            }

            bootstrap = load_next_or_create_editor_document(payload, levels_dir=levels_dir, cwd=base)

            self.assertEqual(bootstrap["output_path"], "levels\\0002.json" if "\\" in bootstrap["output_path"] else "levels/0002.json")
            self.assertEqual(bootstrap["level_id"], "0002")
            self.assertTrue(bootstrap["paths"])

    def test_next_level_creates_blank_numeric_draft_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            base = Path(temp_dir)
            levels_dir = base / "levels"
            levels_dir.mkdir(parents=True, exist_ok=True)

            payload = {
                "size": 6,
                "level_id": "0004",
                "difficulty": "Hard",
                "output_path": "levels/0004.json",
                "source_path": "levels/0004.json",
                "palette": build_default_palette(2),
                "paths": [],
            }

            bootstrap = load_next_or_create_editor_document(payload, levels_dir=levels_dir, cwd=base)

            self.assertEqual(bootstrap["output_path"], "levels\\0005.json" if "\\" in bootstrap["output_path"] else "levels/0005.json")
            self.assertEqual(bootstrap["level_id"], "0005")
            self.assertEqual(bootstrap["size"], 6)
            self.assertEqual(len(bootstrap["palette"]), 2)
            self.assertEqual(bootstrap["paths"], [])


if __name__ == "__main__":
    unittest.main()
