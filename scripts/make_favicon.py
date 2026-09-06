"""Generate app/static/favicon.ico — a target/dartboard on brand blue.

Run:  python scripts/make_favicon.py
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "app" / "static" / "favicon.ico"

BLUE = (26, 77, 143, 255)     # brand --primary
WHITE = (255, 255, 255, 255)
RED = (178, 59, 59, 255)      # brand --red (bullseye)


def render(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = size / 2
    # concentric target rings
    rings = [
        (0.48, BLUE),
        (0.36, WHITE),
        (0.24, BLUE),
        (0.12, RED),
    ]
    for frac, color in rings:
        r = size * frac
        d.ellipse([c - r, c - r, c + r, c + r], fill=color)
    return img


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    sizes = [16, 32, 48, 64]
    imgs = [render(s) for s in sizes]
    # Pillow writes a multi-resolution .ico from the largest image + sizes list.
    imgs[-1].save(OUT, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
