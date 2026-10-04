import json
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

TIMEOUT_SECONDS = 45
MAX_FIGURE_BYTES = 4_000_000


class PlotError(Exception):
    pass


def render_figure(frame, code):
    with TemporaryDirectory(prefix="dora-plot-") as workdir:
        frame.to_parquet(Path(workdir) / "table.parquet")
        # An empty environment keeps API keys and database URLs out of model-written code.
        environment = {"HOME": workdir, "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                       "MPLCONFIGDIR": workdir}
        try:
            completed = subprocess.run([sys.executable, "-I", "-m", "habitat.plot_runner", workdir],
                                       input=code, capture_output=True, text=True, cwd=workdir,
                                       env=environment, timeout=TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired as error:
            raise PlotError(f"The plot code ran longer than {TIMEOUT_SECONDS} seconds. "
                            "Aggregate or sample the table first.") from error

    if completed.returncode == -signal.SIGXCPU:
        raise PlotError("The plot code used too much CPU time. Aggregate or sample the table first.")
    if len(completed.stdout) > MAX_FIGURE_BYTES:
        raise PlotError(f"The figure is larger than {MAX_FIGURE_BYTES // 1_000_000} MB. "
                        "Aggregate the data or sample at most a few thousand points.")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        detail = completed.stderr[-2000:] or f"exit code {completed.returncode}"
        raise PlotError(f"The plot process stopped without a figure: {detail}") from error

    if "error" in payload:
        raise PlotError(payload["error"])
    return payload["figure"]


def figure_summary(figure):
    layout = figure.get("layout") or {}
    title = layout.get("title")
    return {"title": title.get("text") if isinstance(title, dict) else title,
            "traces": [{key: trace.get(key) for key in ("type", "name", "mode") if trace.get(key) not in {None, ""}}
                       for trace in figure.get("data") or []]}
