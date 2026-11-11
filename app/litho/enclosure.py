"""Boden und Deckel mit Schraubverschluss für den Zylinder-Lampenschirm.

Der Lampenschirm bekommt oben und unten ein Außengewinde auf seinem vollen
Rand, Boden und Deckel das passende Innengewinde. Der Deckel ist dasselbe
Bauteil wie der Boden (ohne Vertiefung), nur zum Aufschrauben umgedreht –
deshalb ist das obere Gewinde das um 180° gedrehte untere Gewinde und beide
haben dieselbe Drehrichtung.

Der Boden hat in der Mitte eine Vertiefung für die Lichtquelle.
"""
import math

import manifold3d as m3d
import numpy as np

from .common import Obj, Part

PITCH = 3.0          # Steigung mm
DEPTH = 1.2          # Gewindetiefe (radial) mm, Flanken 45° -> stützfrei druckbar
CREST = 0.3          # Breite der Gewindespitze (axial) mm
TURNS = 2.5          # Gewindegänge
THREAD_LEN = PITCH * TURNS
OVERLAP = 0.6        # so weit greift das Außengewinde in den Rand (sichere Vereinigung)
WALL = 2.4           # Wandstärke außerhalb des Innengewindes
CHAMFER = 0.8        # Fase unten außen
LID_PLATE = 2.5      # Dicke der Deckelplatte
CLEAR_LIGHT = 0.4    # Spiel pro Seite in der Vertiefung
MIN_BOTTOM = 1.0     # Restmaterial unter Vertiefung bzw. Kabelkanal
POCKET_LEAD = 0.6    # Fase an der Kante der Vertiefung
SEG = 180            # Winkelauflösung des Gewindes


def rim_height():
    """Mindesthöhe des vollen Rands am Lampenschirm."""
    return THREAD_LEN + 1.0


def _helix(r_root, r_crest, height, crest=CREST):
    """Eingängiges Trapezgewinde als Volumen (Kurvenscheibe, verdrillt extrudiert)."""
    run = r_crest - r_root                 # 45°-Flanken: axialer Weg = radiale Tiefe
    base = crest + 2 * run
    if base >= PITCH:
        raise ValueError("Gewindeprofil passt nicht in die Steigung.")

    def prof(s):
        s = s % PITCH
        if s < run:
            return r_root + s
        if s < run + crest:
            return r_crest
        if s < base:
            return r_crest - (s - run - crest)
        return r_root

    th = 2 * math.pi * np.arange(SEG) / SEG
    r = np.array([prof(t / (2 * math.pi) * PITCH) for t in th])
    poly = np.stack([r * np.cos(th), r * np.sin(th)], 1)
    divisions = max(8, int(height / PITCH * 72))
    return m3d.Manifold.extrude(m3d.CrossSection([poly]), height, divisions, 360.0 * height / PITCH)


def _revolve(prof):
    return m3d.Manifold.revolve(m3d.CrossSection([prof]), 360)


def _to_manifold(part):
    mesh = m3d.Mesh(vert_properties=part.verts.astype(np.float32),
                    tri_verts=part.faces.astype(np.uint32))
    man = m3d.Manifold(mesh)
    if man.status() != m3d.Error.NoError:
        raise ValueError(f"Relief-Netz nicht verarbeitbar: {man.status()}")
    return man


def _to_part(man, name):
    mesh = man.to_mesh()
    return Part(name, 1, np.asarray(mesh.vert_properties)[:, :3].astype(np.float64),
                np.asarray(mesh.tri_verts).astype(np.int64))


def add_threads(obj, rim_r, height):
    """Außengewinde unten und oben an das weiße Relief (Teil 0) anfügen."""
    L = THREAD_LEN
    core = rim_r - OVERLAP                  # Ring ab hier, überlappt mit dem vollen Rand
    thread = _helix(rim_r, rim_r + DEPTH, L) - m3d.Manifold.cylinder(L, core, core, 360)
    # Gewindeanfang und -ende mit 45° anfasen, damit es sauber greift
    e = DEPTH + 0.3
    clip = _revolve([(0, 0), (rim_r, 0), (rim_r + e, e), (rim_r + e, L - e), (rim_r, L), (0, L)])
    bottom = thread ^ clip
    top = bottom.rotate([180, 0, 0]).translate([0, 0, height])
    relief = _to_manifold(obj.parts[0]) + bottom + top
    obj.parts[0] = _to_part(relief, obj.parts[0].name)


def _cup(rim_r, floor, clearance):
    """Becher mit Innengewinde: Boden der Dicke floor, Gewinde darüber."""
    L = THREAD_LEN
    bore = rim_r + clearance
    ro = rim_r + DEPTH + clearance + WALL
    top = floor + L
    body = _revolve([(0, 0), (ro - CHAMFER, 0), (ro, CHAMFER), (ro, top), (0, top)])
    void = m3d.Manifold.cylinder(L + 1, bore, bore, 360).translate([0, 0, floor])
    void += _helix(bore, bore + DEPTH, L + 1).translate([0, 0, floor])
    lead = DEPTH + clearance                          # Einführfase an der Öffnung
    void += _revolve([(0, top - lead), (bore, top - lead), (bore + lead + 0.2, top + 0.2), (0, top + 0.2)])
    return body - void, ro, bore


def build(rim_d, cable=True, light_d=None, pocket_depth=3.0, floor=4.5, clearance=0.4):
    """rim_d: Außendurchmesser des vollen Rands am Lampenschirm.
    floor: Dicke des Bodens, pocket_depth: Tiefe der Vertiefung (0 = keine)."""
    rim_r = rim_d / 2.0
    if pocket_depth > 0 and pocket_depth > floor - MIN_BOTTOM:
        raise ValueError(f"Die Vertiefung ({pocket_depth:g} mm) ist zu tief für einen {floor:g} mm dicken Boden. "
                         f"Erhöhe die Bodenstärke auf mindestens {pocket_depth + MIN_BOTTOM:g} mm.")

    base, ro, bore = _cup(rim_r, floor, clearance)
    pocket = None
    if light_d and pocket_depth > 0:
        rp = light_d / 2.0 + CLEAR_LIGHT
        if rp + 1.0 > rim_r:
            raise ValueError("Die Lichtquelle ist zu groß für diesen Lampenschirm.")
        lead = min(POCKET_LEAD, pocket_depth / 2)
        base -= _revolve([(0, floor - pocket_depth), (rp, floor - pocket_depth), (rp, floor - lead),
                          (rp + lead, floor), (rp + lead, floor + 0.01), (0, floor + 0.01)])
        pocket = (2 * rp, pocket_depth)

    if cable:
        g_bottom = max(MIN_BOTTOM, floor - max(3.0, pocket_depth))
        x0 = min(15.0, light_d / 2.0 - 10) if light_d else 15.0
        base -= m3d.Manifold.cube([ro + 5 - x0, 6, THREAD_LEN + floor]).translate([x0, -3, g_bottom])

    lid = _cup(rim_r, LID_PLATE, clearance)[0]
    objs = [Obj("Boden", [_to_part(base, "Boden")]), Obj("Deckel", [_to_part(lid, "Deckel")])]
    return objs, {"bore": 2 * bore, "outer": 2 * ro, "pocket": pocket}
