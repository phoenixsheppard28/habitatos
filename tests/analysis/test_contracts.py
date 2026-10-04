import pytest
from pydantic import ValidationError

from contracts.models import ForecastRequest, QuerySpec
from support import REGION


def _query(**overrides):
    fields = {
        "query_id": "query-001",
        "question": "Where did the tracked antelope move?",
        "task_type": "historical",
        "species": ["antelope"],
        "region": REGION,
        "time_range": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-04T00:00:00Z"},
        "access_scope": "public",
    }
    fields.update(overrides)
    return fields


def test_query_requires_species_geometry_and_ordered_dates():
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(_query(species=[]))
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(_query(species=[" "]))
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(_query(region={"type": "Feature", "coordinates": []}))
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(
            _query(time_range={"start": "2026-02-01T00:00:00Z", "end": "2026-01-01T00:00:00Z"})
        )
    with pytest.raises(ValidationError):
        QuerySpec.model_validate(_query(task_type="story"))


def test_forecast_horizon_is_bounded():
    ForecastRequest.model_validate({"cutoff": "2026-02-01T00:00:00Z", "horizon_days": 30})
    with pytest.raises(ValidationError):
        ForecastRequest.model_validate({"cutoff": "2026-02-01T00:00:00Z", "horizon_days": 0})
    with pytest.raises(ValidationError):
        ForecastRequest.model_validate({"cutoff": "2026-02-01T00:00:00Z", "horizon_days": 31})


def test_column_type_must_be_one_of_the_shared_types():
    from contracts.models import ColumnSpec

    ColumnSpec(name="km", type="number", nullable=True, role="daily_displacement", unit="km")
    with pytest.raises(ValidationError):
        ColumnSpec(name="km", type="float", nullable=True)
