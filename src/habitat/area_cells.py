import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry
from shapely.ops import transform as transform_geometry

from habitat.grid import Grid, transformer


def area_cell_overlaps(grid: Grid, geometry: BaseGeometry) -> list[tuple[str, float]]:
    """The grid cells of an area, with the share of each cell inside the area.

    The shares are computed in the equal-area grid CRS. A point area gets the one cell that contains it, with share 1.
    """
    if geometry is None or geometry.is_empty:
        return []

    projected = transform_geometry(transformer("EPSG:4326", grid.crs).transform, geometry)
    if projected.area == 0:
        return point_cells(grid, projected)

    west, south, east, north = projected.bounds
    (row_min, row_max), (col_min, col_max) = grid.rows_cols_from_xy(np.array([west, east]), np.array([north, south]))
    rows, cols = np.meshgrid(
        np.arange(max(row_min, 0), min(row_max, grid.rows - 1) + 1),
        np.arange(max(col_min, 0), min(col_max, grid.columns - 1) + 1),
        indexing="ij",
    )
    rows, cols = rows.ravel(), cols.ravel()
    x0 = grid.origin_x_m + cols * grid.cell_size_m
    y1 = grid.origin_y_m - rows * grid.cell_size_m
    cells = shapely.box(x0, y1 - grid.cell_size_m, x0 + grid.cell_size_m, y1)

    shapely.prepare(projected)
    shares = np.where(shapely.contains(projected, cells), 1.0, 0.0)
    edge = ~(shares > 0) & shapely.intersects(projected, cells)
    shares[edge] = shapely.area(shapely.intersection(cells[edge], projected)) / grid.cell_size_m**2

    overlapping = shares > 0
    cell_ids = grid.cell_ids(rows[overlapping], cols[overlapping])
    return [(str(cell_id), float(share)) for cell_id, share in zip(cell_ids, shares[overlapping])]


def point_cells(grid: Grid, projected: BaseGeometry) -> list[tuple[str, float]]:
    points = shapely.get_coordinates(projected)
    rows, cols = grid.rows_cols_from_xy(points[:, 0], points[:, 1])
    return [(str(cell_id), 1.0) for cell_id in dict.fromkeys(grid.cell_ids(rows, cols))]
