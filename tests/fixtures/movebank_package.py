"""A recorded-shape Movebank Data Repository package for tests, served through httpx.MockTransport."""

import httpx

PACKAGE_UUID = "5b6706c8-e7e5-46e4-82ba-da5a82324298"
HANDLE = "10255/move.1095"
API = "https://datarepository.movebank.org/server/api/core"

GPS_CSV = """event-id,visible,timestamp,location-long,location-lat,gps:dop,sensor-type,individual-taxon-canonical-name,tag-local-identifier,individual-local-identifier,study-name
1,true,2011-03-01 06:00:00.000,36.95,-1.45,4.6,"gps","Connochaetes taurinus","2829","Naboisho","Wildebeest"
2,true,2011-03-01 07:00:00.000,36.96,-1.45,7.2,"gps","Connochaetes taurinus","2829","Naboisho","Wildebeest"
3,false,2011-03-01 08:00:00.000,36.97,-1.46,40.0,"gps","Connochaetes taurinus","2829","Naboisho","Wildebeest"
4,true,2011-03-01 06:00:00.000,36.90,-1.40,,"gps","Connochaetes taurinus","2846","Olope","Wildebeest"
"""

REFERENCE_CSV = """tag-id,animal-id,animal-taxon,deploy-on-date,deploy-off-date,animal-sex,animal-life-stage,study-site
2829,Naboisho,Connochaetes taurinus,2010-05-28 06:00:00.000,2012-06-21 23:59:00.000,f,9 years,Athi-Kaputiei
2846,Olope,Connochaetes taurinus,2010-05-25 18:01:00.000,2011-08-11 23:59:00.000,m,10 years,Athi-Kaputiei
"""

FILES = {
    "bitstream-gps": ("Wildebeest.csv", GPS_CSV),
    "bitstream-reference": ("Wildebeest-reference-data.csv", REFERENCE_CSV),
}


def metadata(**values):
    return {key: [{"value": value}] for key, value in values.items()}


def item(location_name: str = "Wildebeest.csv") -> dict:
    names = {"bitstream-gps": location_name, "bitstream-reference": FILES["bitstream-reference"][0]}
    bitstreams = [
        {
            "name": name,
            "checkSum": {"value": f"md5-{key}"},
            "_links": {"content": {"href": f"{API}/bitstreams/{key}/content"}},
        }
        for key, name in names.items()
    ]
    return {
        "uuid": PACKAGE_UUID,
        "handle": HANDLE,
        "metadata": metadata(**{
            "mdr.study.id": "208413731",
            "dc.date.available": "2020-12-01T00:00:00Z",
            "dwc.ScientificName": "Connochaetes taurinus",
            "dc.identifier.uri": f"https://datarepository.movebank.org/handle/{HANDLE}",
            "dc.rights": "CC0",
            "dc.identifier.citation": "Stabach et al. 2020",
            "dc.identifier.doi": "10.5441/001/1.h0t27719",
            "dc.title": "Data from: Wildebeest on the Athi-Kaputiei Plains",
        }),
        "_embedded": {"bundles": {"_embedded": {"bundles": [
            {"name": "ORIGINAL", "_embedded": {"bitstreams": {"_embedded": {"bitstreams": bitstreams}}}}
        ]}}},
    }


def handler(location_name: str = "Wildebeest.csv"):
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith(f"/items/{PACKAGE_UUID}"):
            return httpx.Response(200, json=item(location_name))
        for key, (_, body) in FILES.items():
            if path.endswith(f"/bitstreams/{key}/content"):
                return httpx.Response(200, text=body)
        return httpx.Response(404)

    return handle
