# Layer compatibility roadmap

GeoServer plus OpenLayers covers standards-based 2D mapping. It does not reproduce every proprietary ArcGIS service or client-side renderer automatically.

| Expected layer or behavior | GeoServer publication path | OpenLayers path | Current status or gap |
| --- | --- | --- | --- |
| Vector points, lines, and polygons | PostGIS feature type; WMS for rendering and WFS/OGC Features for records | `ImageWMS` plus bounded `VectorSource` | Implemented for sample movement and study boundary |
| Cached vector rendering | GeoWebCache WMTS for a published GeoServer layer | `WMTS` tile source | Implemented for sample movement; cache invalidation belongs to publishing tooling |
| Raster imagery and continuous grids | GeoTIFF/ImageMosaic or PostGIS raster; WMS/WMTS | `ImageWMS` or `WMTS` | Adapter path supported; a real raster fixture is not included |
| Time-enabled vector or raster | GeoServer time dimension on a feature type or coverage | WMS `TIME`, WMTS dimensions, bounded WFS CQL filter | Implemented for sample layers; production catalogs must declare the real time field and range |
| Inspectable features | WFS 2.0 or OGC API Features with bbox/count limits | Vector loader and map feature selection | WFS implemented; OGC API Features is a future adapter |
| Styled categories and class breaks | Server-side SLD/CSS styles | Rendered WMS/WMTS | Supported through server styles; ArcGIS renderer JSON needs translation |
| Heat maps and clustering | Precomputed server layer or WPS/backend aggregation | OpenLayers `Heatmap` or cluster vector source | Not implemented; choose server or client aggregation based on data volume |
| Vector tiles | GeoServer vector tile extension / GeoWebCache | OpenLayers MVT source | Gap: extension, adapter, styles, and compatibility tests required |
| Elevation and 3D terrain | External terrain/3D service | OpenLayers is primarily 2D | Gap: use a dedicated 3D client such as Cesium when terrain or 3D scenes are required |
| ArcGIS FeatureServer/MapServer URLs | Proxy/ETL into approved GeoServer layers, or a dedicated ArcGIS adapter | No automatic conversion | Gap: assess licensing, authentication, field schemas, renderer translation, and pagination per service |
| ArcGIS scene, utility network, subtype group, and proprietary symbols | No direct GeoServer equivalent | No direct OpenLayers equivalent | Gap: retain an ArcGIS client or design a deliberate conversion path |
| Local GeoJSON | No publication; browser session only | `GeoJSON` format with vector source | Implemented as temporary import |
| Local CSV with coordinates | No publication; browser session only | Parsed to point features and a vector source | Implemented as temporary import; WGS84 latitude/longitude only |

Every production catalog entry must list only the actions its source actually supports. A rendered WMS layer is not inspectable unless a companion WFS or feature API is declared. Private and write-capable services require backend enforcement; hiding a URL or action in React is not access control.
