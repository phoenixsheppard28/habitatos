"""Thresholds and method versions for the analysis lane.

Train and holdout floors are animal-days, not animals. Below the floor the
forecast is insufficient data rather than an unevaluated number.
"""

SCHEMA_VERSION = "1.0"
MOVEMENT_METHOD = "movement_summary"
MOVEMENT_VERSION = "1"
FORECAST_METHOD = "next_day_displacement"
FORECAST_VERSION = "1"
CELL_USE_METHOD = "cell_use"
CELL_USE_VERSION = "1"
MIN_OWN_HOLDOUT = 5

MIN_HOLDOUT_ROWS = 30
MIN_TRAIN_ROWS = 10
MIN_HABITAT_SIDE = 2
MIN_HABITAT_PAIRS = 4

SAMPLE_LIMITATION = (
    "Findings describe the tracked animals in this sample, not a wildlife census."
)
FORECAST_TARGET_LIMITATION = (
    "The evaluated target is next-day displacement of tracked animals, not a migration route."
)
HOLDOUT_LIMITATION = (
    "Holdout error uses observed previous-day displacement. "
    "The forward series is a separate recursive projection."
)
