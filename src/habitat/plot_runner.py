"""Child process that runs model-written Plotly code. Start it only through habitat.plots.

The limits stop accidents and casual misuse. They are not a security boundary against hostile code.
"""
import builtins
import contextlib
import json
import resource
import socket
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import plotly.io as pio

ALLOWED_IMPORTS = {"collections", "datetime", "itertools", "math", "statistics", "numpy", "pandas", "scipy",
                   "sklearn", "statsmodels", "plotly"}
BLOCKED_BUILTINS = {"open", "exec", "eval", "compile", "input", "breakpoint", "exit", "quit", "help"}
CPU_SECONDS = 30
COLORWAY = ["#387c78", "#c77723", "#7561a8", "#4779b8", "#b85270", "#78823b"]


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    if level or name.split(".", 1)[0] not in ALLOWED_IMPORTS:
        raise ImportError(f"Import of {name} is not allowed. Allowed modules: {', '.join(sorted(ALLOWED_IMPORTS))}.")
    return builtins.__import__(name, globals, locals, fromlist, level)


def no_network(*args, **kwargs):
    raise PermissionError("Network access is not allowed in plot code.")


def lock_down():
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS))
    socket.socket.connect = no_network
    socket.socket.connect_ex = no_network
    socket.socket.bind = no_network
    socket.getaddrinfo = no_network
    socket.create_connection = no_network


def dora_template():
    template = go.layout.Template(pio.templates["simple_white"])
    template.layout.colorway = COLORWAY
    template.layout.font = {"family": "'Source Sans 3', Arial, sans-serif", "color": "#253342", "size": 13}
    template.layout.xaxis.showgrid = template.layout.yaxis.showgrid = True
    template.layout.xaxis.gridcolor = template.layout.yaxis.gridcolor = "#e2e5e9"
    return template


def run(code, frame):
    pio.templates["dora"] = dora_template()
    pio.templates.default = "dora"
    safe_builtins = {name: value for name, value in vars(builtins).items() if name not in BLOCKED_BUILTINS}
    safe_builtins["__import__"] = guarded_import
    namespace = {"__builtins__": safe_builtins, "__name__": "__plot__",
                 "df": frame, "pd": pd, "np": np, "px": px, "go": go}

    with contextlib.redirect_stdout(sys.stderr):
        exec(compile(code, "<plot>", "exec"), namespace)

    figure = namespace.get("fig")
    if not isinstance(figure, go.Figure):
        raise TypeError("The code must assign a plotly.graph_objects.Figure to the variable fig.")
    return json.loads(figure.to_json())


def main():
    workdir = Path(sys.argv[1])
    frame = pd.read_parquet(workdir / "table.parquet")
    code = sys.stdin.read()
    lock_down()

    try:
        payload = {"figure": run(code, frame)}
    except BaseException as error:
        lines = traceback.format_exception(error)
        user_frames = [line for line in lines if 'File "<plot>"' in line or not line.startswith("  File ")]
        payload = {"error": "".join(user_frames)[-3000:]}

    sys.stdout.write(json.dumps(payload))


if __name__ == "__main__":
    main()
