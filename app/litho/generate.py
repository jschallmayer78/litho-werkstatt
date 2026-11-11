"""Verbindet Optionen, Generatoren und 3MF-Export."""
import base64
import io

import numpy as np
from PIL import Image

from . import cylinder, enclosure, flat
from .export3mf import write_3mf

QUALITY = {  # Rasterweite Relief / Farbzelle in mm
    "cylinder": {"schnell": (0.4, 1.0), "normal": (0.3, 0.9), "fein": (0.22, 0.8)},
    "flat": {"schnell": (0.35, 0.8), "normal": (0.25, 0.6), "fein": (0.18, 0.5)},
}


def resolve(o):
    """Fehlende Werte mit sinnvollen Standards füllen."""
    o = dict(o)
    multi = o["color_mode"] == "multi"
    res, cres = QUALITY[o["shape"]][o["quality"]]
    o.setdefault("res", res)
    o.setdefault("color_res", cres)
    if o.get("min_t") is None:
        o["min_t"] = 0.6 if multi else 0.8
    if o.get("max_t") is None:
        o["max_t"] = 2.6 if multi else 3.0
    o.setdefault("skin", 0.6)
    o.setdefault("separator", 2.0)
    o.setdefault("clean", 3)
    o.setdefault("layer", 0.1)
    o.setdefault("sat_cut_cmy", 0.12)
    o.setdefault("dark_cut", 0.08)
    if o.get("yellow_max") is None:
        o["yellow_max"] = o["color_max"]
    return o


def shade_from_total(o):
    """Innen-Ø des Lampenschirms aus dem gewünschten Außen-Ø der ganzen Lampe."""
    if o.get("shade_d"):
        raise ValueError("Bitte entweder den Innen-Ø des Lampenschirms oder den Außen-Ø der Lampe angeben, nicht beides.")
    wall = (o["skin"] if o["color_mode"] == "multi" else 0.0) + o["max_t"]
    if o["enclosure"]:
        wall += enclosure.DEPTH + o["thread_clearance"] + enclosure.WALL
    shade = o["total_d"] - 2 * wall
    need = o["light_d"] + 1
    if shade < need:
        raise ValueError(f"Bei {o['total_d']:g} mm Außen-Ø bleiben innen nur {shade:.1f} mm. "
                         f"Für die Lichtquelle ({o['light_d']:g} mm) sind mindestens "
                         f"{need + 2 * wall:.1f} mm Außen-Ø nötig.")
    return shade


def _png(img, max_w=1600):
    if img.width > max_w:
        img = img.resize((max_w, round(img.height * max_w / img.width)), Image.LANCZOS)
    b = io.BytesIO()
    img.save(b, "PNG", optimize=True)
    return b.getvalue()


def _thumb(previews):
    t = Image.new("RGB", (512, 512), (32, 40, 52))
    p = previews[0].copy()
    p.thumbnail((480, 480))
    t.paste(p, ((512 - p.width) // 2, (512 - p.height) // 2))
    return _png(t)


def run(images, opts):
    o = resolve(opts)
    multi = o["color_mode"] == "multi"
    objects, previews, stats, shares = [], [], {}, {}

    if o["shape"] == "cylinder":
        if o.get("total_d"):
            o["shade_d"] = shade_from_total(o)                     # Gewinde braucht einen ausreichend hohen vollen Rand
            o["border"] = max(o["border"], enclosure.rim_height())
        obj, prev, stats, shares, outer_d = cylinder.build(images, o)
        if o["enclosure"]:
            parts, info = enclosure.build(outer_d, o["cable"], o["light_d"], o["pocket_depth"],
                                          o["floor"], o["thread_clearance"])
            enclosure.add_threads(obj, outer_d / 2.0, o["height"])
            stats["Gewinde"] = (f"{enclosure.PITCH:g} mm Steigung, "
                                f"{enclosure.THREAD_LEN:g} mm lang").replace(".", ",")
            stats["Boden/Deckel Außen-Ø"] = f"{info['outer']:.1f} mm"
            stats["Bodenstärke"] = f"{o['floor']:g} mm"
            if info["pocket"]:
                p = info["pocket"]
                stats["Vertiefung Lichtquelle"] = f"Ø {p[0]:.1f} mm, {p[1]:g} mm tief"
            stats["Gesamt-Ø"] = f"{info['outer']:.1f} mm"
            objects.append(obj)
            objects += parts
        else:
            stats["Gesamt-Ø"] = f"{outer_d:.1f} mm"
            objects.append(obj)
        previews.append(prev)
        if multi:
            slots = [(p["name"], p["color"]) for p in o["palette"]]
        else:
            slots = [("Weiß", "#FFFFFF")]
    else:
        acc = {}
        for k, img in enumerate(images):
            label = "Platte" if len(images) == 1 else f"Platte {k + 1}"
            obj, prev, st, sh = flat.build(img, o, label)
            objects.append(obj)
            previews.append(prev)
            stats = st if len(images) == 1 else {"Platten": str(len(images)), "Breite": f"{o['width']:.0f} mm",
                                                 "Dicke max.": st["Dicke max."]}
            for s, v in sh.items():
                acc[s] = acc.get(s, 0) + v / len(images)
        shares = acc
        if not multi:
            slots = [("Weiß", "#FFFFFF")]
        elif o["flat_method"] == "cmy":
            slots = [("Weiß", "#FFFFFF")] + flat.CMY
        else:
            slots = [(p["name"], p["color"]) for p in o["palette"]]

    data = write_3mf(objects, slots, _thumb(previews))
    tris = sum(len(p.faces) for ob in objects for p in ob.parts)
    stats["Dreiecke"] = f"{tris:,}".replace(",", ".")
    legend = [{"slot": i + 1, "name": n, "color": c, "share": round(100 * shares.get(i + 1, 0), 1)}
              for i, (n, c) in enumerate(slots)]
    return {
        "data": data,
        "previews": ["data:image/png;base64," + base64.b64encode(_png(p)).decode() for p in previews],
        "legend": legend,
        "stats": stats,
        "objects": [ob.name for ob in objects],
    }
