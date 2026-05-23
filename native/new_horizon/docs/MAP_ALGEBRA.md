Adding to the new_horizon solution, I want to write a new library that
implements map algebraic operations on geotiff data files and horizon files.

# Taxonomy and Algebra of Geospatial Operations for Lunar Data Pipelines

## Historical Foundations: Cartographic Modeling and Map Algebra

Geospatial analysis workflows have long been represented as sequences of
operators which combine various spatial datasets to produce
decision-support maps. One foundational framework is Map Algebra,
developed by C. Dana Tomlin in the 1970s–80s, which defines a set of
primitive raster operations that are closed (input and output are
rasters).  Map Algebra classifies raster operations by the spatial scope
of data each operation uses. For example, local operators compute a new
raster where each output cell is a function of the values at the same
location in one or more input rasters (e.g. cell-by-cell addition of two
maps). Focal (neighborhood) operators use a window around each cell
(e.g. calculating slope or average from a 3x3 neighborhood of elevation
values) . Zonal operators aggregate values over regions of identical
class (e.g. computing mean elevation for each geologic unit zone) . Some
definitions also include global operations that consider the entire
raster (e.g. finding the highest value in the whole grid, or performing
cost-distance analysis across the map) . Tomlin’s Map Algebra provided a
formal operator algebra for raster GIS, meaning these operations can be
combined sequentially and the result of any operation is another map
that can feed into the next . This concept of chaining geospatial
functions became fundamental to building complex dataflow graphs in GIS:
essentially, each node is an operation (transforming data), and edges
pass intermediate map layers between operations.

Early GIS also supported analogous operations for vector data. The map
overlay technique pioneered by McHarg and others (manually layering
transparent maps) was translated into digital vector overlay operations
(like polygon intersect and union) in the 1970s . Common vector GIS
geoprocessing tasks include: buffering (creating zones at a specified
distance around features), spatial joins and queries (finding features
within a region or near other features), and topological overlays
(intersect, union, difference of polygons for suitability
analysis). While not formalized as an “algebra” in the same way as
raster map algebra, these vector operations are well-standardized in GIS
software. Modern spatial databases and libraries (e.g. PostGIS or
Shapely) provide a rich set of vector functions (buffer, clip, dissolve,
etc.), which can be combined logically (often via SQL or scripts) to
achieve complex spatial queries . In essence, the idea of an operator
library for geospatial analysis – containing both raster and vector
operations – has been explored and expanded for decades, forming a de
facto taxonomy of what can be done to geographic data.

## Taxonomy of Geospatial Operations

Contemporary GIS platforms and toolboxes catalog hundreds of geospatial
operations, often grouped by their purpose or input/output data
types. For raster (grid) data, the operations range from simple
arithmetic or reclassification to advanced modeling. For example, Esri’s
Spatial Analyst (and equivalently open-source tools) categorize
functions as: Local Math (pixel-wise arithmetic, logarithms, etc.),
Logical and Conditional (applying boolean conditions on rasters),
Neighborhood (focal filters, convolution, moving window stats), Zonal
(region-based calculations), and so on . There are specialized groups
like

Surface analysis (deriving slope, aspect, hillshade from a DEM) ,
Hydrology (flow direction, watershed delineation), Distance analysis
(Euclidean or cost-weighted distance and proximity), Density estimation
(e.g. kernel density on point data), Interpolation (creating continuous
surfaces from sample points), Reclassification (regrouping raster
values), Generalization (smoothing or simplifying rasters), and many
others . A noteworthy category for lunar applications is Solar Radiation
analysis – tools that compute insolation or sun exposure over time given
a terrain model . This is particularly relevant for the Moon’s poles
where illumination is extreme (e.g. mapping areas of permanent shadow or
near-eternal light). Modern GIS software provides such solar irradiance
modeling functions, which can be key for mission planning in polar
regions . In summary, the community has developed a taxonomy of raster
operations that includes virtually any spatial computation: from simple
map algebra expressions to complex environmental models
(e.g. groundwater flow, wildfire spread models, suitability indices),
each of which can be seen as a node in a processing graph.

For vector operations, common categories include overlay (combining
multiple layers by intersecting or unioning geometries and attributes),
proximity (buffering features, nearest-neighbor analysis), network
analysis (path finding, connectivity on road networks), geometry
processing (simplification, smoothing, convex hull, etc.), and
conversion operations. Many vector tools produce new geometries or
select subsets based on spatial relationships (inside, adjacent,
etc.). These have been standard GIS functions for years and are often
combined to answer complex questions (for example, “find areas where a
certain slope raster is high (raster query) and within 1 km of a landing
site (vector buffer and intersect)” – mixing raster criteria and vector
proximity operations). Recent research in GeoAI has even encapsulated
such classic operations in natural language interfaces; for instance,
one study’s “GeoGPT” system enabled an LLM to invoke Buffer, Clip, and
Intersect operations in sequence by understanding a user’s query . This
demonstrates that the repertoire of vector operations is well-defined
enough to be exposed as high-level tools that an AI or workflow engine
can pick and mix.

Raster-vector conversion tasks form another important part of the
taxonomy. It’s often necessary to go between raster and vector
representations. For example, generating contour lines from an elevation
raster (raster-to-vector) is a classic operation: the software traces
isolines through the grid to produce vector polylines for given
elevation intervals . Another common conversion is polygonizing a
classified raster – converting contiguous regions of equal raster value
into vector polygons . This is useful for delineating features like land
cover zones or crater outlines from image classifications. Conversely,
rasterizing vector data (turning vector shapes into a raster grid) is
often done to integrate with raster analysis (e.g. converting a geologic
map into a raster to combine with elevation data in map algebra). These
conversion operators are standard in libraries like GDAL
(e.g. gdal_contour to get polylines, gdal_polygonize for polygons) . The
ability to convert data models means a processing graph can include
steps where, say, a raster result is vectorized for output to a GIS, or
a vector constraint (like a region of interest) is rasterized to mask an
analysis.

## Composition of Operations and Workflow Systems

Importantly, these operations can be chained into a directed acyclic
graph (DAG) or pipeline to produce complex outputs. This was recognized
early: Tomlin’s work introduced a simple scripting language to chain map
algebra operations (including conditional logic) in a cartographic
modeling workflow . Today, GIS software offers visual workflow builders
(like ArcGIS ModelBuilder or QGIS Processing Modeler) where users
drag-and-drop operation nodes and connect them, effectively designing a
dataflow graph. Each node represents a task (e.g. reproject raster,
calculate slope, threshold to binary, polygonize to vector), and the
connections pass the output of one as input to the next. Such frameworks
implicitly define an operator algebra – the idea that any valid sequence
(composition) of operations is itself a computable transformation from
initial data to final result. In general, the algebraic properties (like
commutativity or associativity) depend on the operations (e.g. some
reclassification and arithmetic operations commute, others don’t), but
the key is that the system has a well-defined way to interpret and run
the graph.

There have also been formal efforts to standardize and encode these
workflows. The Open Geospatial Consortium (OGC) developed standards like
Web Processing Service (WPS) for publishing geospatial operations on the
web, and Web Coverage Processing Service (WCPS) which is essentially a
high-level query language for raster data analysis . WCPS provides an
SQL-like syntax to chain raster operations (filtering, slicing, scaling,
arithmetic, etc.) in one expression, with well-defined semantics for the
result . This is an example of an operator algebra on raster “coverages”
that is machine-readable and can be composed into larger
queries. Similarly, many GIS scripting APIs (e.g. Python with ArcPy, or
R’s raster package) let users script sequences of operations; the order
and combination form the “algebra” of the workflow. In database
research, array databases like Rasdaman have their own query languages
(rasQL) that treat raster operations in a compositional manner, and
spatial SQL extensions treat vector operations as composable functions
in queries. All these show that prior work has not only enumerated the
operations available, but also how they can be systematically combined.

From a scientific workflow perspective, prior projects have built
ontologies and catalogs of analysis tasks.  For instance, the UN
Geographic Information Framework and others have ontologies of
geospatial functions (to enable interoperability) . Recent GeoAI
research, as you noted, is looking at translating natural language to
such workflows. Approaches like ChatGeoAI and related “GIS Copilot”
prototypes use an LLM with a predefined library of operations (buffer,
intersect, etc.) or a fine-tuned model that can generate code to call
GIS functions . These efforts build directly on the comprehensive work
done by the GIS community in defining and naming
operations. Essentially, because the taxonomy of geospatial operations
is well-established, an LLM can be taught to act as a translator from a
human description (e.g. “find highsunlight areas on south pole peaks and
output as polygons”) into a sequence of known operations (e.g.
hillshade/illumination analysis → threshold sunny areas → vectorize
polygons).

## Planetary Data Pipelines and Specialized Operations

Your focus on Lunar Reconnaissance Orbiter (LRO) data and lunar mission
planning adds some domain-specific context, but it generally does not
change the fundamental operator taxonomy. The Moon’s south polar region,
however, does introduce specialized analysis needs – and indeed there
are operations tailored to those. For example, assessing sunlight and
shadow over time at high lunar latitudes requires computing illumination
conditions using topography and solar geometry (analogous to the “solar
radiation” functions in GIS) . Another example is thermal modeling:
converting solar input, material properties, and perhaps measured
brightness temperatures into a surface temperature map is essentially a
raster-to-raster operation (possibly implemented by a custom physical
model). Such a model might involve multiple steps – e.g. first computing
expected insolation, then solving a heat transfer equation for the
regolith. This can be seen as a composite operator in your graph (and
could be broken into sub-operations like “calculate net radiation”,
“apply thermal inertia filter”, etc., if needed). Notably, the LRO
Diviner instrument has produced gridded temperature datasets and derived
products that integrate models and other data. For instance, Diviner’s
higher-level products include not just measured brightness temperatures
but also derived fields like thermal inertia and rock abundance that are
generated by combining thermal measurements with topographic models and
other inputs . In workflow terms, those derived products result from a
pipeline of operations: (radiometric calibration → map projection →
mosaicking → applying a thermal model with topography). NASA’s planetary
data processing toolkit ISIS (Integrated Software for Imagers and
Spectrometers) provides many such operations geared for the Moon and
planets, categorized similarly to terrestrial GIS but with
mission-specific flavor. ISIS has a taxonomy of functions including
Radiometric Correction, Photometric correction (sun-angle corrections),
Map Projection (reprojecting images onto cartographic grids), Mosaicking
(stitching images), Topography (e.g. generating digital elevation models
or slope maps from stereo or altimetry), and more . These roughly
correspond to the types of tasks you would chain together for lunar map
product generation. For example, creating an LRO mosaic of the south
pole might involve: ingest images → calibrate (radiometrically) →
project to a common map grid → mosaic → derive elevation (if stereo
pairs or laser altimetry available) → compute slopes and illumination →
threshold slopes for safe landing zones (raster local operation) →
vectorize the safe areas (raster-to-vector) → intersect with permanently
shadowed regions (vector overlay). Each step in that chain is drawn from
the “catalog” of known operations.

It appears that previous work has thoroughly identified and categorized
the building-block tasks for geospatial and planetary data
analysis. From Tomlin’s map algebra and its extensions, to the
exhaustive function lists in modern GIS/ISIS software, to standards like
OGC’s WCPS, the community has essentially created an operator library
and even formal languages (algebras) to combine them. Your idea of using
an LLM as a front-end to this is well-aligned with current trends: the
LLM would be leveraging this prior art by mapping user requests to
sequences of these well-defined operations. In developing your system,
you can draw on the comprehensive taxonomy from GIS science: include
raster-to-raster operators (arithmetics, filters, interpolations,
terrain analyses, image classifications, physical models),
raster-to-vector ones (threshold and polygonize, edge detection to
linework, contouring), vector-to-vector ones (overlays, buffers, network
routing), and vector-to-raster as needed. Each of these categories has
been explored in past literature and implementations. By having “both” –
a rich taxonomy of operators and an algebra/ system to compose them –
you’ll ensure the LLM can flexibly generate complex processing
graphs. The good news is that this foundation is already laid by prior
work: you are essentially standing on decades of GIS and remote sensing
experience, just translating it into a new AI-assisted paradigm. The
south polar focus will mainly influence which specific operations are
invoked (e.g. lots of illumination and thermal analysis), but it doesn’t
require a new taxonomy – rather, it draws upon a subset of existing
operations (some possibly in novel combinations).

In summary, yes, this problem has been worked on before in the sense
that the geospatial community has defined the “vocabulary” of data
transformations and demonstrated how to chain them for complex
analyses. You can confidently build on that comprehensive body of
work. A robust set of operations (GIS functions, remote sensing
algorithms, and planetary data processes) is available and documented,
and frameworks exist that treat these operations as composable units
(from Map Algebra scripts to modern workflow engines) . By surveying
those existing taxonomies and formalisms, you can assemble a complete
library of operations for your lunar mission-planning assistant. The LLM
can then orchestrate these like Lego blocks, turning a scientist’s
English request into a precise sequence of data processing steps –
essentially automating what GIS analysts have been doing manually with
tools like ModelBuilder or ISIS scripts. All the pieces are there; your
task is to integrate them into an intelligent system. The prior art’s
comprehensive overviews and categorizations of geospatial operators will
be an invaluable guide and reference as you design the operator graph
schema for lunar data products.



Scenarios

* view a geotiff in the context of the whole pole
* load any layer from moon trek or quickmap
* horizons
  * render the horizon from an observer on or above the surface
  * plot the path of the sun and earth
  * plot the path of a satellite
  * generate view periods from an observer on or above the surface
  
* lighting
  * generate lighting maps for a dem for an observer at or above the surface
  * render time-series lighting on a dem
  * generate accurate synthetic imagery
  * generate a light curve for an observer on or above the surface
  * generate lighting windows
  * generate average/cumulative sun
  * generate PSR maps
  * generate shadow depth time-series maps
  * generate safe haven maps

* craters
  * automatically mark the position and sizes of craters
  * adjust the craters' locations based on a DEM
  * Generate d/D statistics
  * Generate area counts
  * Generate a hazard map

* path planning
  * multi-constraint path planning like in TD

* radio
  * generate an frame error rate
