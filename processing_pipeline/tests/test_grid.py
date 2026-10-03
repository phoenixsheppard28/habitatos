import numpy as np

from habitat.grid import parse_cell_id, transformer


def test_cell_ids_round_trip(grid):
    rows, cols = np.array([4410, 0]), np.array([21877, 34703])

    ids = grid.cell_ids(rows, cols)

    assert list(ids) == ["E1K-r4410-c21877", "E1K-r0-c34703"]
    assert parse_cell_id(ids[0]) == (4410, 21877)


def test_cells_are_one_square_kilometre(grid):
    x, y = transformer("EPSG:4326", grid.crs).transform(16.0, -19.0)
    rows, cols = grid.rows_cols_from_xy(np.array([x]), np.array([y]))
    cell_id = grid.cell_ids(rows, cols)[0]

    polygon = grid.cell_polygon_wgs84(cell_id)

    assert polygon.contains(polygon.centroid)
    assert abs(grid.cell_size_m**2 - 1_001_790) < 1


def test_cells_in_bbox_cover_the_box(grid):
    rows, cols = grid.cells_in_bbox((16.0, -19.05, 16.05, -19.0))

    # 0.05 degrees is about 5.3 km east-west and 5.5 km north-south at 19 degrees south.
    assert 30 <= rows.size <= 49
