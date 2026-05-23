from __future__ import annotations

from backend.core.crs_semantics import crs_semantically_equivalent

UNNAMED_SOUTH_POLAR_WKT = (
    'PROJCS["unnamed",GEOGCS["unnamed ellipse",DATUM["unknown",SPHEROID["unnamed",1737400,0]],'
    'PRIMEM["Greenwich",0],UNIT["degree",0.0174532925199433]],PROJECTION["Polar_Stereographic"],'
    'PARAMETER["latitude_of_origin",-90],PARAMETER["central_meridian",0],PARAMETER["scale_factor",1],'
    'PARAMETER["false_easting",0],PARAMETER["false_northing",0],UNIT["metre",1],'
    'AXIS["Easting",NORTH],AXIS["Northing",NORTH]]'
)

NON_EQUIVALENT_SOUTH_POLAR_PROJ4 = (
    "+proj=stere +lat_0=-90 +lon_0=12 +k=1 +x_0=0 +y_0=0 +R=1737400 +units=m +no_defs"
)


def test_crs_semantics_matches_equivalent_polar_stereographic_without_authority() -> None:
    assert crs_semantically_equivalent(UNNAMED_SOUTH_POLAR_WKT, "ESRI:103878")


def test_crs_semantics_rejects_non_equivalent_polar_stereographic() -> None:
    assert not crs_semantically_equivalent(NON_EQUIVALENT_SOUTH_POLAR_PROJ4, "ESRI:103878")


def test_crs_semantics_rejects_none() -> None:
    assert not crs_semantically_equivalent(None, "ESRI:103878")
