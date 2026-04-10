# flowfreelike

Minimal Flow Free-like level generation pipeline built around four steps:

1. Generate a full-board solution path.
2. Extract only the endpoint pairs as the puzzle input.
3. Verify uniqueness with a backtracking solver.
4. Grade difficulty from solver search metrics and export JSON.

Generation now supports batch mode and pre-write validation. Only validated levels are written to the output directory. Duplicate detection still treats rotated or mirrored endpoint layouts as the same puzzle. Validation also enforces two gameplay constraints: every solved path must cover at least 3 cells including endpoints, and the final solution must cover 100% of the board. The path generator now randomizes the full-cover walk with backbite steps and prefers partitions with more turns and fewer long straight runs, so generated levels are less rigid than the original serpentine output.

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
.venv\Scripts\python.exe main.py generate --output levels/custom_level.json
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
.venv\Scripts\python.exe main.py validate levels/lvl_0001_5x5_easy.json
```

Low-level solver check for a single file:

```bash
.venv\Scripts\python.exe main.py solve levels/lvl_0001_5x5_easy.json
```

Preview a single file in the terminal:

```bash
.venv\Scripts\python.exe main.py preview levels/lvl_0001_5x5_easy.json
```

## JSON format

```json
{
  "level_id": "lvl_0001_5x5_easy",
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
    "solver_backtracks": 0
  }
}
```

`dots` uses coordinate pairs instead of a full board matrix, which keeps the level file small and easy to consume from Unity, Cocos, or a web frontend. Generated files now also embed one solved path per color under `solution`, so the CLI can preview the finished board and downstream tools can reuse it for hints.
