"""Bounded raw Sentinel-2, MOD13Q1 Terra, and CHIRPS v2 retrieval."""
from __future__ import annotations

import json
import math
import tempfile
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from fetch.archive import load_cached_by_source_key, register_raw_artifact_from_path, save_manifest
from fetch.http_util import fetch_json, download_to_path
from fetch.models import Coverage, Rights, SourceRef
from fetch.session import record

STAC = 'https://planetarycomputer.microsoft.com/api/stac/v1'
SIGN = 'https://planetarycomputer.microsoft.com/api/sas/v1/sign'
CHIRPS = 'https://data.chc.ucsb.edu/products/CHIRPS-2.0/global_daily/tifs/p05'
PRODUCTS = {
    'sentinel-2': ('sentinel-2-l2a', ['B02', 'B03', 'B04', 'B08', 'B11', 'B12', 'SCL']),
    'modis': ('modis-13Q1-061', ['250m_16_days_NDVI', '250m_16_days_EVI',
                              '250m_16_days_VI_Quality', '250m_16_days_pixel_reliability']),
}
# Asset hosts from provider-owned STAC collections; never accept arbitrary URLs.
ASSET_HOSTS = {'sentinel2l2a01.blob.core.windows.net', 'sentinel2l2a02.blob.core.windows.net',
               'sentinel2l2a03.blob.core.windows.net', 'modiseuwest.blob.core.windows.net'}


def validate_request(bbox, start, end, sources, max_items, max_days, max_bytes):
    if len(bbox) != 4 or not all(math.isfinite(x) for x in bbox):
        raise ValueError('bbox must contain four finite WGS84 coordinates')
    w, s, e, n = bbox
    if not (-180 <= w < e <= 180 and -90 <= s < n <= 90):
        raise ValueError('bbox must be west,south,east,north; split antimeridian regions')
    first, last = date.fromisoformat(start), date.fromisoformat(end)
    if first > last:
        raise ValueError('start must be on or before end')
    if not sources or set(sources) - {'sentinel-2', 'modis', 'chirps'}:
        raise ValueError('sources must be sentinel-2, modis, or chirps')
    if not 1 <= max_items <= 100 or not 1 <= max_days <= 366 or max_bytes <= 0:
        raise ValueError('max_items: 1..100; max_days: 1..366; max_bytes must be positive')
    return first, last


def discover_stac(source, bbox, start, end, max_items, cloud_cover):
    collection, keys = PRODUCTS[source]
    params = {'collections': collection, 'bbox': ','.join(map(str, bbox)),
              'datetime': f'{start}T00:00:00Z/{end}T23:59:59Z',
              'limit': min(max_items * 2, 100) if source == 'modis' else max_items}
    # Older MODIS records have an empty platform property. Identify MOD13Q1 from
    # its product ID as well; a server-side terra filter silently drops those records.
    if source != 'modis':
        params['query'] = json.dumps({'eo:cloud_cover': {'lte': cloud_cover}})
    metadata = fetch_json(f'{STAC}/collections/{collection}')
    url = f'{STAC}/search?{urlencode(params)}'
    selected, seen, warnings = [], set(), []
    for page_number in range(10):
        page = fetch_json(url)
        for item in page.get('features', []):
            if item['id'] in seen:
                continue
            seen.add(item['id'])
            props = item.get('properties', {})
            if source == 'modis' and not (item['id'].startswith('MOD13Q1.') and props.get('platform') in (None, '', 'terra')):
                continue
            selected.append(item)
            if len(selected) == max_items:
                break
        if len(selected) == max_items:
            warnings.append(f'{source}: scene limit reached; this is a bounded sample, not complete coverage.')
            break
        next_link = next((x for x in page.get('links', []) if x.get('rel') == 'next'), None)
        if not next_link:
            break
        url = next_link['href']
        parsed = urlsplit(url)
        if next_link.get('method', 'GET') != 'GET' or parsed.scheme != 'https' or parsed.hostname != 'planetarycomputer.microsoft.com':
            warnings.append(f'{source}: unsupported pagination link; search incomplete.')
            break
    else:
        warnings.append(f'{source}: catalog page limit reached; search incomplete.')
    candidates = []
    for item in selected:
        props = item.get('properties', {})
        for key in keys:
            asset = item.get('assets', {}).get(key)
            if not asset:
                warnings.append(f"Missing asset {key} in {item['id']}; scene is incomplete.")
                continue
            url = asset['href']
            if urlsplit(url).hostname not in ASSET_HOSTS or urlsplit(url).scheme != 'https':
                warnings.append(f"Unsupported asset host for {item['id']}/{key}")
                continue
            candidates.append({
                'source': source, 'dataset_id': f"pc:{collection}:{item['id']}:{key}",
                'url': url, 'filename': f"{item['id']}_{key}.tif", 'format': 'tif',
                'coverage': {'bbox': item.get('bbox'),
                             'start': props.get('start_datetime') or props.get('datetime'),
                             'end': props.get('end_datetime') or props.get('datetime')},
                'rights': {'license': metadata.get('license'), 'attribution': (metadata.get('providers') or [{}])[0].get('name')},
                'metadata': {'collection': collection, 'item_id': item['id'], 'asset_key': key,
                             'properties': props, 'asset': asset,
                             'asset_definition': metadata.get('item_assets', {}).get(key),
                             'license_links': [x for x in metadata.get('links', []) if x.get('rel') == 'license'],
                             'time_model': 'composite' if source == 'modis' else 'instant',
                             'data_kind': 'vegetation_observations' if source == 'modis' else 'surface_reflectance',
                             'clipped': False},
            })
    if not candidates:
        warnings.append(f'{source}: no downloadable assets found for the requested region and dates.')
    return candidates, warnings


def discover_chirps(bbox, first, last, max_days):
    warnings = []
    if bbox[3] <= -50 or bbox[1] >= 50:
        return [], ['CHIRPS v2 has no coverage outside 50°S–50°N.']
    if bbox[1] < -50 or bbox[3] > 50:
        warnings.append('CHIRPS v2 covers only the part of this region within 50°S–50°N.')
    candidates = []
    days = (last - first).days + 1
    if days > max_days:
        warnings.append(f'CHIRPS day limit reached: fetching first {max_days} of {days} requested days.')
    for offset in range(min(days, max_days)):
        day = first + timedelta(days=offset)
        filename = f'chirps-v2.0.{day:%Y.%m.%d}.tif.gz'
        candidates.append({
            'source': 'chirps', 'dataset_id': f'chirps-v2:{day}',
            'url': f'{CHIRPS}/{day.year}/{filename}', 'filename': filename, 'format': 'tif.gz',
            'coverage': {'bbox': [-180, -50, 180, 50], 'start': f'{day}T00:00:00Z',
                         'end': f'{day + timedelta(days=1)}T00:00:00Z'},
            'rights': {'attribution': 'Climate Hazards Center, UC Santa Barbara'},
            'metadata': {'data_kind': 'rainfall_observations', 'time_model': 'interval', 'interval_end_exclusive': True, 'units': 'mm',
                         'resolution_degrees': 0.05, 'clipped': False,
                         'compression': 'gzip', 'license_url': 'https://chc.ucsb.edu/data/chirps'},
        })
    return candidates, warnings


def fetch_environment(bbox: list[float], start: str, end: str, *,
                      sources: list[str] | None = None, max_items: int = 1,
                      max_days: int = 3, max_bytes: int = 2 * 1024**3,
                      max_file_bytes: int = 512 * 1024**2,
                      cloud_cover: float = 30, discover_only: bool = False,
                      on_event=None) -> dict:
    """Return per-file outcomes. Dates are inclusive days; raw rasters are not clipped.

    Budgets bound successfully downloaded payload bytes; failed transfer retries may
    use additional bandwidth. Sources are processed sequentially to protect the index.
    """
    sources = list(dict.fromkeys(sources if sources is not None else ['sentinel-2', 'modis', 'chirps']))
    first, last = validate_request(bbox, start, end, sources, max_items, max_days, max_bytes)
    if not 0 <= cloud_cover <= 100 or max_file_bytes <= 0:
        raise ValueError('cloud_cover must be 0..100; max_file_bytes must be positive')
    outcomes, warnings, manifests, used = [], [], [], 0

    def emit(outcome):
        outcomes.append(outcome)
        if on_event:
            on_event(outcome)

    for source in sources:
        try:
            if source == 'chirps':
                candidates, gaps = discover_chirps(bbox, first, last, max_days)
            else:
                candidates, gaps = discover_stac(source, bbox, start, end, max_items, cloud_cover)
            warnings.extend(gaps)
        except Exception as exc:
            # Do not persist signed URLs or provider credentials from exception text.
            warnings.append(f'{source}: catalog search failed ({type(exc).__name__}).')
            emit({'source': source, 'status': 'failed', 'code': 'search_failed'})
            continue
        for candidate in candidates:
            emit({**candidate, 'status': 'discovered'})
            if discover_only:
                continue
            key = candidate['dataset_id']
            manifest = load_cached_by_source_key(key)
            if manifest is not None:
                manifests.append(manifest.model_dump(mode='json'))
                record(manifest)
                emit({'dataset_id': key, 'status': 'cached', 'manifest': manifests[-1]})
                continue
            budget = min(max_file_bytes, max_bytes - used)
            if budget <= 0:
                emit({'dataset_id': key, 'status': 'blocked', 'code': 'byte_budget'})
                warnings.append(f'{key}: byte budget exhausted.')
                continue
            try:
                url = candidate['url']
                if source != 'chirps':
                    url = fetch_json(f'{SIGN}?{urlencode({"href": url})}')['href']
                with tempfile.TemporaryDirectory(prefix='habitat-fetch-') as tmp:
                    file = Path(tmp) / Path(candidate['filename']).name
                    emit({'dataset_id': key, 'status': 'downloading'})
                    size = download_to_path(url, file, max_bytes=budget,
                                            allowed_hosts=ASSET_HOSTS if source != 'chirps' else {'data.chc.ucsb.edu'})
                    # Count payload bytes even when validation or registration fails.
                    used += size
                    with file.open('rb') as stream:
                        magic = stream.read(4)
                    valid = magic[:2] == b'\x1f\x8b' if source == 'chirps' else magic in (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+')
                    if not valid:
                        raise ValueError('Unexpected payload; expected gzip or TIFF')
                    manifest = register_raw_artifact_from_path(
                        src_path=file, source=SourceRef(name=source, url=candidate['url'], study_id=key),
                        coverage=Coverage(**candidate['coverage']), rights=Rights(**candidate['rights']),
                        source_key=key, file_format=candidate['format'])
                manifest.extensions.update(candidate['metadata'])
                manifest.extensions['requested_coverage'] = {'bbox': bbox, 'start': start, 'end': end}
                save_manifest(manifest)
                record(manifest)
                manifests.append(manifest.model_dump(mode='json'))
                emit({'dataset_id': key, 'status': 'downloaded', 'manifest': manifests[-1]})
            except Exception as exc:
                detail = str(exc) if isinstance(exc, ValueError) else f'{type(exc).__name__} (HTTP {getattr(exc, "code", "n/a")})'
                emit({'dataset_id': key, 'status': 'failed', 'code': type(exc).__name__, 'message': detail})
                warnings.append(f'{key}: download failed: {detail}.')
    if not discover_only:
        for warning in warnings:
            record({'status': 'unavailable', 'message': warning})
    return {'status': ('discovered' if discover_only else ('partial' if warnings else 'ok')) if (manifests or (discover_only and any(x['status'] == 'discovered' for x in outcomes))) else 'insufficient_data',
            'raw_artifacts': manifests, 'outcomes': outcomes, 'warnings': warnings,
            'downloaded_bytes': used, 'coverage_verified': False,
            'limitations': ['Raw files are not clipped; scene selection does not guarantee full spatial or temporal coverage.',
                            'CHIRPS originals remain gzip-compressed for ingestion.']}
