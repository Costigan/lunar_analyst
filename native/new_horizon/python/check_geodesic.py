import math
import csv
from osgeo import gdal, osr
MOON_RADIUS_M = 1_737_400.0
path = r"/d/datasets/viper_v71_2024_medium/other/dem.tif"
ds = gdal.Open(path)
gt = ds.GetGeoTransform()
lon, lat = 30.859012529369075, -85.42974349391376
az = math.radians(280.25)
dist = 1216.8
ang = dist/MOON_RADIUS_M
sin_lat = math.sin(math.radians(lat))
cos_lat = math.cos(math.radians(lat))
sin_ang = math.sin(ang)
cos_ang = math.cos(ang)
sin_az = math.sin(az)
cos_az = math.cos(az)
lat2 = math.asin(sin_lat*cos_ang + cos_lat*sin_ang*cos_az)
lon2 = math.radians(lon) + math.atan2(sin_az*sin_ang*cos_lat, cos_ang - sin_lat*math.sin(lat2))
print(math.degrees(lat2), math.degrees(lon2))
