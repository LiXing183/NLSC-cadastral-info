# Changelog

## 2.0.0 — 2026-10-04

- Added comma-separated batch queries and continuous map-click queries, prioritizing location and coloring before attribute completion.
- Preloaded county/township/section lists and restored native dropdown animations.
- Added per-record and batch fill colors with shared opacity.
- Sorted records by completion state, color, county code, land office code, section code and numeric parcel number.
- Reserved red text for confirmed failures; orange batch summaries appear after queries settle.
- Added spreadsheet-friendly attribute copy, one parcel per row.
- Added merged fill boundary generation, a single feature named 填色範圍 at the top of the layer tree.
- Improved mixed geometry repair, small gaps/fragments, cancellation and bounded memory processing.
- Release validation: 202 test executions on QGIS 4.2.3; saved 9-, 11- and 31-parcel datasets replayed in EPSG:3826, EPSG:3857 and EPSG:4326.

The publishing preparation updates documentation, license and metadata; runtime code remains identical to the v2.0.0 release.
