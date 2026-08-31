from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1200
HEIGHT = 650
BACKGROUND = "#0b1020"
PANEL = "#121a2b"
TEXT = "#dce7f3"
MUTED = "#7e8da6"
GREEN = "#65d6a6"
YELLOW = "#f2c66d"
BLUE = "#7db7ff"
FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/consola.ttf"),
    Path("/System/Library/Fonts/Menlo.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationMono-Regular.ttf"),
)
BOLD_FONT_CANDIDATES = (
    Path("C:/Windows/Fonts/consolab.ttf"),
    Path("/System/Library/Fonts/Menlo.ttc"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
    Path("/usr/share/fonts/truetype/liberation2/LiberationMono-Bold.ttf"),
)

FRAMES = [
    [
        ("$ repo-gardener diff .", BLUE),
    ],
    [
        ("$ repo-gardener diff .", BLUE),
        ("", TEXT),
        ("AI Repo Gardener", TEXT),
        (
            "========================================================================",
            MUTED,
        ),
        ("[HIGH] stale-file  parser_v2.py -> parser.py", YELLOW),
        ("  confidence 100%  risk 0%  action safe_delete_candidate", GREEN),
        ("  - call_site_migration: app.py", TEXT),
        ("  - inbound_imports: 0", TEXT),
        ("  - replacement_reachable: True", TEXT),
    ],
    [
        ("$ repo-gardener fix . --dry-run", BLUE),
    ],
    [
        ("$ repo-gardener fix . --dry-run", BLUE),
        ("", TEXT),
        ("AI Repo Gardener safe deletion plan", TEXT),
        (
            "========================================================================",
            MUTED,
        ),
        ("Mode: DRY RUN", MUTED),
        ("Plan ID: 5390c1a0e3e80051", MUTED),
        ("", TEXT),
        ("DELETE parser_v2.py", YELLOW),
        ("  replacement: parser.py", TEXT),
        ("  confidence: 100%", GREEN),
        ("", TEXT),
        ("No files changed. Save the JSON plan before apply.", MUTED),
    ],
]


def render_frame(lines: list[tuple[str, str]]) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 38, WIDTH - 45, HEIGHT - 38), 22, fill=PANEL)
    draw.ellipse((76, 70, 92, 86), fill="#ff6b6b")
    draw.ellipse((102, 70, 118, 86), fill="#ffd166")
    draw.ellipse((128, 70, 144, 86), fill="#65d6a6")
    title_font = _load_font(24, bold=True)
    body_font = _load_font(23)
    draw.text((WIDTH - 260, 62), "repo-gardener", font=title_font, fill=MUTED)
    y = 124
    for line, color in lines:
        draw.text((78, y), line, font=body_font, fill=color)
        y += 43
    return image


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = BOLD_FONT_CANDIDATES if bold else FONT_CANDIDATES
    for path in candidates:
        if not path.is_file():
            continue
        try:
            return ImageFont.truetype(str(path), size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow versions before the scalable default font.
        return ImageFont.load_default()


def main() -> None:
    destination = Path(__file__).resolve().parents[1] / "docs" / "demo.gif"
    destination.parent.mkdir(parents=True, exist_ok=True)
    images = [render_frame(lines) for lines in FRAMES]
    images[0].save(
        destination,
        save_all=True,
        append_images=images[1:],
        duration=[1000, 2400, 1000, 2600],
        loop=0,
        optimize=True,
    )
    print(destination)


if __name__ == "__main__":
    main()
