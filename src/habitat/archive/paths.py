from pathlib import Path

PARTIAL_SUFFIX = ".part"


def safe_name(value: str) -> str:
    """The base name of a remote file name or an id, safe to use as one path component."""
    name = Path(str(value).replace("\\", "/")).name
    if name in ("", ".", "..") or "\x00" in name or name.endswith(PARTIAL_SUFFIX):
        raise ValueError(f"unsafe file name: {value!r}")

    return name


def contained(root: Path, *parts: str) -> Path:
    root = root.resolve()
    path = root.joinpath(*parts).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"path {path} is outside the archive root {root}")

    return path
