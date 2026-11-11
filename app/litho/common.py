"""Gemeinsame Bausteine: Datentypen, Farbe, Bildaufbereitung, Mosaik-Körper."""
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps


@dataclass
class Part:
    """Ein Teil eines Objekts, gedruckt mit einem Filament (slot = 1..n)."""
    name: str
    slot: int
    verts: np.ndarray
    faces: np.ndarray


@dataclass
class Obj:
    """Ein Objekt im Slicer (z. B. Lampenschirm, Boden, Deckel)."""
    name: str
    parts: list = field(default_factory=list)


def compact(verts, faces):
    """Unbenutzte Punkte entfernen."""
    used = np.unique(faces)
    remap = np.full(len(verts), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    return verts[used], remap[faces]


def hex_to_rgb(h):
    h = h.strip().lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4)])


def srgb_to_lab(rgb):
    rgb = np.clip(rgb, 0, 1)
    c = np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124, 0.3576, 0.1805],
                  [0.2126, 0.7152, 0.0722],
                  [0.0193, 0.1192, 0.9505]])
    xyz = c @ m.T / np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16 / 116)
    return np.stack([116 * f[..., 1] - 16,
                     500 * (f[..., 0] - f[..., 1]),
                     200 * (f[..., 1] - f[..., 2])], axis=-1)


def prepare_image(img, size, autocontrast=False, gamma=1.0, saturation=1.0):
    """Bild auf size=(w,h) zuschneiden. Liefert Helligkeit (0..1) und RGB (0..1),
    Zeile 0 = oben."""
    img = ImageOps.exif_transpose(img).convert("RGB")
    if autocontrast:
        img = ImageOps.autocontrast(img, cutoff=1)
    img = ImageOps.fit(img, size, method=Image.LANCZOS)
    grey = np.asarray(img.convert("L"), dtype=np.float64) / 255.0
    if gamma != 1.0:
        grey = grey ** gamma
    if saturation != 1.0:
        img = ImageEnhance.Color(img).enhance(saturation)
    return grey, np.asarray(img, dtype=np.float64) / 255.0


def chroma(rgb, sat_cut, dark_cut):
    """Farbton auf volle Helligkeit normieren; graue/dunkle Pixel -> weiß."""
    mx = rgb.max(-1)
    mn = rgb.min(-1)
    sat = np.where(mx > 1e-6, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    norm = rgb / np.maximum(mx, 1e-6)[..., None]
    grey = (mx < dark_cut) | (sat < sat_cut)
    norm[grey] = 1.0
    return norm, grey


def quantize(rgb, mask, pal_rgb, wrap, dither=False, clean=3, sat_cut=0.18, dark_cut=0.10):
    """Zellfarben -> Paletten-Index. Index 0 ist Weiß."""
    norm, grey = chroma(rgb, sat_cut, dark_cut)
    grey |= mask < 0.5
    lab = srgb_to_lab(norm)
    pal_lab = srgb_to_lab(pal_rgb)
    nr, nc, _ = lab.shape

    if not dither:
        idx = ((lab[..., None, :] - pal_lab[None, None]) ** 2).sum(-1).argmin(-1)
    else:
        work = lab.copy()
        idx = np.zeros((nr, nc), dtype=np.int64)
        for i in range(nr - 1, -1, -1):
            for j in range(nc):
                if grey[i, j]:
                    continue
                k = int(((pal_lab - work[i, j]) ** 2).sum(1).argmin())
                idx[i, j] = k
                err = work[i, j] - pal_lab[k]
                jr = (j + 1) % nc if wrap else j + 1
                jl = (j - 1) % nc if wrap else j - 1
                if jr < nc:
                    work[i, jr] += err * 7 / 16
                if i > 0:
                    if jl >= 0:
                        work[i - 1, jl] += err * 3 / 16
                    work[i - 1, j] += err * 5 / 16
                    if jr < nc:
                        work[i - 1, jr] += err * 1 / 16

    idx[grey] = 0
    if clean and clean > 1:
        im = Image.fromarray(idx.astype(np.uint8)).filter(ImageFilter.ModeFilter(int(clean)))
        idx = np.asarray(im).astype(np.int64).copy()
        idx[mask < 0.5] = 0
    return fix_diagonals(idx, wrap)


def fix_diagonals(idx, wrap):
    """Gleichfarbige Zellen, die sich nur über Eck berühren, ergeben
    nicht-manifolde Kanten – solche 2x2-Muster werden aufgelöst."""
    idx = idx.copy()
    for _ in range(100):
        if wrap:
            r = np.roll(idx, -1, axis=1)
            a, b, c, d = idx[:-1].copy(), r[:-1], idx[1:].copy(), r[1:]
        else:
            a, b = idx[:-1, :-1].copy(), idx[:-1, 1:].copy()
            c, d = idx[1:, :-1].copy(), idx[1:, 1:].copy()
        diag1 = (a == d) & (b != a) & (c != a)
        diag2 = (b == c) & (a != b) & (d != b) & ~diag1
        if not (diag1.any() or diag2.any()):
            break
        i, j = np.nonzero(diag1)
        idx[i + 1, j] = a[i, j]
        i, j = np.nonzero(diag2)
        idx[i, j] = b[i, j]
    return idx


def cell_faces(mask, wrap):
    """Geschlossene Hülle aller Zellen mit mask==True.

    Zellen liegen in einem Raster (Zeilen i, Spalten j) mit zwei Lagen
    (0 = innen/unten, 1 = außen/oben). Punktindex:
        V(l, i, j) = l*(nr+1)*ncv + i*ncv + j,  ncv = nc (wrap) bzw. nc+1.
    Spalte -> Zeile -> Lage bilden ein Rechtssystem."""
    nr, nc = mask.shape
    ncv = nc if wrap else nc + 1

    def V(l, i, j):
        return l * (nr + 1) * ncv + i * ncv + j

    def nxt(j):
        return (j + 1) % nc if wrap else j + 1

    F = []
    i, j = np.nonzero(mask)
    j1 = nxt(j)
    F += [np.stack([V(1, i, j), V(1, i, j1), V(1, i + 1, j1)], 1),
          np.stack([V(1, i, j), V(1, i + 1, j1), V(1, i + 1, j)], 1),
          np.stack([V(0, i, j), V(0, i + 1, j1), V(0, i, j1)], 1),
          np.stack([V(0, i, j), V(0, i + 1, j), V(0, i + 1, j1)], 1)]

    # Seitenflächen zwischen Spalten
    if wrap:
        left, right = mask, np.roll(mask, -1, axis=1)
        col = lambda jj: (jj + 1) % nc
    else:
        pad = np.zeros((nr, nc + 2), dtype=bool)
        pad[:, 1:-1] = mask
        left, right = pad[:, :-1], pad[:, 1:]
        col = lambda jj: jj
    for sel, flip in ((left & ~right, False), (~left & right, True)):
        i, jj = np.nonzero(sel)
        jj = col(jj)
        A, B, C, D = V(0, i, jj), V(1, i, jj), V(1, i + 1, jj), V(0, i + 1, jj)
        F += ([np.stack([A, C, D], 1), np.stack([A, B, C], 1)] if flip
              else [np.stack([A, D, C], 1), np.stack([A, C, B], 1)])

    # Flächen zwischen Zeilen
    pad = np.zeros((nr + 2, nc), dtype=bool)
    pad[1:-1] = mask
    below, above = pad[:-1], pad[1:]
    for sel, up in ((below & ~above, True), (~below & above, False)):
        i, j = np.nonzero(sel)
        j1 = nxt(j)
        P, Q, R, S = V(0, i, j), V(1, i, j), V(1, i, j1), V(0, i, j1)
        F += ([np.stack([Q, S, P], 1), np.stack([Q, R, S], 1)] if up
              else [np.stack([Q, P, S], 1), np.stack([Q, S, R], 1)])
    return np.concatenate(F)
