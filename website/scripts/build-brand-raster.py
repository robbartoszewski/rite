"""Rasterize the icon set from the flame, and the OG banner from the lockup."""
import io, os, sys, cairosvg
from PIL import Image

WT = sys.argv[1]
PUB = f"{WT}/website/public"; B = f"{PUB}/brand"
PAPER = (250, 247, 242, 255); INK = (34, 34, 33, 255)

def flame_rgba(h):
    """Flame rendered at height h, transparent, native aspect."""
    png = cairosvg.svg2png(url=f"{B}/rite-flame.svg", output_height=h)
    return Image.open(io.BytesIO(png)).convert("RGBA")

def tile(size, flame_h, bg, out):
    """Flame centred on a solid tile."""
    c = Image.new("RGBA", (size, size), bg)
    f = flame_rgba(flame_h)
    c.alpha_composite(f, ((size - f.width)//2, (size - f.height)//2))
    c.save(out); return c

def transparent(size, out, pad=0.06):
    """Flame centred in a transparent square, small optical padding."""
    c = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    f = flame_rgba(round(size * (1 - 2*pad)))
    c.alpha_composite(f, ((size - f.width)//2, (size - f.height)//2))
    c.save(out); return c

made = []
def note(p, im):
    made.append((os.path.relpath(p, PUB), im.size, os.path.getsize(p)))

# favicons — the flame alone. A 16px icon carrying the wordmark is illegible.
for s in (16, 32, 48, 64):
    p = f"{PUB}/favicon-{s}.png"; note(p, transparent(s, p, pad=0.03))

# favicon.ico, multi-resolution 16/32/48
ico_src = Image.open(f"{PUB}/favicon-48.png")
ico_path = f"{PUB}/favicon.ico"
ico_src.save(ico_path, format="ICO", sizes=[(16,16), (32,32), (48,48)])
note(ico_path, Image.open(ico_path))

# apple-touch-icon — iOS composites on black, so it needs an opaque tile
p = f"{PUB}/apple-touch-icon.png"; note(p, tile(180, 128, PAPER, p))

# PWA "any" icons — transparent per the manifest's purpose
for s in (192, 512):
    p = f"{PUB}/icon-{s}.png"; note(p, transparent(s, p))

# maskable — full bleed, flame inside the 80%-diameter safe circle.
# flame 330 tall on 512: half-diagonal = hypot(72.5, 165) = 180 < 204.8 ✓
p = f"{PUB}/maskable-512.png"; note(p, tile(512, 330, PAPER, p))

# OG/social banner 1200x630 from the horizontal lockup, on paper
og = Image.new("RGBA", (1200, 630), PAPER)
lk = Image.open(io.BytesIO(cairosvg.svg2png(url=f"{B}/rite-logo-horizontal.svg",
                                            output_width=620))).convert("RGBA")
og.alpha_composite(lk, ((1200-lk.width)//2, (630-lk.height)//2))
p = f"{PUB}/og.png"; og.convert("RGB").save(p, optimize=True); note(p, og)

# square avatar for profiles, from the flame on paper
p = f"{B}/avatar-512.png"; note(p, tile(512, 360, PAPER, p))

for rel, size, nb in made:
    print(f"  {rel:26} {size[0]:>4}x{size[1]:<4} {nb:>7} B")
