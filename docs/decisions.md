# Decision log

Each entry says what I chose, why, what I rejected and where it breaks. Where my first version
was wrong, the entry says what went wrong and what changed. The numbers come from the tests, the
stage reports and `docs/accuracy.md`.

## Area: a local equal-area projection per part

I measure area in a Lambert azimuthal equal-area (LAEA) projection on the WGS84 datum, in
metres. The code builds a new projection for every part of every feature, centred on that part's
centroid. For the park in `samples/sample.kml` it is
`+proj=laea +lat_0=52.505 +lon_0=13.41 +datum=WGS84 +units=m`, and the response returns that
string in `area_crs` next to the value.

Area is the property I need, and LAEA is designed to keep it. Every projection keeps some
properties and distorts others. Web Mercator is useful for web maps, but it does not keep area,
so it is the wrong map to measure polygons in.

The centring happens in `project_local` (`app/geo/crs.py`).
I centre the projection on the shape’s centroid because projections introduce less distortion near their centre. This makes the area measurement more reliable for relatively small shapes than using one fixed projection centre for every feature.

Each part of a MultiPolygon or a collection gets its own projection, and the parts are summed
(`_measure_parts` in `app/geo/measure.py`). A test checks that two distant parts measured
together give the same total as the sum of each part measured alone. If two parts are thousands of kilometres apart, one shared projection centred on one part could introduce significant distortion when measuring the other. By giving each part its own local projection, I can measure both more reliably and then add their areas together.

I rejected one projection for all features. Web Mercator would have been the simplest, but at
60N it makes a 1 degree box about four times too big (`tests/test_measure.py`, the Web Mercator
test). I also did not use `pyproj.Geod` as the method. The assignment asks for a transformation
to a projected CRS before measuring, so Geod, which measures on the ellipsoid directly, is only
the independent check in the tests.

Where it breaks: large polygons. My edges are straight lines in the local projection, while Geod
joins the corners with geodesics, so the two disagree about where a long edge runs. For boxes at
the equator the difference from Geod is -0.0038% at 1 degree, -0.38% at 10 degrees and -1.5% at
20 degrees. When the edges are cut every 0.01 degrees, so that both methods follow the same
meridians and parallels, the difference is 0.0000% for every box in `docs/accuracy.md`. The gap
comes from how an edge is drawn, not from the projection.

## Length: a local azimuthal equidistant projection per part

Lines are measured in an azimuthal equidistant (AEQD) projection, again one per part and centred
on the part's centroid. AEQD keeps true distances measured from its centre, which is what a
length needs. The centre has to be near the line: an AEQD map centred on Berlin would give an
inaccurate length for a footpath in Sydney.

The plan named the UTM zone of the centroid as the alternative. I chose local AEQD because it centres the projection on the line itself and preserves distances from that centre. UTM uses fixed geographic zones, so a line near a zone boundary or spanning a large area may experience more distortion than expected.

Where it degrades: AEQD is exact only along lines through its centre. Two 20 degree legs (about
2,200 km each) come out 0.126% longer than Geod, a 5 degree L shape 0.008% longer, and a line
through the centre matches exactly (`tests/test_measure_limits.py`). In `docs/accuracy.md` the
worst length error is +0.0138%, the diagonal of a 20 degree box at latitude 70. At the other
extreme, a line shorter than about half a millimetre measures exactly 0 m: 0.56 mm at the
equator gave 0, while 0.72 mm at 50N measured correctly.

## Always reprojecting through WGS84

Every geometry goes from its source CRS to EPSG:4326 first (`to_wgs84` in `app/geo/crs.py`),
even when the file is already in a projected CRS such as UTM. Z and M values are dropped before
that, because the API measures horizontal area and length. Every `Transformer` in the code is
built with `always_xy=True`.

I convert every geometry to WGS84 first so that all input files use the same geographic coordinate system, regardless of their original CRS. Then I create a suitable local projection for the measurement, rather than assuming the file’s existing projection is accurate for area or length calculations.
Without always_xy=True, PyProj may follow the CRS’s official axis order, which for EPSG:4326 can be latitude first and longitude second. My code expects X,Y order, meaning longitude first and latitude second, so the coordinates could be swapped and produce incorrect locations and measurements.

What the tests show: a box at 60N given in Web Mercator (EPSG:3857) measures the same as Geod
after this step, while its area in Web Mercator units is more than three times the true area.
The CRS audit compared five source CRSs on a 0.1 degree box: EPSG:4326, UTM zone 33N, EPSG:3857,
a CRS in US survey feet (EPSG:2263) and NAD83 (EPSG:4269). All five were within 0.00003% of Geod,
and an ESRI style `.prj` inside a zipped shapefile gave the same results
(`tests/test_crs_sources.py`).

I rejected measuring a projected file in its own units.

Where it breaks: the NAD83 to WGS84 step is approximate (pyproj lists it at about 4 m), although
on the 0.1 degree box it changed the area by only about 0.00003%. A geocentric CRS such as
EPSG:4978 in a `.prj` is accepted and gives meaningless results (a line measured 0 m at the
north pole).

## pyshp instead of pyogrio

I read shapefiles with pyshp and parse KML with my own code. The project uses no GDAL,
GeoPandas, fiona or pyogrio. The reason is installation: every dependency installs from a pip
wheel, with no system libraries, and CI installs everything from `requirements.lock` on Python
3.11 and 3.12 without extra steps.

pyogrio and fiona read many formats through GDAL. I gave that up, and I also took on the ZIP
handling around pyshp myself: zip slip checks, size and entry caps counted on the bytes actually
read, exactly one shapefile per ZIP, and reading the `.cpg` and `.prj` members.

What went wrong with pyshp along the way:

- When the `.shp` and the `.dbf` held different numbers of records, pyshp silently cut the file
  short. That is now a 400.
- Deleted `.dbf` records could have shifted attributes onto the wrong shapes. Shapes and records
  are now read by position, and deleted records are skipped.
- The mutation test found three exceptions from the ZIP and pyshp side that nothing caught: a
  `.cpg` with a NUL byte, an unsupported ZIP version and a corrupt ZIP directory offset. Each
  would have been a 500. They are now a warning or a 400.

Where it breaks: pyshp raises `IndexError` for a polygon ring with one point, and that still
fails the whole upload with 400. pyshp also ships no type stubs, which is part of why mypy is not
in CI.

## KML parser

I parse KML with my own code on top of `defusedxml`. It walks the file with `iterparse` and
refuses any DTD. The plan named the two XML risks: entity expansion (the billion laughs file)
and external entities (XXE). defusedxml blocks both, and the tests include an entity bomb and an
XXE file. With my own parser I also decide what each Placemark becomes: Point, LineString,
Polygon with any number of holes, MultiGeometry, or a bare LinearRing, which is read as a
LineString so its length is measured. Properties come from `name`, `description`,
`ExtendedData/Data` and `SchemaData/SimpleData`.

I rejected reading KML through GDAL, for the same installation reason as above.

What went wrong first: there was no depth limit. Nested `MultiGeometry` elements were read
recursively, and only a recursion error stopped a very deep file. The parser now stops at depth
50 while reading, and 10,000 nested levels give a clean 400. The mutation test also found that
an XML declaration naming an unknown encoding raised a `LookupError` that nothing caught. That
would have been a 500 for what is really bad input, so it is now a 400.

Where it breaks: KMZ (a zipped KML) is not supported, and because it is a ZIP without a `.shp` it
gets 400 rather than 415. `gx:Track` and other geometries the parser does not know are kept as
features with no geometry and a warning. A truncated KML is 400 in UTF-8 but 415 in UTF-16.

## Failure policy

Before any code, I set the failure policy in `plan.md`. Problems fall into four groups:

- A file that cannot be read at all (a corrupt or unsafe ZIP, broken XML, a wrong file type,
  missing shapefile members) gets a 4xx and nothing is stored. No file id exists.
- A file that can be read but not measured gets a `FAILED` record and a 422, and the body
  carries the record's id in `error.file_id`. Examples are no `.prj` with coordinates that are
  not lon/lat, a feature wider than 180 degrees of longitude, and an invalid polygon over the
  repair limit.
- A problem in one feature (malformed or non finite coordinates, a coordinate outside lon/lat,
  a geometry type the parser does not know) keeps that feature with `geometry: null` and a
  warning. The rest of the file is measured and stored, and feature indexes stay stable.
- A busy server answers 503 and stores nothing (see "Processing inside the request").

If a ZIP and its shapes are readable but
the CRS is missing, I do not guess one, because a guess could give a wrong measurement. Storing
the attempt as `FAILED` lets the client look up why it failed later. And a mistake in the
uploaded file should never come back as a 500: the input is wrong, not the server.

What a 422 leaves behind, in the code: a `FAILED` record with `feature_count` 0 and no feature
rows. `GET /api/files/{id}/` returns it with its `error`, and `GET /api/files/{id}/measurements/`
answers 409 `file_failed` with the stored error. When an upload gets a 422, the client receives an error explaining why the file couldn’t be measured, along with a file ID. The client can use that ID to check the file’s status and stored error through the file details endpoint, but requesting measurements returns a 409 because the file failed processing.

What went wrong first: coordinates outside lon/lat. At first the range check lived only in
`measure()`, so one bad coordinate failed the whole file with 422. The check also allowed a
tolerance of 0.000001 degrees, so a latitude of 90.0000005 passed it, the projection returned a
value that is not a finite number, and the database insert crashed with a 500. Now the check has
no tolerance, and the parsers apply it per feature (the third group above). That change had a
side effect: a KML written entirely in projected numbers became a 201 full of empty features. So
a file that has features, but none with a usable geometry, is now a 422 again, with the message
"none of the N features has a usable geometry".

The range check in `measure()` is still there. It turns non finite or out of range coordinates
into a `MeasurementError`, and the service turns that into the `FAILED` record and the 422.
When the parser finds a feature with invalid coordinates, it skips that geometry and records a warning instead of failing the entire file immediately. If every feature ends up without usable geometry, the service marks the file as FAILED, stores the error, and returns a 422 explaining that none of the features could be measured. If at least one feature has usable geometry, the file can still succeed while retaining the invalid features with warnings.

Where it breaks: the per feature rule covers only what the parsers see. A feature that fails
inside `measure()` (wider than 180 degrees, an invalid polygon over 350 vertices, more than
20,000 parts after repair) still fails the whole file with 422. A shapefile ring with one point
fails the whole file with 400. `FAILED` records are never deleted, because the API has no delete
route.

## Missing `.prj`

A shapefile without a `.prj` has no CRS. If every coordinate fits longitude +/-180 and latitude
+/-90, I assume EPSG:4326 and add the warning "no .prj file found; assumed EPSG:4326 because all
coordinates fit lon/lat". Otherwise the file is unmeasurable: 422 and a `FAILED` record. A
`.prj` that pyproj cannot read is also a 422.

The numbers alone do not say where a point is. In UTM zone 33N (EPSG:32633), `391390, 5817855`
is the south west corner of the sample park in Berlin, and the same pair means a different place
in another CRS. So I only accept the one case where the numbers can be lon/lat degrees, and I do
not guess a projected CRS.

I rejected refusing every shapefile without a `.prj`. I allow the fallback because some Shapefiles without a .prj file may still contain valid longitude and latitude coordinates. If all coordinates fit within the valid longitude and latitude ranges, I assume EPSG:4326 and return a warning instead of rejecting the file outright. If the coordinates don’t fit those ranges, I refuse to guess the CRS because the same numbers can represent completely different locations in another coordinate system.

Where it breaks: the fallback cannot tell degrees from small projected numbers. Checked on
2026-10-10 with a scratch script (not a committed test): a 40 m by 40 m square in a local
projected CRS, with corners at 10,10 and 50,50 and no `.prj`, is read as degrees and measures
16,039,424,623,573 m2, with only the warning. An empty or whitespace only `.prj`, or one with a
byte order mark before valid WKT, gives 422, although a missing `.prj` falls back.

## Processing inside the request

The upload is parsed, measured and stored inside the POST request, and the response is 201 with
the finished result. There is no background queue, so the stored status is only ever
`COMPLETED` or `FAILED`. The `PROCESSING` status from `plan.md` never appears. The plan's reason
was that the files are small and synchronous code is simpler to test.

What went wrong first showed up under load. In Stage E, 60 uploads of 1,500 polygons each were
sent at once, and some failed with a 500 because the SQLite write lock was busy. WAL mode and a
30 s busy timeout still left 19 of the 60 as 500s. In the next phase, a locked database and an
exhausted connection pool became 503 `database_busy` with `Retry-After: 2`: 36 uploads returned
201, 24 returned 503 and none returned 500. The security review then showed this was still poor.
A client waited 30 s only to be told to retry in 2 s. With 45 uploads waiting, `/health` took
27.2 s. One 6.5 MB ZIP holding 500,000 line parts took about five minutes of CPU.

What changed: at most four uploads are parsed and measured at once. Another upload waits up to
2 s for a slot and then gets 503 `server_busy`. The database wait is 5 s, both 503s send
`Retry-After: 5`, an upload may hold at most 20,000 lines and polygon rings, and `/health` is an
`async` function. The same 60 upload test then gave 4 x 201, 56 x 503 and no 500, all within
11.2 s, and `/health` answered in 7.9 ms (median) and 965 ms at worst.

`/health` got fast because, as an `async` function, it no longer waits for a free thread in the
worker pool that the uploads were holding. It answers directly on the event loop. The uploads
did not get faster, because parsing and measuring are CPU bound. The four slots limit how many
run at once, so the server is not overwhelmed.

What a 503 means for a client, in the code: nothing was stored, and `Retry-After` is 5 seconds.
`server_busy` means all four slots stayed busy for 2 s; `database_busy` means the write lock or a
pooled connection was not free within 5 s. If the client receives a 503, it should wait for the recommended five seconds using the Retry-After header and then retry the upload. The server returns this error before storing anything, so the failed attempt doesn’t create a file record that could be duplicated by the retry.

I rejected background jobs with a `PROCESSING` status for now; they are in Future scope. I did
not add more server processes either.

Where it breaks: in the 60 upload test, 56 clients have to retry. More slots would not add speed
in one process: each of the four accepted uploads took up to 11 s, against 1.93 s for one upload
alone. Peak memory is still roughly four uploads' worth (25 MB upload, up to 100 MB of
decompressed members, and the geometries).

## Antimeridian

Stage A found the problem. A feature written with wrapped longitudes (179, then -179) was read as
358 degrees wide. A 2 degree box came out about 3,700 times too large, and a line from 179 to
-179 along the equator measured 39,852 km instead of 222 km. Both came back with
`is_valid: true` and no warning. I could have rejected such features. I chose to unwrap them.

The rule: if a part spans more than 180 degrees of longitude, the code also tries it with
negative longitudes shifted by +360 and keeps the narrower version, with a warning. If both
versions are still wider than 180 degrees, the file is unmeasurable (422). The code tries both
instead of always shifting, because a span over 180 degrees suggests a crossing but does not
prove one, and shifting every geometry blindly could change what other shapes mean.

Two more mistakes came later. The CRS audit found that the whole world, `box(-180, -90, 180, 90)`,
measured 0 m2 with only a warning. The shift moved -180 onto 180, the box collapsed to zero
width, and zero counted as narrower. The true area is about 510 million km2. A shift that
collapses a feature to zero size is now a 422. Then the security review found that validity was
checked and polygons were repaired before the unwrap. A wrapped box and a small box at longitude
10 in one MultiGeometry measured about 848 billion m2 against about 36.9 billion m2 from Geod
(+2197%), and a hole across 180 was added to the area instead of subtracted. Now every part is
unwrapped first, and only then checked and repaired.

The box at longitude 10 was involved even though it is far from 180. In the raw numbers, the
wrapped box's longitudes run from -179 to 179. Before unwrapping, the antimeridian-crossing box looked like it stretched from −179° to 179°, covering almost the whole world. The small box at longitude 10° appeared to overlap that huge band, so Shapely treated the combined geometry as invalid and make_valid() merged them during repair.

Where it breaks: the rule cannot know what the author meant. `box(-170, 0, 170, 10)` is read as
a 20 degree box across 180, not a 340 degree box, and only the warning says so. A feature written
with continuous longitudes past 180 (179 to 181) is outside the lon/lat range, so it loses its
geometry with a warning.

## Invalid polygons

The plan said: measure what `make_valid` returns, report `is_valid: false` with shapely's reason,
and never crash. Lines are flagged but not repaired.

What went wrong first: repair ran only when every part of a feature was a polygon. A
MultiGeometry with two identical 1 degree polygons and a point was never repaired, so its area
was counted twice (24.6 billion m2 instead of 12.3 billion). Two overlapping polygons were
repaired, but with the default `make_valid` method, which dropped the overlapping area instead of
counting it once (73.8 billion m2 instead of 86.1 billion). The security review also found the
cost problem: a star shaped polygon with 141 vertices took about 30 s in `make_valid`.

What the code does now: all polygons of one feature, including polygons inside a mixed
collection, are checked together and repaired together with `make_valid` using the "structure"
method, which merges overlaps. When the repair changes the area, a warning gives both numbers. An
invalid feature whose polygons hold more than 350 vertices in total is not repaired, and the file
gets 422. I check the polygons belonging to a feature together and use Shapely’s make_valid() with the structure method to repair invalid geometry and merge overlapping areas. If the repair changes the area, I add a warning showing the original and repaired measurements. To prevent expensive repairs from blocking the server, I reject invalid features whose polygons exceed 350 vertices in total, while valid polygons are still measured regardless of their vertex count.

The 350 vertex cap applies only to invalid polygons that need repair. A valid coastline with
50,000 vertices is still measured. The cap limits the worst case time, but it can reject an
invalid polygon that would have been cheap to repair. The worst shape found, a star, takes about
1.5 s at 350 vertices and over 2 s at 401. A 10,000 vertex bow tie would repair in about 0.01 s,
but it is rejected, because the cap counts vertices, not crossings.

Where it breaks: besides the bow tie, polygons that share an edge inside one feature are
reported invalid and measured as their union.
