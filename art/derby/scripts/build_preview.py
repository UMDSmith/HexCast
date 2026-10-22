"""Write <out dir>/preview.html and race.html (the race prototype) from the rendered hounds there and
hounds.json, with fx.js (the overlay effects) built in.

Called by render_gallop.py after rendering; run it on its own (any Python 3) after changing a hound's
colour or effects, no re-render needed:

    python art/derby/scripts/build_preview.py art/derby/out/gallop
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def build(out, frames=None):
    roster = json.load(open(os.path.join(HERE, "..", "hounds.json"), encoding="utf-8"))["hounds"]
    lines = json.load(open(os.path.join(HERE, "..", "lines.json"), encoding="utf-8"))
    cfg = {"frames": frames, "hounds": [], "lines": lines}
    for h in roster:
        anchors = os.path.join(out, h["id"], "anchors.json")
        if not os.path.exists(anchors):
            continue                                  # not rendered (yet)
        entry = {k: h.get(k) for k in ("id", "name", "short", "hue", "sat", "cadence", "note", "fx")}
        with open(anchors, encoding="utf-8") as f:
            entry["anchors"] = json.load(f)
        cfg["frames"] = cfg["frames"] or len(entry["anchors"]["frames"])
        cfg["hounds"].append(entry)
    fx = open(os.path.join(HERE, "fx.js"), encoding="utf-8").read()
    for template, name in (("preview_template.html", "preview.html"), ("race_template.html", "race.html")):
        page = open(os.path.join(HERE, template), encoding="utf-8").read()
        page = page.replace("/*FX*/", fx).replace("/*ROSTER*/null", json.dumps(cfg))
        with open(os.path.join(out, name), "w", encoding="utf-8") as f:
            f.write(page)
    return os.path.join(out, "preview.html")


if __name__ == "__main__":
    print(build(sys.argv[1]))
