# Analysis lane

This lane does not fetch or join data. Recipe hands it a finished feature table. Analysis computes the summary or forecast and writes the report from those numbers.

## What we accept

A contract `1.0` request with a question and a Parquet feature table (`artifact://` only).

The question names the species, a GeoJSON region, and a UTC time range. Forecast questions also need a cutoff, a horizon of 1–30 days, and a target: `next_day_displacement` or `cell_use`. Two named before/after windows, or named polygon boundaries, are optional.

Columns are matched by role, not by name. Movement needs `entity_id`, `event_time`, `longitude`, `latitude`, and `daily_displacement` with a unit. A forecast of displacement needs the animal, the time, and the displacement. Cell use needs the animal, the time, and `cell_id`. Rainfall or vegetation alone, with `event_time`, is an environment summary. Optional roles: `cell_id`, `rainfall`, `vegetation_index`, `species`.

One row per animal per calendar day. Coordinates are WGS84. Times are UTC. A public table can be read by any request. A private table can be read only by the same access scope. The query id and access scope on the question must match the envelope.

## What we return

`AnalysisSpec`, `AnalysisResult`, and a model file only when a forecast model was fit.

The result has a status (`complete`, `partial`, or `insufficient_data`), findings, metrics, a map, a timeline, limitations, and a report. Evidence cites the feature table, the recipe version, and the source datasets. `partial` means some coordinates were missing. `insufficient_data` means the table cannot support the question. That is final, not a retry.

Historical output is the tracked sample: displacement, revisit cells, an optional rainfall or vegetation split, before/after sample sizes, and the share of the track inside each boundary. Forecast output is next-day displacement or the next cell, with holdout error against a baseline. The model is the shown forecast only when it beats that baseline. Each animal's forward series starts from that animal's last observation and is labeled as a projection. Extinction and population-migration questions are refused.
