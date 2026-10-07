"""Draw the app icon (a caption bubble with text lines) into assets/icon.ico.

Run once when the design changes; the .ico is committed and bundled by build_exe.py.
"""

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "icon.ico"


def draw(size: int = 256) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 256
    # Speech bubble with a tail, then three caption lines.
    d.rounded_rectangle((16 * s, 28 * s, 240 * s, 196 * s), radius=36 * s, fill=(26, 95, 180, 255))
    d.polygon([(64 * s, 190 * s), (64 * s, 240 * s), (112 * s, 192 * s)], fill=(26, 95, 180, 255))
    for i, width in enumerate((176, 136, 96)):
        y = (70 + i * 38) * s
        d.rounded_rectangle((44 * s, y, (44 + width) * s, y + 20 * s), radius=10 * s, fill=(255, 255, 255, 255))
    return img


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    draw().save(OUT, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("wrote", OUT)
