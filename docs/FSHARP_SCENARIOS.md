# Design Document: F# Dynamic Reduction Scenarios



## 1. General capabilities

### A. Support calculations that are not functions of time

* GENERATE TRANSFORMATION MATRIX:
  Given a pixel location in the 2D terrain patch, calculate a 3D
  transformation matrix to transform positions of the objects 
  sun, earth, ground station) in moon mean earth frame to a local
  frame centered at the terrain pixel.  object=[sun, earth, ground station]

### B. Support calculations that are functions of time

* GENERATE OBJECT POSITION at TIME:
  object=[sun, earth, ground station]
  Result is Vector3d.
* GENERATE OBJECT POSITION in PIXEL-LOCAL FRAME at TIME:
  use the transformation matrix to rotate and translate an object's
  position in moon mean earth frame to a pixel's local frame.
  Result is vector3d.
* Generate AZIMUTH and ELEVATION in PIXEL-LOCAL FRAME at TIME:
  Units are degrees.
* GENERATE OBJECT ELEVATION above pixel HORIZON in at TIME:
  This requires loading horizons for a terrain patch.
  Units are degrees.

The primary output of these functions is a 3D array containing a value
(sun_fraction, sun_elevation_over_horizon,
earth_elevation_over_horizon) for each pixel in a 128 x 128 pixel
patch of terrain and for each time in a list of DateTime's.

There can be more than one of these

## 2. Boolean

* compare value against constant (used to compare against a threshold, e.g., Earth_elevation_above_horizon >= 2 degrees)

## 2. Reduction functions

These functions reduce the 3D array along the time axis.

* maximum
* minimum
* average
* maximum contiguous run of true's or false's.  Generally, this can be converted to a duration of that run.

## 3. Return values from .net to python

2D array of values