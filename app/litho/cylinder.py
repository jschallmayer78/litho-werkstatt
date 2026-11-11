"""Zylinder-Lithophane (Lampenschirm), einfarbig oder mit Farbhaut-Mosaik."""
import math

import numpy as np
from PIL import Image

from .common import Obj, Part, cell_faces, compact, hex_to_rgb, prepare_image, quantize


def relief_mesh(T, r_inner, height, ntc, f):
    """Weißes Relief: außen Bildrelief (nz x nt), innen glatte Wand mit ntc Segmenten."""
    nz, nt = T.shape
    th = 2 * math.pi * np.arange(nt) / nt
    thc = 2 * math.pi * np.arange(ntc) / ntc
    z = np.linspace(0, height, nz)
    R = r_inner + T
    outer = np.stack([R * np.cos(th), R * np.sin(th),
                      np.broadcast_to(z[:, None], R.shape)], -1).reshape(-1, 3)
    inner = np.concatenate([
        np.stack([r_inner * np.cos(thc), r_inner * np.sin(thc), np.zeros(ntc)], -1),
        np.stack([r_inner * np.cos(thc), r_inner * np.sin(thc), np.full(ntc, height)], -1)])
    verts = np.concatenate([outer, inner])
    base = nz * nt

    j = np.arange(nt)
    j1 = (j + 1) % nt
    i = np.arange(nz - 1)[:, None]
    a, b = (i * nt + j).ravel(), (i * nt + j1).ravel()
    c, d = ((i + 1) * nt + j1).ravel(), ((i + 1) * nt + j).ravel()
    faces = [np.stack([a, b, c], 1), np.stack([a, c, d], 1)]

    k = np.arange(ntc)
    k1 = (k + 1) % ntc
    ib, ib1, it, it1 = base + k, base + k1, base + ntc + k, base + ntc + k1
    faces += [np.stack([ib, it1, ib1], 1), np.stack([ib, it, it1], 1)]

    jk = j // f
    faces += [np.stack([j, base + jk, j1], 1),
              np.stack([(k1 * f) % nt, ib, ib1], 1)]
    ot, ot1 = (nz - 1) * nt + j, (nz - 1) * nt + j1
    faces += [np.stack([ot, ot1, base + ntc + jk], 1),
              np.stack([(nz - 1) * nt + (k1 * f) % nt, it1, it], 1)]
    return verts, np.concatenate(faces)


def skin_vertices(r0, r1, height, ntc, nzc):
    th = 2 * math.pi * np.arange(ntc) / ntc
    z = np.linspace(0, height, nzc + 1)
    out = []
    for r in (r0, r1):
        out.append(np.stack([np.broadcast_to(r * np.cos(th), (nzc + 1, ntc)),
                             np.broadcast_to(r * np.sin(th), (nzc + 1, ntc)),
                             np.broadcast_to(z[:, None], (nzc + 1, ntc))], -1).reshape(-1, 3))
    return np.concatenate(out)


def build(images, o):
    """images: Liste PIL-Bilder. o: Optionen (dict-ähnlich, siehe generate.py).
    Liefert (Obj, Vorschau-Bild, Statistik)."""
    multi = o["color_mode"] == "multi"
    if o.get("shade_d"):
        if o["shade_d"] < o["light_d"] + 1:
            raise ValueError("Der Lampenschirm muss innen mindestens 1 mm größer sein als die Lichtquelle.")
        r_in = o["shade_d"] / 2.0
    else:
        r_in = o["light_d"] / 2.0 + 0.5
    skin = o["skin"] if multi else 0.0
    r_skin = r_in + skin
    tmin, tmax = o["min_t"], o["max_t"]
    r_ref = r_skin + (tmin + tmax) / 2.0
    h = o["height"]

    res = o["res"]
    cres = max(o["color_res"], res) if multi else res
    f = max(1, int(round(cres / res)))
    ntc = int(round(2 * math.pi * r_ref / cres))
    nzc = int(round(h / cres))
    nt, nz = ntc * f, nzc * f + 1

    T = np.full((nz, nt), tmax)
    RGB = np.ones((nz, nt, 3))
    M = np.zeros((nz, nt))

    z = np.linspace(0, h, nz)
    rows = np.where((z >= o["border"]) & (z <= h - o["border"]))[0]
    n = len(images)
    seg = nt // n
    sep_px = int(round(o["separator"] / (2 * math.pi * r_ref / nt))) if n > 1 else 0
    img_w = seg - sep_px
    if rows.size < 10 or img_w < 10:
        raise ValueError("Die Bildfläche ist zu klein. Verringere den Rand oder die Anzahl der Bilder.")

    for k, img in enumerate(images):
        grey, col = prepare_image(img, (img_w, rows.size), o["autocontrast"], o["gamma"], o["saturation"])
        sl = (slice(rows[0], rows[-1] + 1), slice(k * seg + sep_px // 2, k * seg + sep_px // 2 + img_w))
        T[sl] = tmax - np.flipud(grey) * (tmax - tmin)
        RGB[sl] = np.flipud(col)
        M[sl] = 1.0

    obj = Obj("Lampenschirm")
    v, fc = relief_mesh(T, r_skin, h, ntc, f)
    obj.parts.append(Part("Relief weiß", 1, v, fc))
    stats = {"Innen-Ø": f"{2 * r_in:.1f} mm",
             "Außen-Ø max.": f"{2 * (r_skin + tmax):.1f} mm",
             "Höhe": f"{h:.0f} mm"}
    shares = {1: 1.0}
    light = ((tmax - T[:-1]) / (tmax - tmin)).clip(0, 1)[..., None]

    if multi:
        pal = o["palette"]
        pal_rgb = np.array([hex_to_rgb(p["color"]) for p in pal])
        rgb_c = RGB[:-1].reshape(nzc, f, ntc, f, 3).mean(axis=(1, 3))
        m_c = M[:-1].reshape(nzc, f, ntc, f).mean(axis=(1, 3))
        idx = quantize(rgb_c, m_c, pal_rgb, wrap=True, dither=o["dither"], clean=o["clean"])
        sv = skin_vertices(r_in, r_skin, h, ntc, nzc)
        shares = {}
        for k, p in enumerate(pal):
            mask = idx == k
            shares[k + 1] = float(mask.mean())
            if mask.any():
                vv, ff = compact(sv, cell_faces(mask, wrap=True))
                obj.parts.append(Part(f"Farbe {p['name']}", k + 1, vv, ff))
        fine = np.repeat(np.repeat(pal_rgb[idx], f, 0), f, 1)
        prev = fine * (0.12 + 0.88 * light)
    else:
        prev = np.repeat(0.12 + 0.88 * light, 3, axis=2)

    preview = Image.fromarray((np.flipud(prev) * 255).astype(np.uint8))
    return obj, preview, stats, shares, 2 * (r_skin + tmax)
