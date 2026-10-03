from collections.abc import Iterable

import numpy as np
import pandas as pd
from pyproj import CRS
from rasterio.transform import rowcol

from habitat.contracts import BBox
from habitat.grid import Grid, transformer
from habitat.normalize.raster_io import RasterBlock, pixel_area_m2, pixel_centres

MIN_VALID_FRACTION = 0.5


class CellAccumulator:
    """Running sum, sum of squares and count per grid cell and variable, so a tile is aggregated block by block."""

    def __init__(self, grid: Grid):
        self.grid = grid
        self.partials: list[pd.DataFrame] = []
        self.pixel_area_m2: float | None = None

    def add_block(self, block: RasterBlock, variables: dict[str, np.ndarray]) -> None:
        if not CRS.from_user_input(block.crs).is_projected:
            raise ValueError(f"zonal statistics need a projected CRS in metres, got {block.crs}")

        self.pixel_area_m2 = pixel_area_m2(block.transform)
        x, y = pixel_centres(block.transform, block.shape)
        grid_x, grid_y = transformer(block.crs, self.grid.crs).transform(x, y)
        rows, cols = self.grid.rows_cols_from_xy(np.asarray(grid_x), np.asarray(grid_y))
        flat = self.grid.flat_index(rows, cols)
        inside = (rows >= 0) & (rows < self.grid.rows) & (cols >= 0) & (cols < self.grid.columns)

        for variable, values in variables.items():
            values = values.ravel()
            if not inside.any():
                continue

            # Keep cells with no valid pixels so cloudy cells are represented as null observations.
            frame = pd.DataFrame({"flat": flat[inside], "value": values[inside]})
            frame.loc[~np.isfinite(frame["value"]), "value"] = np.nan
            frame["value_sq"] = frame["value"] ** 2
            partial = frame.groupby("flat").agg(
                total=("value", "sum"), total_sq=("value_sq", "sum"), count=("value", "count")
            )
            partial["variable"] = variable
            self.partials.append(partial.reset_index())

    def finalize(self, min_valid_fraction: float = MIN_VALID_FRACTION) -> pd.DataFrame:
        columns = ["cell_id", "variable", "value", "std", "valid_fraction", "pixel_count"]
        if not self.partials:
            return pd.DataFrame(columns=columns)

        sums = pd.concat(self.partials).groupby(["flat", "variable"], as_index=False)[
            ["total", "total_sq", "count"]
        ].sum()

        mean = sums["total"] / sums["count"]
        variance = (sums["total_sq"] / sums["count"] - mean**2).clip(lower=0)
        expected_pixels = self.grid.cell_size_m**2 / self.pixel_area_m2
        valid_fraction = (sums["count"] / expected_pixels).clip(upper=1.0)
        reliable = valid_fraction >= min_valid_fraction

        rows, cols = self.grid.rows_cols_from_flat(sums["flat"].to_numpy())
        return pd.DataFrame(
            {
                "cell_id": self.grid.cell_ids(rows, cols),
                "variable": sums["variable"],
                "value": mean.where(reliable),
                "std": np.sqrt(variance).where(reliable),
                "valid_fraction": valid_fraction,
                "pixel_count": sums["count"].astype("int64"),
            }
        )[columns]


def aggregate_blocks(
    grid: Grid,
    blocks: Iterable[RasterBlock],
    compute_variables,
    min_valid_fraction: float = MIN_VALID_FRACTION,
) -> pd.DataFrame:
    accumulator = CellAccumulator(grid)
    for block in blocks:
        accumulator.add_block(block, compute_variables(block))

    return accumulator.finalize(min_valid_fraction)


def sample_cell_centres(grid: Grid, source, aoi: BBox) -> pd.DataFrame:
    """Value of the source pixel under each grid cell centre. Use for sources coarser than the grid."""
    rows, cols = grid.cells_in_bbox(aoi)
    x, y = grid.cell_centres_xy(rows, cols)
    source_x, source_y = transformer(grid.crs, source.crs.to_string()).transform(x, y)
    pixel_rows, pixel_cols = rowcol(source.transform, source_x, source_y)
    pixel_rows, pixel_cols = np.asarray(pixel_rows), np.asarray(pixel_cols)

    inside = (pixel_rows >= 0) & (pixel_rows < source.height) & (pixel_cols >= 0) & (pixel_cols < source.width)
    rows, cols, pixel_rows, pixel_cols = rows[inside], cols[inside], pixel_rows[inside], pixel_cols[inside]
    if rows.size == 0:
        return pd.DataFrame({"cell_id": [], "raw": []})

    row_start, col_start = pixel_rows.min(), pixel_cols.min()
    window = ((row_start, pixel_rows.max() + 1), (col_start, pixel_cols.max() + 1))
    data = source.read(1, window=window)

    return pd.DataFrame(
        {
            "cell_id": grid.cell_ids(rows, cols),
            "raw": data[pixel_rows - row_start, pixel_cols - col_start],
        }
    )
