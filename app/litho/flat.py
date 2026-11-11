"""Flache Lithophane-Platte: einfarbig, CMY-Farbmischung oder Farb-Mosaik."""
import numpy as np
from PIL import Image

from .common import Obj, Part, cell_faces, chroma, compact, hex_to_rgb, prepare_image, quantize

EPS = 0.01  # Mindestdicke je Farbschicht, damit keine Flächen ohne Volumen entstehen

CMY = [("Cyan", "#00AEEF"), ("Magenta", "#EC008C"), ("Gelb", "#FFE100")]


def slab(x, y, z_bot, z_top):
    """Geschlossener Körper zwischen zwei Höhenfeldern auf demselben Raster."""
    ny, nx = z_bot.shape
    X, Y = np.meshgrid(x, y)
    verts = np.concatenate([np.stack([X, Y, z_bot], -1).reshape(-1, 3),
                            np.stack([X, Y, z_top], -1).reshape(-1, 3)])
    N = nx * ny
    i = np.arange(ny - 1)[:, None]
    j = np.arange(nx - 1)[None, :]
    a, b = (i * nx + j).ravel(), (i * nx + j + 1).ravel()
    c, d = ((i + 1) * nx + j + 1).ravel(), ((i + 1) * nx + j).ravel()
    faces = [np.stack([N + a, N + b, N + c], 1), np.stack([N + a, N + c, N + d], 1),
             np.stack([a, c, b], 1), np.stack([a, d, c], 1)]
    loop = np.concatenate([np.arange(nx),
                           np.arange(1, ny) * nx + (nx - 1),
                           (ny - 1) * nx + np.arange(nx - 2, -1, -1),
                           np.arange(ny - 2, 0, -1) * nx])
    k0, k1 = loop, np.roll(loop, -1)
    faces += [np.stack([k0, k1, N + k1], 1), np.stack([k0, N + k1, N + k0], 1)]
    return verts, np.concatenate(faces)


def cell_vertices(W, H, ncx, ncy, z0, z1):
    x = np.linspace(0, W, ncx + 1)
    y = np.linspace(0, H, ncy + 1)
    X, Y = np.meshgrid(x, y)
    return np.concatenate([np.stack([X, Y, np.full_like(X, z)], -1).reshape(-1, 3) for z in (z0, z1)])


def build(img, o, label="Platte"):
    multi = o["color_mode"] == "multi"
    method = o["flat_method"] if multi else "mono"
    src = img
    W = o["width"]
    H = W * src.height / src.width if not o.get("plate_height") else o["plate_height"]
    res = o["res"]
    nx, ny = int(round(W / res)) + 1, int(round(H / res)) + 1
    b = int(round(o["border"] / res))
    if nx - 2 * b < 10 or ny - 2 * b < 10:
        raise ValueError("Die Platte ist zu klein für diesen Rand.")

    grey, rgb = prepare_image(src, (nx - 2 * b, ny - 2 * b), o["autocontrast"], o["gamma"], o["saturation"])
    G = np.zeros((ny, nx))
    RGB = np.ones((ny, nx, 3))
    M = np.zeros((ny, nx))
    G[b:ny - b, b:nx - b] = grey
    RGB[b:ny - b, b:nx - b] = rgb
    M[b:ny - b, b:nx - b] = 1.0
    G, RGB, M = np.flipud(G), np.flipud(RGB), np.flipud(M)   # Zeile 0 = unten (y = 0)

    tmin, tmax = o["min_t"], o["max_t"]
    relief = tmax - G * (tmax - tmin)
    light = ((tmax - relief) / (tmax - tmin))[..., None]
    x, y = np.linspace(0, W, nx), np.linspace(0, H, ny)
    obj = Obj(label)
    shares = {1: 1.0}

    if method == "mono":
        v, f = slab(x, y, np.zeros((ny, nx)), relief)
        obj.parts.append(Part("Relief weiß", 1, v, f))
        prev = np.repeat(0.12 + 0.88 * light, 3, axis=2)
        top = tmax

    elif method == "cmy":
        norm, _ = chroma(RGB, o["sat_cut_cmy"], o["dark_cut"])
        maxima = np.array([o["color_max"], o["color_max"], o["yellow_max"]])
        t = (1.0 - norm) * maxima
        if o["layer"] > 0:
            t = np.round(t / o["layer"]) * o["layer"]
        t = np.maximum(t, EPS)
        z = np.zeros((ny, nx))
        shares = {}
        for k, (name, _) in enumerate(CMY):
            v, f = slab(x, y, z, z + t[..., k])
            obj.parts.append(Part(name, k + 2, v, f))
            shares[k + 2] = float((t[..., k] > EPS).mean())
            z = z + t[..., k]
        v, f = slab(x, y, z, z + relief)
        obj.parts.insert(0, Part("Relief weiß", 1, v, f))
        shares[1] = 1.0
        col = 1.0 - np.clip((t - EPS) / maxima, 0, 1)
        prev = col * (0.12 + 0.88 * light)
        top = float((z + relief).max())

    else:  # Mosaik
        skin = o["skin"]
        pal = o["palette"]
        pal_rgb = np.array([hex_to_rgb(p["color"]) for p in pal])
        cres = max(o["color_res"], res)
        ncx, ncy = int(round(W / cres)), int(round(H / cres))
        rgb_c = np.asarray(Image.fromarray((RGB * 255).astype(np.uint8)).resize((ncx, ncy), Image.BOX),
                           dtype=np.float64) / 255.0
        m_c = np.asarray(Image.fromarray((M * 255).astype(np.uint8)).resize((ncx, ncy), Image.BOX),
                         dtype=np.float64) / 255.0
        idx = quantize(rgb_c, m_c, pal_rgb, wrap=False, dither=o["dither"], clean=o["clean"])
        cv = cell_vertices(W, H, ncx, ncy, 0.0, skin)
        shares = {}
        for k, p in enumerate(pal):
            mask = idx == k
            shares[k + 1] = float(mask.mean())
            if mask.any():
                vv, ff = compact(cv, cell_faces(mask, wrap=False))
                obj.parts.append(Part(f"Farbe {p['name']}", k + 1, vv, ff))
        v, f = slab(x, y, np.full((ny, nx), skin), skin + relief)
        obj.parts.insert(0, Part("Relief weiß", 1, v, f))
        cells = np.asarray(Image.fromarray(idx.astype(np.uint8)).resize((nx, ny), Image.NEAREST)).astype(int)
        prev = pal_rgb[cells] * (0.12 + 0.88 * light)
        top = skin + tmax

    preview = Image.fromarray((np.flipud(prev) * 255).astype(np.uint8))
    stats = {"Größe": f"{W:.0f} × {H:.0f} mm", "Dicke max.": f"{top:.2f} mm"}
    return obj, preview, stats, shares
