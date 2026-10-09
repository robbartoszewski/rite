"""Generate the whole rite brand set from the keeper logo (rite-mark.svg).

Everything derives from that one file: the flame paths are indices 0-4, the
wordmark paths 5-9 (four ink glyphs plus the ember dot on the 'i').
"""

import io
import os
import re
import sys

import cairosvg
from PIL import Image

WT = sys.argv[1]
PUB = f"{WT}/website/public"
BRAND = f"{PUB}/brand"
MASTER = f"{BRAND}/rite-mark.svg"

INK, PAPER = "#222221", "#FAF7F2"

src = open(MASTER).read()
paths = re.findall(r'<path fill="(#[0-9A-Fa-f]{6})" d="([^"]+)"/>', src)
assert len(paths) == 10, len(paths)
FLAME, WORD = paths[0:5], paths[5:10]

HDR = '<?xml version="1.0" encoding="utf-8" ?>\n'


def paths_xml(items, recolor=None, only=None):
    """recolor: new fill. `only` limits recolouring to paths of that fill, which
    is how the lockups keep the ember dot on the 'i' while the ink glyphs flip
    to off-white. Monochrome variants pass no `only`, so everything changes."""
    out = []
    for f, d in items:
        fill = (
            recolor if (recolor and (only is None or f.lower() == only.lower())) else f
        )
        out.append(f'<path fill="{fill}" d="{d}"/>')
    return "".join(out)


def svg_doc(body, w, h, vb):
    return (
        f'{HDR}<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}"'
        f' viewBox="{vb}">{body}</svg>\n'
    )


def render(svg_text, w, h=None):
    return cairosvg.svg2png(
        bytestring=svg_text.encode(), output_width=w, output_height=h or w
    )


def bbox(items, scale=2):
    png = render(svg_doc(paths_xml(items), 1024, 1024, "0 0 1024 1024"), 1024 * scale)
    a = Image.open(io.BytesIO(png)).convert("RGBA").split()[-1].getbbox()
    return tuple(v / scale for v in a)


FB, WB = bbox(FLAME), bbox(WORD)
fw, fh = FB[2] - FB[0], FB[3] - FB[1]
ww, wh = WB[2] - WB[0], WB[3] - WB[1]

written = []


def write(name, text, sub=""):
    p = os.path.join(PUB, sub, name) if sub else os.path.join(PUB, name)
    open(p, "w").write(text)
    written.append(p)
    return p


# ── 1. flame-only mark, tight box. The icon set uses the flame alone: a 16px
#       favicon carrying the wordmark is illegible, and the brand pack's own
#       note says the favicon is the flame only.
def flame_svg(recolor=None, pad=0.0):
    px, py = fw * pad, fh * pad
    vb = f"{FB[0] - px:.2f} {FB[1] - py:.2f} {fw + 2 * px:.2f} {fh + 2 * py:.2f}"
    return svg_doc(
        paths_xml(FLAME, recolor), round(fw + 2 * px), round(fh + 2 * py), vb
    )


write("rite-flame.svg", flame_svg(), "brand")


# ── 2. monochrome full mark (flame + wordmark), one colour, transparent
def mono_svg(colour):
    x0 = min(FB[0], WB[0])
    y0 = min(FB[1], WB[1])
    x1 = max(FB[2], WB[2])
    y1 = max(FB[3], WB[3])
    vb = f"{x0:.2f} {y0:.2f} {x1 - x0:.2f} {y1 - y0:.2f}"
    return svg_doc(
        paths_xml(FLAME, colour) + paths_xml(WORD, colour),
        round(x1 - x0),
        round(y1 - y0),
        vb,
    )


write("rite-mark-ink.svg", mono_svg(INK), "brand")
write("rite-mark-white.svg", mono_svg(PAPER), "brand")


# ── 3. horizontal lockup: flame left, wordmark right.
#    FLAME_RATIO — flame height as a multiple of the wordmark's height.
#    ALIGN — "baseline" sits the flame on the wordmark's baseline; "center"
#            centres it on the wordmark's box.
def lockup_svg(word_colour, flame_ratio=1.40, gap_ratio=0.34, align="baseline"):
    th = wh * flame_ratio  # target flame height
    s = th / fh  # flame scale
    tfw = fw * s  # scaled flame width
    gap = tfw * gap_ratio
    H = max(th, wh)
    wx = tfw + gap  # wordmark x offset
    if align == "baseline":
        wy = H - wh  # bottoms flush
    else:
        wy = (H - wh) / 2
    W = wx + ww
    f_tr = f"translate({-FB[0] * s:.3f},{(H - th) / 2 - FB[1] * s:.3f}) scale({s:.6f})"
    w_tr = f"translate({wx - WB[0]:.3f},{wy - WB[1]:.3f})"
    body = (
        f'<g transform="{f_tr}">{paths_xml(FLAME)}</g>'
        f'<g transform="{w_tr}">{paths_xml(WORD, word_colour, only=INK)}</g>'
    )
    return svg_doc(body, round(W), round(H), f"0 0 {W:.2f} {H:.2f}")


for nm, col in (
    ("rite-logo-horizontal.svg", INK),
    ("rite-logo-horizontal-dark.svg", PAPER),
):
    write(nm, lockup_svg(col), "brand")

print(f"FLAME {fw:.1f}x{fh:.1f}   WORD {ww:.1f}x{wh:.1f}")
for p in written:
    print(f"  wrote {os.path.relpath(p, PUB)}  ({os.path.getsize(p)} B)")
