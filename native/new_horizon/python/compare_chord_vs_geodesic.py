import argparse
import math
from pathlib import Path
from typing import Tuple
from osgeo import gdal, osr

MOON_RADIUS_M = 1_737_400.0

def build_longlat_srs():
    srs = osr.SpatialReference()
    srs.ImportFromProj4(f"+proj=longlat +R={MOON_RADIUS_M} +no_defs")
    return srs

def invert_gt(gt):
    inv = gdal.InvGeoTransform(gt)
    if isinstance(inv, tuple) and len(inv) == 2:
        success, inv_gt = inv
        if not success:
            raise RuntimeError("GeoTransform not invertible")
        return tuple(inv_gt)
    if isinstance(inv, tuple) and len(inv) == 6:
        return inv
    raise RuntimeError(f"Unexpected InvGeoTransform result: {inv!r}")


def load_dataset(path: str):
    ds = gdal.Open(path, gdal.GA_ReadOnly)
    if ds is None:
        raise RuntimeError(f"Failed to open {path}")
    gt = ds.GetGeoTransform()
    inv_gt = invert_gt(gt)
    target = osr.SpatialReference()
    target.ImportFromWkt(ds.GetProjection())
    geog = build_longlat_srs()
    to_geog = osr.CoordinateTransformation(target, geog)
    to_map = osr.CoordinateTransformation(geog, target)
    return ds, gt, inv_gt, to_geog, to_map


def pixel_to_latlon(gt, to_geog, col, row):
    x = gt[0] + gt[1] * col + gt[2] * row
    y = gt[3] + gt[4] * col + gt[5] * row
    lon_deg, lat_deg, _ = to_geog.TransformPoint(x, y)
    return lat_deg, lon_deg


def latlon_to_pixel(inv_gt, to_map, lat_deg, lon_deg):
    x, y, _ = to_map.TransformPoint(lon_deg, lat_deg)
    col = inv_gt[0] + inv_gt[1] * x + inv_gt[2] * y
    row = inv_gt[3] + inv_gt[4] * x + inv_gt[5] * y
    return col, row


def geodesic_point(lat_rad, lon_rad, az_rad, dist_m):
    R = MOON_RADIUS_M
    ang = dist_m / R
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_ang = math.sin(ang)
    cos_ang = math.cos(ang)
    sin_az = math.sin(az_rad)
    cos_az = math.cos(az_rad)
    lat2 = math.asin(sin_lat * cos_ang + cos_lat * sin_ang * cos_az)
    lon2 = lon_rad + math.atan2(sin_az * sin_ang * cos_lat, cos_ang - sin_lat * math.sin(lat2))
    return lat2, lon2


def latlon_to_ecef(lat_rad, lon_rad, elev_m=0.0):
    r = MOON_RADIUS_M + elev_m
    cos_lat = math.cos(lat_rad)
    sin_lat = math.sin(lat_rad)
    cos_lon = math.cos(lon_rad)
    sin_lon = math.sin(lon_rad)
    x = r * cos_lat * cos_lon
    y = r * cos_lat * sin_lon
    z = r * sin_lat
    return (x, y, z)


def normalize(v):
    x,y,z = v
    mag = math.sqrt(x*x+y*y+z*z)
    return (x/mag, y/mag, z/mag)


def enu_axes(lat_rad, lon_rad):
    # East, North, Up (ECEF)
    sin_lat = math.sin(lat_rad)
    cos_lat = math.cos(lat_rad)
    sin_lon = math.sin(lon_rad)
    cos_lon = math.cos(lon_rad)
    east = (-sin_lon, cos_lon, 0.0)
    north = (-sin_lat*cos_lon, -sin_lat*sin_lon, cos_lat)
    up = (cos_lat*cos_lon, cos_lat*sin_lon, sin_lat)
    return east, north, up


def vector_add(a,b):
    return (a[0]+b[0], a[1]+b[1], a[2]+b[2])

def vector_scale(v,s):
    return (v[0]*s, v[1]*s, v[2]*s)


def ecef_to_latlon(v):
    x,y,z = v
    r = math.sqrt(x*x+y*y+z*z)
    lat = math.asin(z/r)
    lon = math.atan2(y,x)
    return lat, lon


def main():
    parser = argparse.ArgumentParser(description="Compare chord vs geodesic pixel deltas")
    parser.add_argument('--dem', required=True)
    parser.add_argument('--origin-x', type=float, required=True)
    parser.add_argument('--origin-y', type=float, required=True)
    parser.add_argument('--azimuth', type=float, required=True, help='degrees clockwise from north')
    parser.add_argument('--distances', type=float, nargs='*', default=[100,200,300,400,500,600,700,800,900,1000], help='distances in meters')
    args = parser.parse_args()

    ds, gt, inv_gt, to_geog, to_map = load_dataset(args.dem)
    origin_lat_deg, origin_lon_deg = pixel_to_latlon(gt, to_geog, args.origin_x, args.origin_y)
    origin_lat = math.radians(origin_lat_deg)
    origin_lon = math.radians(origin_lon_deg)
    east, north, up = enu_axes(origin_lat, origin_lon)
    az_rad = math.radians(args.azimuth)
    dir_enu = (
        math.sin(az_rad) * east[0] + math.cos(az_rad) * north[0],
        math.sin(az_rad) * east[1] + math.cos(az_rad) * north[1],
        math.sin(az_rad) * east[2] + math.cos(az_rad) * north[2]
    )
    dir_unit = normalize(dir_enu)
    observer_ecef = latlon_to_ecef(origin_lat, origin_lon)

    print(f"Observer lat/lon: {origin_lat_deg:.9f},{origin_lon_deg:.9f}")
    print(f"Azimuth: {args.azimuth} deg")
    print("dist_m,delta_px,delta_py")
    for dist in args.distances:
        # chord point
        chord_ecef = vector_add(observer_ecef, vector_scale(dir_unit, dist))
        lat_chord, lon_chord = ecef_to_latlon(chord_ecef)

        # geodesic point
        lat_geo, lon_geo = geodesic_point(origin_lat, origin_lon, az_rad, dist)

        px_chord, py_chord = latlon_to_pixel(inv_gt, to_map, math.degrees(lat_chord), math.degrees(lon_chord))
        px_geo, py_geo = latlon_to_pixel(inv_gt, to_map, math.degrees(lat_geo), math.degrees(lon_geo))

        dx = px_chord - px_geo
        dy = py_chord - py_geo
        print(f"{dist:.1f},{dx:.9f},{dy:.9f}")

if __name__ == '__main__':
    main()
