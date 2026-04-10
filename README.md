# flowfreelike

Minimal Flow Free-like level generation pipeline built around four steps:

1. Generate a full-board solution path.
2. Shape the endpoint layout from that full-board solution, then extract only the endpoint pairs as the puzzle input.
3. Verify uniqueness with a backtracking solver and discard any multi-solution candidate.
4. Grade difficulty from a blind first-solution search and export JSON.

Generation now supports batch mode and pre-write validation. Only validated levels are written to the output directory. Duplicate detection still treats rotated or mirrored endpoint layouts as the same puzzle. Validation also enforces two gameplay constraints: every solved path must cover at least 3 cells including endpoints, and the final solution must cover 100% of the board. Solver metrics under `solver_*` now describe the blind grading pass, while the uniqueness check is recorded separately under `verification_solver_*`.

## Usage

Generate one validated level directly into `levels/`:

```bash
.venv\Scripts\python.exe main.py generate
```

Batch-generate 20 validated levels into `levels/`:

```bash
.venv\Scripts\python.exe main.py generate --count 20
```

Batch-generate with a reproducible seed and difficulty target:

```bash
.venv\Scripts\python.exe main.py generate --count 10 --size 5 --difficulty medium --seed 100
```

Write a single level to a custom file:

```bash
.venv\Scripts\python.exe main.py generate --output levels/0042.json
```

Print one validated level to stdout without writing a file:

```bash
.venv\Scripts\python.exe main.py generate --stdout
```

Validate the whole `levels/` directory:

```bash
.venv\Scripts\python.exe main.py validate
```

Validate a specific file or directory:

```bash
.venv\Scripts\python.exe main.py validate levels/0001.json
```

Low-level solver check for a single file:

```bash
.venv\Scripts\python.exe main.py solve levels/0001.json
```

Launch the manual level editor in your browser:

```bash
.venv\Scripts\python.exe main.py editor --size 6
```

Open an existing level in the editor and keep its file path as the default save target:

```bash
.venv\Scripts\python.exe main.py editor --input levels/0001.json
```

Start the editor server without opening a browser tab automatically:

```bash
.venv\Scripts\python.exe main.py editor --no-browser --port 8765
```

The editor now handles both preview and editing. It gives you a color palette, an interactive grid, direct path drawing, local file loading, and a “load next level / create next level” workflow. Each drawn line becomes one endpoint pair automatically, so you can hand-author a full solved board, validate it with the existing solver, and export JSON without leaving the repo.

## JSON format

```json
{
  "level_id": "0001",
  "grid_size": 5,
  "difficulty": "Easy",
  "dots": [
    {
      "color_id": 1,
      "p1": [0, 0],
      "p2": [2, 0]
    }
  ],
  "solution": [
    {
      "color_id": 1,
      "path": [[0, 0], [1, 0], [2, 0]]
    }
  ],
  "metrics": {
    "target_moves": 25,
    "color_count": 6,
    "existing_level_pool": 10,
    "duplicate_rejections": 2,
    "min_solution_path_cells": 3,
    "solution_coverage_ratio": 1.0,
    "segment_turns_total": 11,
    "segment_zero_turns": 1,
    "solver_backtracks": 0,
    "verification_solver_backtracks": 7
  }
}
```

`dots` uses coordinate pairs instead of a full board matrix, which keeps the level file small and easy to consume from Unity, Cocos, or a web frontend. Generated files now also embed one solved path per color under `solution`, so the editor can open an existing level with its solved board already visible and downstream tools can reuse it for hints.

Manual editor exports may also include an optional `palette` array with `color_id`, `name`, and `hex` values. Existing CLI commands ignore that extra metadata, while the editor uses it to restore your chosen colors when you reopen a file.
