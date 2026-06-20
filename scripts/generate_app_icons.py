#!/usr/bin/env python3
"""Generate committed Locus app icon assets for macOS and Windows."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent.parent
ICON_DIR = ROOT / "assets" / "icons"
MACOS_DIR = ICON_DIR / "macos"
WINDOWS_DIR = ICON_DIR / "windows"
ICONSET_DIR = ICON_DIR / "Locus.iconset"
SOURCE_ICON = ICON_DIR / "locus-app-icon-source.png"


def rounded_mask(size: int, radius: int) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size, size), radius=radius, fill=255)
    return mask


def draw_locus_icon(size: int = 1024) -> Image.Image:
    scale = size / 1024
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(v * scale) for v in values)

    def width(value: int) -> int:
        return max(1, round(value * scale))

    bg = Image.new("RGBA", (size, size), (15, 17, 16, 255))
    bg_draw = ImageDraw.Draw(bg)
    for y in range(size):
        t = y / max(1, size - 1)
        r = round(15 + 34 * (1 - t))
        g = round(17 + 36 * (1 - t))
        b = round(16 + 30 * (1 - t))
        bg_draw.line((0, y, size, y), fill=(r, g, b, 255))

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse(box((124, 86, 910, 840)), fill=(237, 246, 239, 58))
    glow_draw.ellipse(box((386, 230, 928, 956)), fill=(119, 215, 207, 44))
    glow_draw.ellipse(box((86, 516, 514, 970)), fill=(245, 199, 131, 34))
    glow = glow.filter(ImageFilter.GaussianBlur(width(76)))
    canvas.alpha_composite(bg)
    canvas.alpha_composite(glow)

    for i, alpha in enumerate((46, 30, 20)):
        inset = 116 + i * 74
        draw.rounded_rectangle(
            box((inset, inset, 1024 - inset, 1024 - inset)),
            radius=width(150 - i * 22),
            outline=(237, 246, 239, alpha),
            width=width(3),
        )

    # Subtle water line and lotus base.
    draw.ellipse(box((154, 704, 870, 850)), fill=(111, 135, 124, 46), outline=(237, 246, 239, 42), width=width(3))
    draw.arc(box((174, 662, 850, 874)), 8, 172, fill=(119, 215, 207, 62), width=width(6))

    def petal(cx: int, cy: int, w: int, h: int, angle: float, fill: tuple[int, int, int, int], outline: tuple[int, int, int, int]) -> None:
        layer = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        layer_draw = ImageDraw.Draw(layer)
        local_box = box((cx - w // 2, cy - h // 2, cx + w // 2, cy + h // 2))
        layer_draw.ellipse(local_box, fill=fill, outline=outline, width=width(7))
        highlight = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        highlight_draw = ImageDraw.Draw(highlight)
        highlight_draw.ellipse(
            box((cx - w // 5, cy - h // 2 + 28, cx + w // 5, cy + h // 3)),
            fill=(255, 255, 255, 30),
        )
        layer.alpha_composite(highlight)
        rotated = layer.rotate(angle, center=(round(cx * scale), round(cy * scale)), resample=Image.Resampling.BICUBIC)
        canvas.alpha_composite(rotated)

    # Back row: football-like petals.
    petal(512, 422, 194, 412, 0, (203, 216, 207, 226), (247, 247, 242, 124))
    petal(392, 480, 184, 396, -30, (174, 191, 181, 224), (247, 247, 242, 116))
    petal(632, 480, 184, 396, 30, (174, 191, 181, 224), (247, 247, 242, 116))
    petal(294, 584, 164, 356, -58, (143, 163, 154, 218), (237, 246, 239, 105))
    petal(730, 584, 164, 356, 58, (143, 163, 154, 218), (237, 246, 239, 105))

    # Front row.
    petal(442, 642, 176, 330, -18, (238, 243, 233, 236), (255, 255, 255, 138))
    petal(582, 642, 176, 330, 18, (238, 243, 233, 236), (255, 255, 255, 138))
    petal(512, 678, 166, 292, 0, (247, 247, 242, 242), (255, 255, 255, 142))

    center_glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    center_draw = ImageDraw.Draw(center_glow)
    center_draw.ellipse(box((418, 600, 606, 790)), fill=(245, 199, 131, 58))
    center_glow = center_glow.filter(ImageFilter.GaussianBlur(width(32)))
    canvas.alpha_composite(center_glow)

    draw.ellipse(box((456, 634, 568, 746)), fill=(255, 245, 196, 245), outline=(255, 255, 255, 170), width=width(5))
    draw.ellipse(box((492, 670, 532, 710)), fill=(255, 255, 247, 255))
    draw.line(box((512, 746, 512, 842)), fill=(215, 238, 232, 142), width=width(10))

    mask = rounded_mask(size, width(212))
    shadow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle(box((32, 38, 992, 998)), radius=width(212), fill=(0, 0, 0, 92))
    shadow = shadow.filter(ImageFilter.GaussianBlur(width(18)))
    img.alpha_composite(shadow)
    tile = Image.composite(canvas, Image.new("RGBA", (size, size), (0, 0, 0, 0)), mask)
    img.alpha_composite(tile)
    return img


def write_svg() -> None:
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-label="Locus lotus app icon">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#30352f"/>
      <stop offset=".58" stop-color="#171a16"/>
      <stop offset="1" stop-color="#0f1110"/>
    </linearGradient>
    <filter id="soft" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="18"/></filter>
  </defs>
  <rect x="48" y="48" width="928" height="928" rx="212" fill="url(#bg)"/>
  <ellipse cx="512" cy="486" rx="344" ry="300" fill="#edf6ef" opacity=".16" filter="url(#soft)"/>
  <ellipse cx="512" cy="780" rx="358" ry="72" fill="#77d7cf" opacity=".18"/>
  <g stroke="#f7f7f2" stroke-width="7" stroke-opacity=".58">
    <ellipse cx="512" cy="422" rx="97" ry="206" fill="#cbd8cf" transform="rotate(0 512 422)"/>
    <ellipse cx="392" cy="480" rx="92" ry="198" fill="#aebfb5" transform="rotate(-30 392 480)"/>
    <ellipse cx="632" cy="480" rx="92" ry="198" fill="#aebfb5" transform="rotate(30 632 480)"/>
    <ellipse cx="294" cy="584" rx="82" ry="178" fill="#8fa39a" transform="rotate(-58 294 584)"/>
    <ellipse cx="730" cy="584" rx="82" ry="178" fill="#8fa39a" transform="rotate(58 730 584)"/>
    <ellipse cx="442" cy="642" rx="88" ry="165" fill="#eef3e9" transform="rotate(-18 442 642)"/>
    <ellipse cx="582" cy="642" rx="88" ry="165" fill="#eef3e9" transform="rotate(18 582 642)"/>
    <ellipse cx="512" cy="678" rx="83" ry="146" fill="#f7f7f2"/>
  </g>
  <circle cx="512" cy="690" r="56" fill="#fff1a8" stroke="#fff" stroke-opacity=".66" stroke-width="5"/>
  <circle cx="512" cy="690" r="20" fill="#fffef2"/>
</svg>
"""
    (ICON_DIR / "locus-app-icon.svg").write_text(svg, encoding="utf-8")


def write_pngs(icon: Image.Image) -> None:
    icon.save(ICON_DIR / "locus-app-icon-1024.png")
    for size in (16, 32, 48, 64, 128, 256, 512):
        icon.resize((size, size), Image.Resampling.LANCZOS).save(ICON_DIR / f"locus-app-icon-{size}.png")


def write_ico(icon: Image.Image) -> None:
    sizes = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    icon.save(WINDOWS_DIR / "Locus.ico", sizes=sizes)


def write_icns(icon: Image.Image) -> None:
    target = MACOS_DIR / "Locus.icns"
    if ICONSET_DIR.exists():
        shutil.rmtree(ICONSET_DIR)
    ICONSET_DIR.mkdir(parents=True, exist_ok=True)
    for size in (16, 32, 128, 256, 512):
        icon.resize((size, size), Image.Resampling.LANCZOS).save(ICONSET_DIR / f"icon_{size}x{size}.png")
        icon.resize((size * 2, size * 2), Image.Resampling.LANCZOS).save(ICONSET_DIR / f"icon_{size}x{size}@2x.png")
    try:
        subprocess.run(
            ["iconutil", "-c", "icns", str(ICONSET_DIR), "-o", str(target)],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        icon.save(
            target,
            sizes=[(16, 16), (32, 32), (128, 128), (256, 256), (512, 512), (1024, 1024)],
        )
    finally:
        shutil.rmtree(ICONSET_DIR, ignore_errors=True)


def source_or_generated_icon() -> Image.Image:
    if SOURCE_ICON.exists():
        return Image.open(SOURCE_ICON).convert("RGBA").resize((1024, 1024), Image.Resampling.LANCZOS)
    write_svg()
    return draw_locus_icon()


def main() -> None:
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    MACOS_DIR.mkdir(parents=True, exist_ok=True)
    WINDOWS_DIR.mkdir(parents=True, exist_ok=True)
    icon = source_or_generated_icon()
    write_pngs(icon)
    write_ico(icon)
    write_icns(icon)
    print(f"Wrote Locus icon assets to {ICON_DIR}")


if __name__ == "__main__":
    main()
