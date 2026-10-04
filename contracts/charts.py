from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, FiniteFloat, model_validator


class ChartPoint(BaseModel):
    x: str | FiniteFloat
    y: FiniteFloat | None = Field(description="Use null for a missing measurement.")
    note: str | None = None


class ChartSeries(BaseModel):
    name: str
    points: list[ChartPoint]
    style: Literal["solid", "dashed"] = "solid"


class ChartXAxis(BaseModel):
    label: str
    type: Literal["category", "temporal", "numeric"]


class ChartYAxis(BaseModel):
    label: str
    unit: str | None = None


class ChartSpec(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    chart_id: str
    title: str
    kind: Literal["line", "bar", "scatter"]
    x_axis: ChartXAxis
    y_axis: ChartYAxis
    series: list[ChartSeries]
    description: str | None = None
    max_gap: FiniteFloat | None = Field(
        default=None, gt=0,
        description="Maximum x-axis gap between connected points. Temporal axes use milliseconds.",
    )

    @model_validator(mode="after")
    def valid_coordinates(self):
        for series in self.series:
            for point in series.points:
                if self.x_axis.type == "numeric" and not isinstance(point.x, float):
                    raise ValueError("Numeric axes need numeric coordinates.")
                if self.x_axis.type == "temporal":
                    if not isinstance(point.x, str):
                        raise ValueError("Temporal axes need ISO dates.")
                    datetime.fromisoformat(point.x.replace("Z", "+00:00"))
        return self
