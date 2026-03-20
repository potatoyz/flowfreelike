from __future__ import annotations

from typing import Callable

from flowfreelike.models import Point

GridTransform = Callable[[Point, int], Point]

GRID_SYMMETRIES: tuple[GridTransform, ...] = (
    lambda point, size: point,
    lambda point, size: (size - 1 - point[0], point[1]),
    lambda point, size: (point[0], size - 1 - point[1]),
    lambda point, size: (size - 1 - point[0], size - 1 - point[1]),
    lambda point, size: (point[1], point[0]),
    lambda point, size: (size - 1 - point[1], point[0]),
    lambda point, size: (point[1], size - 1 - point[0]),
    lambda point, size: (size - 1 - point[1], size - 1 - point[0]),
)
