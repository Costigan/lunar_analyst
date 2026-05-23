# SPICE Kernels for Lunar Analysis

This document describes the SPICE kernels used in the Lunar Analyst application for mission analysis, lighting simulation, and terrain visualization. SPICE is an information system the Navigation and Ancillary Information Facility (NAIF) Node of the Planetary Data System (PDS) uses to archive and distribute ancillary data.

## Kernel Types

The SPICE system uses several types of data files, called kernels, each covering a specific aspect of space mission geometry.

### 1. SPK (Spacecraft and Planet Kernels)
- **Extension:** `.bsp` (Binary), `.tsp` (Text)
- **Content:** Ephemeris data (position and velocity) for spacecraft, planets, moons, and other solar system bodies.
- **Lunar Context:** Essential for calculating the positions of the Moon, Sun, and Earth relative to each other and the spacecraft.

### 2. PCK (Planetary Constants Kernels)
- **Extension:** `.tpc` (Text), `.bpc` (Binary)
- **Content:** Physical and cartographic constants, such as size, shape, and orientation (rotation) for planets, moons, and satellites.
- **Lunar Context:** Defines the Moon's orientation and reference frames (e.g., Mean Earth/Polar Axis vs. Principal Axes).

### 3. CK (C-matrix Kernels)
- **Extension:** `.bc` (Binary)
- **Content:** Orientation (attitude) of a spacecraft or a rotating structure on a spacecraft (e.g., a scan platform).
- **Lunar Context:** Necessary for mapping instrument observations (like LRO LOLA or cameras) to the lunar surface.

### 4. LSK (Leapseconds Kernels)
- **Extension:** `.tls` (Text)
- **Content:** Data for converting between Coordinated Universal Time (UTC) and Ephemeris Time (ET/TDB).
- **Lunar Context:** Foundation for all time-based geometric calculations.

### 5. FK (Frame Kernels)
- **Extension:** `.tf` (Text)
- **Content:** Definitions of and relationships between coordinate systems (reference frames).
- **Lunar Context:** Defines lunar-centric and landing-site-specific frames.

### 6. SCLK (Spacecraft Clock Kernels)
- **Extension:** `.tsc` (Text)
- **Content:** Correlation between spacecraft clock and ephemeris time.

## Accessing Kernels

Kernels are hosted by NASA's NAIF and are available through several channels:

1.  **NAIF HTTPS Repository:** Direct access to the file system.
    - [Generic Kernels](https://naif.jpl.nasa.gov/pub/naif/generic_kernels/): Leapseconds, planetary constants, and general ephemeris.
    - [PDS Archived Missions](https://naif.jpl.nasa.gov/pub/naif/pds/): Official mission archives (LRO, GRAIL, Clementine, etc.).
2.  **WebGeocalc:** A web-based interface for performing SPICE calculations without writing code.
    - [WebGeocalc Tool](https://naif.jpl.nasa.gov/naif/webgeocalc.html)
3.  **SPICE Toolkit:** Libraries for C, Fortran, IDL, MATLAB, and Python (`spiceypy`).

## Relevant Lunar Missions

The following missions provide high-fidelity SPICE data for lunar south pole analysis:

- **Lunar Reconnaissance Orbiter (LRO):** High-precision orbit and orientation data, foundational for modern lunar mapping.
- **GRAIL:** Exceptional lunar gravity field data and precise ephemerides.
- **Clementine:** Global lunar mapping data from the mid-1990s.

## References

- [NAIF Official Website](https://naif.jpl.nasa.gov/naif/)
- [SPICE Kernel Types Guide](https://naif.jpl.nasa.gov/pub/naif/toolkit_docs/C/req/kernel.html)
- [PDS Geosciences Node](https://pds-geosciences.wustl.edu/)
- [Lunar Reconnaissance Orbiter SPICE Archive](https://naif.jpl.nasa.gov/pub/naif/pds/data/lro-l-spice-6-v1.0/lrosp_1000/)
