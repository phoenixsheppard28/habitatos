import pandas as pd
import pytest

from habitat import plots
from habitat.plots import PlotError, render_figure

TABLE = pd.DataFrame({"ndvi": [0.2, 0.5, 0.8, 0.6], "km": [3.0, 2.0, 1.0, 1.5], "animal": ["a", "a", "b", "b"]})


def test_statistical_figures_with_library_trendlines_render():
    figure = render_figure(TABLE, "fig = px.scatter(df, x='ndvi', y='km', color='animal', trendline='ols')")

    assert [trace["mode"] for trace in figure["data"]] == ["markers", "lines", "markers", "lines"]


@pytest.mark.parametrize("code", ["import os", "import subprocess", "from urllib import request", "open('/etc/hosts')",
                                  "eval('1')"])
def test_files_processes_and_network_are_not_available(code):
    with pytest.raises(PlotError, match="not allowed|not defined"):
        render_figure(TABLE, code)


def test_backend_secrets_are_not_in_the_plot_environment(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    code = "fig = go.Figure(layout={'title': {'text': str(sorted(pd.io.common.os.environ))}})"

    figure = render_figure(TABLE, code)

    assert "OPENAI_API_KEY" not in figure["layout"]["title"]["text"]


def test_code_must_assign_a_figure():
    with pytest.raises(PlotError, match="assign a plotly.graph_objects.Figure"):
        render_figure(TABLE, "chart = px.scatter(df, x='ndvi', y='km')")


def test_long_running_code_is_stopped(monkeypatch):
    monkeypatch.setattr(plots, "TIMEOUT_SECONDS", 1)

    with pytest.raises(PlotError, match="longer than 1 seconds"):
        render_figure(TABLE, "while True:\n    pass")


def test_oversized_figures_are_refused(monkeypatch):
    monkeypatch.setattr(plots, "MAX_FIGURE_BYTES", 100)

    with pytest.raises(PlotError, match="Aggregate the data"):
        render_figure(TABLE, "fig = px.scatter(df, x='ndvi', y='km')")
