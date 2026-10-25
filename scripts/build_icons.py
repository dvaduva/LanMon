"""Construiește app/static/icons.svg (sprite) din pachetul npm lucide-static.

npm pack lucide-static@0.460.0 && tar xzf lucide-static-*.tgz
python scripts/build_icons.py package/icons
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.icons import ALL_ICONS  # noqa: E402


def main(icons_dir: str) -> None:
    symbols = []
    for name in sorted(ALL_ICONS):
        svg = (Path(icons_dir) / f"{name}.svg").read_text()
        body = re.search(r"<svg[^>]*>(.*)</svg>", svg, re.S).group(1)
        body = re.sub(r"\s*\n\s*", "", body)
        symbols.append(f'<symbol id="{name}" viewBox="0 0 24 24">{body}</symbol>')
    out = ROOT / "app" / "static" / "icons.svg"
    out.write_text(
        "<!-- Lucide icons (ISC), generat cu scripts/build_icons.py -->\n"
        '<svg xmlns="http://www.w3.org/2000/svg">\n' + "\n".join(symbols) + "\n</svg>\n"
    )
    print(f"{len(symbols)} iconițe → {out}")


if __name__ == "__main__":
    main(sys.argv[1])
