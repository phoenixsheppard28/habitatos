from dataclasses import dataclass, field
from typing import Literal

import httpx

from habitat.contracts import TaxonRef

GBIF_API = "https://api.gbif.org/v1"
SPECIES_RANKS = {"SPECIES", "SUBSPECIES"}
MAX_CANDIDATES = 20


@dataclass
class TaxonResolution:
    name: str
    status: Literal["resolved", "ambiguous", "not_found"]
    taxa: list[TaxonRef] = field(default_factory=list)
    candidates: list[TaxonRef] = field(default_factory=list)


def resolve_taxon(name: str, client: httpx.Client | None = None) -> TaxonResolution:
    """Resolve a user's species name against the GBIF backbone.

    A common name for a group, such as "antelope", is ambiguous. The caller must ask the user to choose
    from the candidates. Search must not expand it to every species silently.
    """
    if client is None:
        with httpx.Client(base_url=GBIF_API, timeout=20) as owned_client:
            return resolve_taxon(name, owned_client)

    response = client.get("/species/match", params={"name": name})
    response.raise_for_status()
    match = response.json()
    if match.get("matchType") == "EXACT" and match.get("rank") in SPECIES_RANKS:
        return TaxonResolution(name, "resolved", taxa=[TaxonRef(gbif_key=match["usageKey"], name=match["scientificName"])])

    if match.get("matchType") in ("EXACT", "FUZZY") and match.get("rank") not in SPECIES_RANKS:
        return TaxonResolution(name, "ambiguous", candidates=species_below(client, match["usageKey"]))

    response = client.get(
        "/species/search",
        params={"q": name, "qField": "VERNACULAR", "rank": "SPECIES", "status": "ACCEPTED", "limit": MAX_CANDIDATES},
    )
    response.raise_for_status()
    vernacular = response.json()
    candidates = [
        TaxonRef(gbif_key=result["key"], name=result["scientificName"])
        for result in vernacular.get("results", [])
        if "key" in result
    ]
    if len(candidates) == 1:
        return TaxonResolution(name, "resolved", taxa=candidates)

    return TaxonResolution(name, "ambiguous" if candidates else "not_found", candidates=candidates)


def species_below(client: httpx.Client, higher_taxon_key: int) -> list[TaxonRef]:
    response = client.get(
        "/species/search",
        params={"highertaxonKey": higher_taxon_key, "rank": "SPECIES", "status": "ACCEPTED", "limit": MAX_CANDIDATES},
    )
    response.raise_for_status()
    return [TaxonRef(gbif_key=r["key"], name=r["scientificName"]) for r in response.json().get("results", []) if "key" in r]
