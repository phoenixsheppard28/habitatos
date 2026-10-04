from copy import deepcopy


def vegetation_scenario(scenario):
    def column(name, kind, role=None, unit=None):
        return {"name": name, "type": kind, "role": role, "unit": unit,
                "nullable": True, "description": name}

    fixes = [column("entity_id", "string", "entity_id"), column("observed_at", "timestamp", "event_time"),
             column("longitude", "number", "longitude", "degree"),
             column("latitude", "number", "latitude", "degree"), column("cell_id", "string", "cell_id")]
    vegetation = [column("cell_id", "string", "cell_id"), column("geometry", "geometry", "geometry"),
                  column("observed_at", "timestamp", "event_time"), column("observed_until", "timestamp"),
                  column("source_record_id", "string"), column("index_name", "string"),
                  column("index_value", "number", "measurement", "1")]
    coverage = {"start": "2025-12-01T00:00:00Z", "end": "2026-02-01T00:00:00Z", "bbox": [0, 0, 3, 3]}

    def dataset(name, family, columns):
        return {"dataset_id": name, "version": "1", "access_scope": "public", "status": "ready",
                "family": family, "description": name, "row_grain": "observation", "columns": columns,
                "storage": {"uri": f"fixture://{name}"}, "mapping_version": "1",
                "validation_report_ref": "fixture-validation", "coverage": deepcopy(coverage),
                "metadata": {"source_id": "modis_mod13q1" if name == "vegetation" else "movebank_repository"}}

    tracks = [{"entity_id": "bear-a", "observed_at": "2026-01-01T01:00:00Z", "longitude": 1, "latitude": 1, "cell_id": "a"},
              {"entity_id": "bear-a", "observed_at": "2026-01-01T02:00:00Z", "longitude": 1, "latitude": 1, "cell_id": "a"},
              {"entity_id": "bear-b", "observed_at": "2026-01-02T01:00:00Z", "longitude": 2, "latitude": 2, "cell_id": "b"}]
    rows = [{"cell_id": cell, "geometry": {"type": "Point", "coordinates": [1, 1]},
             "observed_at": timestamp, "observed_until": "2026-01-17T00:00:00Z",
             "source_record_id": str(index), "index_name": "ndvi", "index_value": value}
            for index, (cell, timestamp, value) in enumerate([
                ("a", "2025-12-31T00:00:00Z", 0.2), ("a", "2026-01-01T01:30:00Z", 0.8),
                ("b", "2026-01-01T00:00:00Z", 0.4)])]
    rows.extend({**row, "index_name": "evi"} for row in list(rows))
    rows.extend({**rows[0], "cell_id": f"unused-{index}"} for index in range(12))
    rows.append({**rows[0], "cell_id": "remote", "geometry": {"type": "Point", "coordinates": [40, -1]}})
    output_columns = [*deepcopy(fixes), column("greenness", "number", unit="1"),
                      column("vegetation_valid_from", "timestamp"), column("vegetation_valid_until", "timestamp")]
    scenario.clear()
    scenario.update({
        "query": {"query_id": "q-bear", "question": "Time spent in greener places", "task_type": "historical",
                  "access_scope": "public", "analysis_method": "residence_time",
                  "region": {"type": "Polygon", "coordinates": [[[0, 0], [3, 0], [3, 3], [0, 3], [0, 0]]]},
                  "time_range": {"start": "2026-01-01T00:00:00Z", "end": "2026-01-03T00:00:00Z"}},
        "datasets": [dataset("bear_fixes", "animal_locations", fixes),
                     dataset("vegetation", "vegetation_observations", vegetation)],
        "rows": {"bear_fixes": tracks, "vegetation": rows},
        "recipe": {
            "recipe_id": "bear-vegetation", "version": "1", "query_ref": "q-bear", "access_scope": "public",
            "inputs": {"veg": {"dataset_id": "vegetation", "version": "1"},
                       "fixes": {"dataset_id": "bear_fixes", "version": "1"}},
            "steps": [
                {"id": "renamed", "operation": "select", "input": "veg",
                 "columns": {("spectral_index" if c["name"] == "index_name" else c["name"]): c["name"] for c in vegetation}},
                {"id": "ndvi", "operation": "filter", "input": "renamed",
                 "predicates": [{"column": "spectral_index", "operator": "eq", "value": "ndvi"}]},
                {"id": "joined", "operation": "asof_join", "left": "fixes", "right": "ndvi",
                 "keys": {"cell_id": "cell_id"}, "left_time": "observed_at", "right_time": "observed_at",
                 "right_tie_break": "source_record_id", "tolerance_seconds": 32 * 86400,
                 "right_columns": {"greenness": "index_value", "vegetation_valid_from": "observed_at",
                                   "vegetation_valid_until": "observed_until"}},
                {"id": "features", "operation": "select", "input": "joined",
                 "columns": {c["name"]: c["name"] for c in output_columns}},
            ],
            "output": {"step": "features", "row_grain": "animal fix", "keys": ["entity_id", "observed_at"],
                       "time_column": "observed_at", "description": "NDVI at original bear fixes",
                       "intended_use": "residence time", "columns": output_columns},
        },
    })

    return scenario
