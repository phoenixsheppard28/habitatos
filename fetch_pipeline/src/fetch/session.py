"""Request-local receipts; cached downloads also count as handoff artifacts."""
from contextvars import ContextVar
from dataclasses import dataclass, field
from fetch.models import RawManifest


@dataclass
class Receipts:
    artifacts: dict[str, RawManifest] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


current_receipts: ContextVar[Receipts | None] = ContextVar('fetch_receipts', default=None)


def record(result: RawManifest | dict) -> None:
    receipts = current_receipts.get()
    if receipts is None:
        return
    if isinstance(result, RawManifest):
        if result.access_scope != 'public':
            receipts.warnings.append('Account-scoped Movebank download excluded from public handoff; see local manifest.')
            return
        receipts.artifacts[result.artifact_id] = result
        if result.extensions.get('movebank_download_mode') == 'public_preview':
            receipts.warnings.append('Movebank preview only: insufficient for movement analysis.')
    elif result.get('status') in ('error', 'restricted', 'unavailable'):
        receipts.warnings.append(result.get('message', 'Retrieval failed.'))
