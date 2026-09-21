#!/usr/bin/env python3
"""Render the project mark used by directory listings.

Several MCP directories (Cline's marketplace among them) want a square PNG
icon at a fixed size, so the mark is generated rather than hand-drawn: change a
colour or a node here and every size regenerates consistently.

    python scripts/make_logo.py                    # docs/images/logo-400.png
    python scripts/make_logo.py --size 512 1024

The drawing is a call graph, not an abstract swirl: one file node at the top
with two symbols under it, the lower pair joined by the edge that makes this
tool different from a text search.
"""

from __future__ import annotations

import argparse
from pathlib import Path

try:
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover - dev tool
    raise SystemExit("Pillow is required: pip install pillow")

ROOT = Path(__file__).resolve().parents[1]
IMAGES = ROOT / "docs" / "images"

# Same palette as the README terminal GIFs (scripts/make_demo_gif.py).
BG = (13, 17, 23)
EDGE = (62, 72, 86)
BLUE = (88, 166, 255)
GREEN = (63, 185, 80)
PURPLE = (188, 140, 255)

SS = 4  # supersampling factor; PIL has no anti-aliased draw primitives

# Unit-square coordinates, so the mark scales to any output size.
NODES = [
    ((0.50, 0.24), 0.085, BLUE),  # the file
    ((0.26, 0.68), 0.070, PURPLE),  # a symbol it defines
    ((0.74, 0.68), 0.070, GREEN),  # a symbol the first one calls
]
EDGES = [(0, 1), (0, 2), (1, 2)]


def arrow(d, tip, direction, size, width):
    """Draw a filled arrowhead at `tip`, pointing along the unit `direction`."""
    ux, uy = direction
    px_, py_ = -uy, ux  # perpendicular
    back_x, back_y = tip[0] - ux * size, tip[1] - uy * size
    half = size * 0.55
    d.polygon(
        [
            tip,
            (back_x + px_ * half, back_y + py_ * half),
            (back_x - px_ * half, back_y - py_ * half),
        ],
        fill=EDGE,
    )


def render(size: int) -> Image.Image:
    px = size * SS
    img = Image.new("RGB", (px, px), BG)
    d = ImageDraw.Draw(img)

    radius = int(px * 0.22)
    d.rounded_rectangle([0, 0, px - 1, px - 1], radius=radius, fill=BG)

    width = max(1, int(px * 0.022))
    for a, b in EDGES:
        (ax, ay), ar, _ = NODES[a]
        (bx, by), br, _ = NODES[b]
        # Stop the line on each node's rim rather than its centre, so the
        # arrowhead sits where the edge meets the target instead of under it.
        dx, dy = (bx - ax) * px, (by - ay) * px
        length = (dx * dx + dy * dy) ** 0.5
        ux, uy = dx / length, dy / length
        x0, y0 = ax * px + ux * ar * px, ay * px + uy * ar * px
        x1, y1 = bx * px - ux * br * px, by * px - uy * br * px
        d.line([x0, y0, x1, y1], fill=EDGE, width=width)
        arrow(d, (x1, y1), (ux, uy), px * 0.052, width)

    for (cx, cy), r, colour in NODES:
        x, y, rr = cx * px, cy * px, r * px
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=colour)

    return img.resize((size, size), Image.LANCZOS)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--size", type=int, nargs="+", default=[400])
    ap.add_argument("--out-dir", default=str(IMAGES))
    args = ap.parse_args(argv)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for size in args.size:
        path = out_dir / f"logo-{size}.png"
        render(size).save(path, optimize=True)
        print(f"{path.name}: {path.stat().st_size / 1024:.0f} KiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
