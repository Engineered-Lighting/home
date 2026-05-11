"""Generate the source 1024x1024 PNG used by `cargo tauri icon`.

Renders a calm wordmark "h" in white on a black square, with a single
ice-tinted accent dot at the lower-right baseline — matches the
Engineered Lighting micro-mark (pill + dot from home-icons.jsx).
"""
from PIL import Image, ImageDraw, ImageFont
import os
import sys

SIZE = 1024
OUT = os.path.join(os.path.dirname(__file__), "..", "app", "src-tauri", "icons", "source.png")

img = Image.new("RGB", (SIZE, SIZE), "black")
d = ImageDraw.Draw(img)

# Try a few fonts in preference order; Consolas is reliably present on Windows.
font = None
for f in ["consola.ttf", "consolab.ttf", "C:/Windows/Fonts/consola.ttf",
          "C:/Windows/Fonts/segoeuib.ttf"]:
    try:
        font = ImageFont.truetype(f, 700)
        break
    except (OSError, IOError):
        continue

if font is None:
    print("No suitable font found; using default.", file=sys.stderr)
    font = ImageFont.load_default()

# Centre the "h" glyph.
text = "h"
bbox = d.textbbox((0, 0), text, font=font)
tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
d.text(
    ((SIZE - tw) / 2 - bbox[0], (SIZE - th) / 2 - bbox[1] - 60),
    text, fill="white", font=font,
)

# Accent dot — bottom-right, the EL signature.
dot_r = 28
cx, cy = SIZE * 0.78, SIZE * 0.82
d.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=(184, 216, 255))

os.makedirs(os.path.dirname(OUT), exist_ok=True)
img.save(OUT, "PNG")
print(f"wrote {OUT} ({SIZE}x{SIZE})")
