"""Download the guests' models from Sketchfab: each guest of a spec whose model folder has no model yet, from its
credit's Sketchfab link, as its glTF binary (.glb).

    python art/derby/scripts/fetch_guests.py <spec.json> <abs path>/art/derby/source/guests [ids]

Sketchfab only hands downloads to an account: put your API token (sketchfab.com > Settings > Password & API) in
~/.sketchfab_token or SKETCHFAB_TOKEN. Dev time only: nothing here ships, and the models stay in source/ (never
committed). A model with no .glb on offer comes as its glTF zip, unpacked into the folder.
"""
import io, json, os, re, sys, urllib.request, zipfile

API = "https://api.sketchfab.com/v3/models/{}/download"


def token():
    t = os.environ.get("SKETCHFAB_TOKEN")
    p = os.path.expanduser("~/.sketchfab_token")
    if not t and os.path.isfile(p):
        t = open(p, encoding="utf-8").read().strip()
    if not t:
        sys.exit("no Sketchfab API token: put it in ~/.sketchfab_token or SKETCHFAB_TOKEN")
    return t


def get(url, headers=None):
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers or {}), timeout=600) as r:
        return r.read()


def main():
    spec, src = sys.argv[1], sys.argv[2]
    want = set(sys.argv[3].split(",")) if len(sys.argv) > 3 else None
    auth = {"Authorization": "Token " + token()}
    for g in json.load(open(spec, encoding="utf-8"))["guests"]:
        if want and g["id"] not in want:
            continue
        d = os.path.join(src, g.get("model", g["id"]))
        os.makedirs(d, exist_ok=True)
        if any(f.lower().endswith((".glb", ".gltf", ".fbx")) for f in os.listdir(d)):
            continue                                   # (already here)
        m = re.search(r"([0-9a-f]{32})/?$", (g.get("credit") or {}).get("url", ""))
        if not m:
            print("SKIP", g["id"], "(no Sketchfab link)"); continue
        try:
            offer = json.loads(get(API.format(m.group(1)), auth))
        except Exception as e:                         # (not downloadable any more, a bad token...)
            print("FAIL", g["id"], e); continue
        if offer.get("glb"):
            data = get(offer["glb"]["url"])
            open(os.path.join(d, g.get("model", g["id"]) + ".glb"), "wb").write(data)
        elif offer.get("gltf"):
            data = get(offer["gltf"]["url"])
            zipfile.ZipFile(io.BytesIO(data)).extractall(d)
        else:
            print("FAIL", g["id"], "no glTF on offer:", ", ".join(offer)); continue
        print("GOT", g["id"], round(len(data) / 1e6, 1), "MB")


if __name__ == "__main__":
    main()
