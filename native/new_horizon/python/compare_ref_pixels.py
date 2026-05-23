import csv
from osgeo import gdal, osr

MOON_RADIUS_M = 1_737_400.0
path = r"/d/datasets/viper_v71_2024_medium/other/dem.tif"
ds = gdal.Open(path, gdal.GA_ReadOnly)
if ds is None:
    raise SystemExit('failed to open DEM')
gt = ds.GetGeoTransform()
inv = gdal.InvGeoTransform(gt)
if isinstance(inv, tuple) and len(inv) == 2:
    success, inv_gt = inv
    if not success:
        raise SystemExit('invGeoTransform failed')
    inv_gt = tuple(inv_gt)
elif isinstance(inv, tuple) and len(inv) == 6:
    inv_gt = inv
else:
    raise SystemExit('unexpected inv result')

src = osr.SpatialReference()
src.ImportFromWkt(ds.GetProjection())
geog = osr.SpatialReference()
geog.ImportFromProj4(f"+proj=longlat +R={MOON_RADIUS_M} +no_defs")
transform = osr.CoordinateTransformation(geog, src)

def latlon_to_pixel(lat_deg, lon_deg):
    x, y, _ = transform.TransformPoint(lon_deg, lat_deg)
    col = inv_gt[0] + inv_gt[1] * x + inv_gt[2] * y
    row = inv_gt[3] + inv_gt[4] * x + inv_gt[5] * y
    return col, row

rows=[]
with open(r"tests/HorizonGen.Tests/bin/Debug/net9.0/reference_trace.csv") as fh:
    reader = csv.DictReader(fh)
    for rec in reader:
        lat = float(rec['lat_deg'])
        lon = float(rec['lon_deg'])
        px = float(rec['pixel_x'])
        py = float(rec['pixel_y'])
        col,row = latlon_to_pixel(lat, lon)
        rows.append((float(rec['dist_m']), px - col, py - row))

rows.sort(key=lambda t: abs(t[1]), reverse=True)
print('max px delta', rows[0])
rows.sort(key=lambda t: abs(t[2]), reverse=True)
print('max py delta', rows[0])
