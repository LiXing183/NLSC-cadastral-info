# Changelog

## 2.0.2 — 2026-10-07

- Resolve three Flake8 E731 findings by replacing assigned lambdas with named functions.
- Preserve namespace tag parsing, county-list callback handling and per-request argument binding for point-based dropdown refresh.
- Retain the XML security protections introduced in v2.0.1.


## 2.0.1 — 2026-10-04

- Replace all untrusted XML parsing with Qt QXmlStreamReader; reject DTDs/custom entities and bound document bytes, node count and depth. No new external dependency.
- Move two public JavaScript source SHA-256 checksums into documentation. They were provenance hashes, not credentials; lookup tables and source URLs are unchanged.
- Runtime lookup, cache validation and survey parsing continue to share the same safe parser.


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

The v2.0.1 release updates XML security parsing. Other functionality is retained from v2.0.0.
