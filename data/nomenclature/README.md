# Nomenclature Source Snapshot

This directory is for local, non-committed source snapshots used to ingest lunar nomenclature.

Current expected files:
- `MOON_nomenclature_center_pts.zip` (downloaded from USGS/IAU mirror)
- `MOON_nomenclature_center_pts.geojson` (converted from shapefile for ingest)

Ingest command:

```bash
.venv/bin/python scripts/ingest-nomenclature.py \
  data/nomenclature/MOON_nomenclature_center_pts.geojson
```
