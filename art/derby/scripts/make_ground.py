"""The track's dirt: a seamless ground texture (source/ground/: a Poly Haven CC0 colour map, see credits.json) made into
the add-on's static/derby_ground.webp. The overlay lays it over each theme's track colours (an overlay blend: lighter
than mid grey lightens, darker darkens), so it is made grey and centred on mid grey, and its big blotches are evened
out (a dark patch repeating every tile shows as the track scrolls); blurring wraps around, so it still tiles.

    python -I art/derby/scripts/make_ground.py <source .jpg/.png> <abs path>/catalog/games_derby/static/derby_ground.webp [size] [contrast]

size: the tile's side (px, 512); contrast: how far from mid grey the detail swings (its standard deviation, 34).
Needs Pillow only (no Blender).
"""
import sys
from PIL import Image, ImageChops, ImageFilter, ImageStat

src, dst = sys.argv[1], sys.argv[2]
size = int(sys.argv[3]) if len(sys.argv) > 3 else 512
contrast = float(sys.argv[4]) if len(sys.argv) > 4 else 34.0

im = Image.open(src).convert("L").resize((size, size), Image.LANCZOS)   # (the whole tile: its edges still meet)
big = Image.new("L", (size * 3, size * 3))
for i in range(3):
    for j in range(3):
        big.paste(im, (i * size, j * size))
blur = big.filter(ImageFilter.GaussianBlur(size / 10)).crop((size, size, 2 * size, 2 * size))   # (wrapped: tiles)
flat = ImageChops.subtract(im, blur, scale=1.0, offset=128)            # the detail, the blotches gone
st = ImageStat.Stat(flat)
mean, sd = st.mean[0], max(1.0, st.stddev[0])
out = flat.point(lambda v: max(0, min(255, round(128 + (v - mean) * contrast / sd))))
out.save(dst, "WEBP", quality=82, method=6)
print("GROUND", src, "->", dst, f"{size}px, detail sd {sd:.1f} -> {contrast}")
