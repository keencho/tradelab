"""TL 로고 source.png 생성 (1024x1024)."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "source.png"
SIZE = 1024

img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 둥근 사각 배경
radius = SIZE // 6
d.rounded_rectangle(
    [(0, 0), (SIZE - 1, SIZE - 1)],
    radius=radius,
    fill=(28, 32, 40, 255),
    outline=(80, 90, 110, 255),
    width=8,
)

# 폰트 - Windows 기본 Segoe UI Bold
font = None
for fp in [
    r"C:\Windows\Fonts\seguibl.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
]:
    try:
        font = ImageFont.truetype(fp, int(SIZE * 0.55))
        break
    except OSError:
        continue
if font is None:
    font = ImageFont.load_default()

text = "TL"
bbox = d.textbbox((0, 0), text, font=font)
tw = bbox[2] - bbox[0]
th = bbox[3] - bbox[1]
x = (SIZE - tw) / 2 - bbox[0]
y = (SIZE - th) / 2 - bbox[1] - SIZE * 0.04

# 그라디언트 느낌 — 두 색 텍스트 겹치기
d.text((x + 4, y + 4), text, font=font, fill=(0, 0, 0, 120))   # 그림자
d.text((x, y), text, font=font, fill=(58, 130, 247, 255))      # 메인 (파란색)

img.save(OUT)
print(f"saved: {OUT}")
