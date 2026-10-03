from collections.abc import Iterable

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from habitat.grid import Grid, parse_cell_id, transformer

SIMPLIFY_TOLERANCE_DEGREES = 0.005


def footprint_from_cells(grid: Grid, cell_ids: Iterable[str]) -> BaseGeometry | None:
    """Union of the cells in WGS84. The union runs in the grid CRS, where the cells are aligned squares."""
    parsed = np.array([parse_cell_id(cell_id) for cell_id in cell_ids])
    if parsed.size == 0:
        return None

    rows, cols = parsed[:, 0], parsed[:, 1]
    x0 = grid.origin_x_m + cols * grid.cell_size_m
    y1 = grid.origin_y_m - rows * grid.cell_size_m
    cells = shapely.box(x0, y1 - grid.cell_size_m, x0 + grid.cell_size_m, y1)
    union = shapely.union_all(cells)

    wgs84 = transform_geometry(transformer(grid.crs, "EPSG:4326").transform, union)
    return wgs84.simplify(SIMPLIFY_TOLERANCE_DEGREES, preserve_topology=True)


def area_share(part: BaseGeometry, whole: BaseGeometry) -> float:
    """Share of `whole` covered by `part`. Areas are in square degrees, which is good enough for ranking."""
    if whole.area == 0:
        return 1.0 if part.intersects(whole) else 0.0

    return part.intersection(whole).area / whole.area


def grows_materially(previous: BaseGeometry | None, current: BaseGeometry | None, threshold: float = 0.10) -> bool:
    if previous is None or current is None:
        return previous is not current

    return current.difference(previous).area > threshold * previous.area
