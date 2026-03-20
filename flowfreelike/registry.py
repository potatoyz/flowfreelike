from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any, TypeAlias

from flowfreelike.geometry import GRID_SYMMETRIES
from flowfreelike.models import Dot, Point

NormalizedPair: TypeAlias = tuple[Point, Point]
LevelFingerprint: TypeAlias = tuple[int, tuple[NormalizedPair, ...]]


def build_level_groups(
    sources: Iterable[Path],
    exclude_paths: Iterable[Path] | None = None,
) -> dict[LevelFingerprint, list[Path]]:
    excluded = {path.resolve() for path in (exclude_paths or [])}
    groups: dict[LevelFingerprint, list[Path]] = {}

    for source in sources:
        for level_file in iter_level_files(source):
            resolved = level_file.resolve()
            if resolved in excluded:
                continue

            fingerprint = load_level_fingerprint(level_file)
            if fingerprint is None:
                continue

            groups.setdefault(fingerprint, []).append(resolved)

    return groups


def build_level_index(
    sources: Iterable[Path],
    exclude_paths: Iterable[Path] | None = None,
) -> dict[LevelFingerprint, Path]:
    groups = build_level_groups(sources=sources, exclude_paths=exclude_paths)
    return {
        fingerprint: paths[0]
        for fingerprint, paths in groups.items()
        if paths
    }


def find_duplicate_level(
    size: int,
    dots: list[Dot],
    level_index: dict[LevelFingerprint, Path],
) -> Path | None:
    return level_index.get(build_level_fingerprint(size=size, dots=dots))


def iter_level_files(source: Path) -> list[Path]:
    if not source.exists():
        return []
    if source.is_file():
        return [source]
    return sorted(path for path in source.rglob("*.json") if path.is_file())


def load_level_definition(path: Path) -> tuple[dict[str, Any], int, list[Dot]] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        size = int(data["grid_size"])
        dots = [
            Dot(
                color_id=int(item.get("color_id", index + 1)),
                p1=_as_point(item["p1"]),
                p2=_as_point(item["p2"]),
            )
            for index, item in enumerate(data["dots"])
        ]
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None

    return data, size, dots


def load_level_fingerprint(path: Path) -> LevelFingerprint | None:
    definition = load_level_definition(path)
    if definition is None:
        return None

    _, size, dots = definition
    return build_level_fingerprint(size=size, dots=dots)


def build_level_fingerprint(size: int, dots: list[Dot]) -> LevelFingerprint:
    signatures = []
    for transform in GRID_SYMMETRIES:
        pairs = [
            _normalize_pair(transform(dot.p1, size), transform(dot.p2, size))
            for dot in dots
        ]
        pairs.sort()
        signatures.append(tuple(pairs))

    return size, min(signatures)


def _normalize_pair(p1: Point, p2: Point) -> NormalizedPair:
    return (p1, p2) if p1 <= p2 else (p2, p1)


def _as_point(value: object) -> Point:
    if not isinstance(value, list | tuple) or len(value) != 2:
        raise ValueError("Point must be a two-item list or tuple.")
    return int(value[0]), int(value[1])
