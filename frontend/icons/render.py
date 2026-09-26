"""Render the home-screen icons from the mark in `index.html`.

    cd frontend && uv run --with pillow python icons/render.py

Rerun after changing the mark; the PNGs are checked in, because a build that
needed a font and a Python interpreter to produce an icon would be a strange
thing to ask of `npm run build`.

Three shapes, because Android and iOS crop differently:

* ``icon-192`` / ``icon-512`` are the icon as drawn — a rounded square with
  transparent corners, used as-is wherever nothing is cropped.
* ``icon-maskable-512`` fills the whole square and keeps the character inside
  the central 80 % the maskable spec guarantees, so a launcher may cut a
  circle, a squircle or a teardrop out of it without clipping the strokes.
* ``apple-touch-icon`` is square and opaque: iOS rounds the corners itself and
  renders transparency as black.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# The kanji colour of the app (`--kanji` in styles.scss), which is what sits
# behind the character on every review card.
MAGENTA = (241, 0, 161, 255)
WHITE = (255, 255, 255, 255)
GLYPH = "漢"
FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"

HERE = Path(__file__).resolve().parent
PUBLIC = HERE.parent / "public"
# Drawn large and downsampled: Pillow has no anti-aliased rounded rectangle,
# so the corners are only smooth if they are shrunk afterwards.
SUPERSAMPLE = 4


def render(size: int, *, glyph_ratio: float, radius_ratio: float, opaque: bool) -> Image.Image:
    px = size * SUPERSAMPLE
    image = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    if radius_ratio:
        draw.rounded_rectangle((0, 0, px - 1, px - 1), radius=px * radius_ratio, fill=MAGENTA)
    else:
        draw.rectangle((0, 0, px - 1, px - 1), fill=MAGENTA)

    font = ImageFont.truetype(FONT, int(px * glyph_ratio))
    # Centre the ink, not the em box: a CJK glyph does not fill its em square
    # evenly, and anchoring on the box leaves the character sitting low.
    left, top, right, bottom = draw.textbbox((0, 0), GLYPH, font=font)
    draw.text(
        ((px - (right - left)) / 2 - left, (px - (bottom - top)) / 2 - top),
        GLYPH,
        font=font,
        fill=WHITE,
    )

    image = image.resize((size, size), Image.LANCZOS)
    if opaque:
        flat = Image.new("RGB", (size, size), MAGENTA[:3])
        flat.paste(image, mask=image.split()[3])
        return flat
    return image


def main() -> None:
    written = [
        ("icon-192.png", render(192, glyph_ratio=0.52, radius_ratio=0.22, opaque=False)),
        ("icon-512.png", render(512, glyph_ratio=0.52, radius_ratio=0.22, opaque=False)),
        # 0.40 keeps the character within the 80 % safe zone with room to spare.
        ("icon-maskable-512.png", render(512, glyph_ratio=0.40, radius_ratio=0, opaque=True)),
        ("apple-touch-icon.png", render(180, glyph_ratio=0.52, radius_ratio=0, opaque=True)),
    ]
    for name, image in written:
        image.save(PUBLIC / name)
        print(f"{name}: {image.size[0]}px {image.mode}")


if __name__ == "__main__":
    main()
