from typing import Any

from pydantic import BaseModel, Field


class PlotlyFigure(BaseModel):
    data: list[dict[str, Any]]
    layout: dict[str, Any] = Field(default_factory=dict)


class Chart(BaseModel):
    schema_version: str = "2.0"
    chart_id: str
    title: str
    figure: PlotlyFigure
    code: str = Field(description="The Python code that built the figure from the prepared table.")
