"""Build static/quota-hub.ico from the generated PNG. One-shot helper."""
from __future__ import annotations

import shutil
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
SRC_CANDIDATES = [STATIC / "quota-hub.png"]


def main() -> None:
    src = next((p for p in SRC_CANDIDATES if p.exists()), None)
    if not src:
        raise SystemExit("missing quota-hub-icon.png")
    STATIC.mkdir(parents=True, exist_ok=True)
    png = STATIC / "quota-hub.png"
    if src.resolve() != png.resolve():
        shutil.copy2(src, png)

    img = Image.open(png).convert("RGBA")
    w, h = img.size
    side = min(w, h)
    left = (w - side) // 2
    top = (h - side) // 2
    img = img.crop((left, top, left + side, top + side))
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(STATIC / "quota-hub.ico", format="ICO", sizes=sizes)
    print("wrote", STATIC / "quota-hub.ico", "from", src)


if __name__ == "__main__":
    main()
