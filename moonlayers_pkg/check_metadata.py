import rasterio

with rasterio.open('data/malapert-psr.tif') as src:
    print(f"Photometric: {src.profile.get('photometric', 'Unknown')}")
    print(f"Tags: {src.tags()}")
    print(f"ColorInterp: {src.colorinterp}")
