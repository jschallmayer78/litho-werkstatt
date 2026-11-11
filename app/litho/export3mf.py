"""3MF-Export: alle Objekte in einer Datei, jedes Teil mit eigenem Filament.

- Kern-3MF mit Basismaterialien (Farbe je Dreieck) -> Farben in jedem Viewer.
- Projekt-Metadaten im PrusaSlicer-Format (Metadata/Slic3r_PE_model.config)
  mit Teilen ("volumes") und Extruder-Zuordnung. Dieselbe Datei wird auch als
  Metadata/model_settings.config abgelegt, die Orca Slicer/Bambu Studio lesen.
"""
import io
import zipfile
from xml.sax.saxutils import quoteattr

import numpy as np

BED_CENTER = (128.0, 128.0)
GAP = 10.0

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="application/xml"/>
 <Default Extension="png" ContentType="image/png"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel-1" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
 <Relationship Target="/Metadata/thumbnail.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>
</Relationships>"""


def _layout(objects):
    """Objekte nebeneinander anordnen. Liefert je Objekt (verschobene Teile, Position)."""
    placed, sizes = [], []
    for obj in objects:
        allv = np.concatenate([p.verts for p in obj.parts])
        lo, hi = allv.min(0), allv.max(0)
        placed.append([(p, p.verts - lo) for p in obj.parts])
        sizes.append(hi - lo)
    total_w = sum(s[0] for s in sizes) + GAP * (len(sizes) - 1)
    x = BED_CENTER[0] - total_w / 2
    pos = []
    for s in sizes:
        pos.append((x, BED_CENTER[1] - s[1] / 2))
        x += s[0] + GAP
    return placed, pos


def write_3mf(objects, slots, thumbnail_png=None):
    """objects: Liste Obj; slots: Liste (name, '#RRGGBB') für Filament 1..n.
    Liefert die 3MF-Datei als bytes."""
    placed, pos = _layout(objects)
    model = io.StringIO()
    w = model.write
    w('<?xml version="1.0" encoding="UTF-8"?>\n'
      '<model unit="millimeter" xml:lang="de-DE" '
      'xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
      'xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06">\n'
      ' <metadata name="slic3rpe:Version3mf">1</metadata>\n'
      ' <metadata name="Title">Lithophane</metadata>\n'
      ' <metadata name="Application">Lithophane-Werkstatt</metadata>\n'
      ' <resources>\n  <basematerials id="1">\n')
    for name, color in slots:
        w(f'   <base name={quoteattr(name)} displaycolor="{color.upper()}FF"/>\n')
    w('  </basematerials>\n')

    cfg = ['<?xml version="1.0" encoding="UTF-8"?>\n<config>\n']
    for n, (obj, parts) in enumerate(zip(objects, placed)):
        oid = n + 2
        w(f'  <object id="{oid}" type="model" name={quoteattr(obj.name)} pid="1" pindex="0">\n'
          '   <mesh>\n    <vertices>\n')
        offs, vols, tri0 = [], [], 0
        base = 0
        for part, v in parts:
            w("".join(f'     <vertex x="{a:.4f}" y="{b:.4f}" z="{c:.4f}"/>\n' for a, b, c in v.tolist()))
            offs.append(base)
            base += len(v)
        w('    </vertices>\n    <triangles>\n')
        for (part, v), off in zip(parts, offs):
            f = part.faces + off
            p1 = part.slot - 1
            w("".join(f'     <triangle v1="{a}" v2="{b}" v3="{c}" pid="1" p1="{p1}"/>\n'
                      for a, b, c in f.tolist()))
            vols.append((tri0, tri0 + len(f) - 1, part))
            tri0 += len(f)
        w('    </triangles>\n   </mesh>\n  </object>\n')

        cfg.append(f' <object id="{oid}" instances_count="1">\n'
                   f'  <metadata type="object" key="name" value={quoteattr(obj.name)}/>\n'
                   f'  <metadata type="object" key="extruder" value="1"/>\n')
        for first, last, part in vols:
            cfg.append(f'  <volume firstid="{first}" lastid="{last}">\n'
                       f'   <metadata type="volume" key="name" value={quoteattr(part.name)}/>\n'
                       f'   <metadata type="volume" key="volume_type" value="ModelPart"/>\n'
                       f'   <metadata type="volume" key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                       f'   <metadata type="volume" key="extruder" value="{part.slot}"/>\n'
                       f'  </volume>\n')
        cfg.append(' </object>\n')
    cfg.append('</config>\n')

    w(' </resources>\n <build>\n')
    for n, (x, y) in enumerate(pos):
        w(f'  <item objectid="{n + 2}" transform="1 0 0 0 1 0 0 0 1 {x:.3f} {y:.3f} 0" printable="1"/>\n')
    w(' </build>\n</model>\n')

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("_rels/.rels", RELS if thumbnail_png else RELS.replace(
            ' <Relationship Target="/Metadata/thumbnail.png" Id="rel-2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail"/>\n', ""))
        z.writestr("3D/3dmodel.model", model.getvalue())
        config = "".join(cfg)
        z.writestr("Metadata/Slic3r_PE_model.config", config)
        z.writestr("Metadata/model_settings.config", config)
        if thumbnail_png:
            z.writestr("Metadata/thumbnail.png", thumbnail_png)
    return buf.getvalue()
