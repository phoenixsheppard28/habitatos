import re
from dataclasses import dataclass
from functools import cache

import numpy as np
from pyproj import Transformer
from shapely.geometry import Polygon, box
from shapely.ops import transform as transform_geometry

from habitat.contracts import BBox, load_contract

CELL_ID_PATTERN = re.compile(r"^E1K-r(\d+)-c(\d+)$")


@dataclass(frozen=True)
class Grid:
    grid_id: str
    crs: str
    cell_size_m: float
    columns: int
    rows: int
    origin_x_m: float
    origin_y_m: float

    def rows_cols_from_xy(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        cols = np.floor((x - self.origin_x_m) / self.cell_size_m).astype(np.int64)
        rows = np.floor((self.origin_y_m - y) / self.cell_size_m).astype(np.int64)
        return rows, cols

    def flat_index(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        return rows * self.columns + cols

    def rows_cols_from_flat(self, flat: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return np.divmod(flat, self.columns)

    def cell_ids(self, rows: np.ndarray, cols: np.ndarray) -> np.ndarray:
        return np.char.add(np.char.add(np.char.add("E1K-r", rows.astype(str)), "-c"), cols.astype(str))

    def cell_centres_xy(self, rows: np.ndarray, cols: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        x = self.origin_x_m + (cols + 0.5) * self.cell_size_m
        y = self.origin_y_m - (rows + 0.5) * self.cell_size_m
        return x, y

    def cells_in_bbox(self, bbox_wgs84: BBox) -> tuple[np.ndarray, np.ndarray]:
        west, south, east, north = bbox_wgs84
        to_grid = transformer("EPSG:4326", self.crs)
        xs, ys = to_grid.transform([west, east, west, east], [south, south, north, north])
        row_min, col_min = self.rows_cols_from_xy(np.array([min(xs)]), np.array([max(ys)]))
        row_max, col_max = self.rows_cols_from_xy(np.array([max(xs)]), np.array([min(ys)]))
        row_range = np.arange(max(row_min[0], 0), min(row_max[0], self.rows - 1) + 1)
        col_range = np.arange(max(col_min[0], 0), min(col_max[0], self.columns - 1) + 1)
        rows, cols = np.meshgrid(row_range, col_range, indexing="ij")
        return rows.ravel(), cols.ravel()

    def cell_polygon_wgs84(self, cell_id: str) -> Polygon:
        row, col = parse_cell_id(cell_id)
        x0 = self.origin_x_m + col * self.cell_size_m
        y1 = self.origin_y_m - row * self.cell_size_m
        cell = box(x0, y1 - self.cell_size_m, x0 + self.cell_size_m, y1)
        return transform_geometry(transformer(self.crs, "EPSG:4326").transform, cell)


def parse_cell_id(cell_id: str) -> tuple[int, int]:
    match = CELL_ID_PATTERN.match(cell_id)
    if match is None:
        raise ValueError(f"not a grid cell id: {cell_id!r}")

    return int(match.group(1)), int(match.group(2))


@cache
def transformer(source_crs: str, target_crs: str) -> Transformer:
    return Transformer.from_crs(source_crs, target_crs, always_xy=True)


@cache
def default_grid() -> Grid:
    spec = load_contract("grid.json")
    return Grid(
        grid_id=spec["grid_id"],
        crs=spec["crs"],
        cell_size_m=spec["cell_size_m"],
        columns=spec["columns"],
        rows=spec["rows"],
        origin_x_m=spec["origin_x_m"],
        origin_y_m=spec["origin_y_m"],
    )
