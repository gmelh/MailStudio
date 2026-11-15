"""Rebuild sections/social/*.png from Font Awesome Free's SVG icons (CC BY 4.0).

Development only: MailStudio itself just colours the finished PNGs (with Pillow) when an
email needs them, so installs don't need any of this.

    npm pack @fortawesome/free-brands-svg-icons @fortawesome/free-solid-svg-icons
    (extract them, write one SVG per network into a folder, as in tools/README)
    pip install resvg-py
    python tools/build_social_icons.py <folder of SVGs> sections/social

Each icon is centred in a square and saved as a 256px white shape on transparency.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import resvg_py

SIZE = 256


def square(svg: str) -> str:
    """Centre the icon in a square viewBox, so every icon has the same shape."""
    match = re.search(r'viewBox="0 0 (\d+) (\d+)"', svg)
    assert match, "expected a viewBox"
    width, height = int(match.group(1)), int(match.group(2))
    side = max(width, height)
    box = f'viewBox="{-(side - width) / 2:g} {-(side - height) / 2:g} {side} {side}"'
    return svg.replace(match.group(0), box)


def main(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for svg_file in sorted(source.glob("*.svg")):
        png = resvg_py.svg_to_bytes(svg_string=square(svg_file.read_text()), width=SIZE, height=SIZE)
        (target / f"{svg_file.stem}.png").write_bytes(bytes(png))
        print(svg_file.stem)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]))
