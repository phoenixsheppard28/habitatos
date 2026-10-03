"""Request-local receipts and archive. Cached downloads also count as handoff artifacts."""

from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

from habitat.archive import Archive
from habitat.contracts import RawManifest


@dataclass
class Receipts:
    artifacts: dict[str, RawManifest] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


current_receipts: ContextVar[Receipts | None] = ContextVar("fetch_receipts", default=None)
current_archive: ContextVar[Archive | None] = ContextVar("fetch_archive", default=None)


def archive() -> Archive:
    """The archive of the current request. Outside a request, a local archive with an in-memory index."""
    return current_archive.get() or Archive()


def record(result: RawManifest | dict[str, Any]) -> None:
    receipts = current_receipts.get()
    if receipts is None:
        return

    if isinstance(result, RawManifest):
        if result.access_scope != "public":
            receipts.warnings.append(
                "Account-scoped Movebank download excluded from public handoff; see the local archive."
            )
            return
        receipts.artifacts[f"{result.artifact_id}/{result.version}"] = result
        if result.extensions.properties.get("movebank_download_mode") == "public_preview":
            receipts.warnings.append("Movebank preview only: insufficient for movement analysis.")
    elif result.get("status") in ("error", "restricted", "unavailable"):
        receipts.warnings.append(result.get("message", "Retrieval failed."))
