"""OSM + 1 m LIDAR -> local-metre geometry for Blender (run with plain python + pyproj + tifffile).
Local frame: X east, Y north, origin O (OSGB). Heights: DEM metres minus Z0."""
import json, sys, math, numpy as np, tifffile, xml.etree.ElementTree as ET
from pyproj import Transformer
D = sys.argv[1]
O = (441900.0, 325950.0)
dem = tifffile.imread(f"{D}/data/donington_dtm1m.tif").astype(np.float32)   # rows = north->south, 1 m px
E0, N1 = 440800.0, 327200.0                                                  # top-left corner of the WCS request
Z0 = float(dem[int(N1 - O[1]), int(O[0] - E0)])
t = Transformer.from_crs("EPSG:4326", "EPSG:27700", always_xy=True)
root = ET.parse(f"{D}/data/donington.osm").getroot()
nodes = {}
for n in root.iter('node'):
    x, y = t.transform(float(n.get('lon')), float(n.get('lat'))); nodes[n.get('id')] = (x - O[0], y - O[1])
KEEP = {'highway': None, 'barrier': None, 'building': None, 'landuse': ('grass', 'meadow', 'farmland'), 'natural': None,
        'amenity': ('parking',), 'area:highway': None, 'surface': None}
ways = []
for w in root.iter('way'):
    tags = {k.get('k'): k.get('v') for k in w.iter('tag')}
    ids = [nd.get('ref') for nd in w.iter('nd')]
    pts = [nodes[i] for i in ids if i in nodes]
    if len(pts) < 2: continue
    if not any(abs(x) < 1400 and abs(y) < 900 for x, y in pts): continue
    k = next((k for k in KEEP if k in tags and (KEEP[k] is None or tags[k] in KEEP[k])), None)
    if not k: continue
    ways.append({"id": w.get('id'), "kind": f"{k}={tags[k]}", "name": tags.get('name', ''), "tags": tags,
                 "closed": ids[0] == ids[-1], "pts": [[round(x, 3), round(y, 3)] for x, y in pts], "nodes": ids})
# DEM crop around the circuit, local frame: rows south->north for convenience
x0, x1, y0, y1 = -1100, 1700, -1150, 1250
c0, c1 = int(O[0] + x0 - E0), int(O[0] + x1 - E0); r0, r1 = int(N1 - (O[1] + y1)), int(N1 - (O[1] + y0))
crop = dem[r0:r1, c0:c1][::-1] - Z0
np.save(f"{D}/data/dem_local.npy", crop.astype(np.float32))
json.dump({"origin_osgb": O, "z0": Z0, "dem": {"x0": x0, "y0": y0, "res": 1.0, "shape": list(crop.shape)}, "ways": ways},
          open(f"{D}/data/geo.json", "w"))
print(f"Z0={Z0:.2f}  ways={len(ways)}  dem crop {crop.shape} z {crop.min():.1f}..{crop.max():.1f}")
