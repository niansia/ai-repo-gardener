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
FONT_PATH = Path("C:/Windows/Fonts/consola.ttf")
BOLD_FONT_PATH = Path("C:/Windows/Fonts/consolab.ttf")

FRAMES = [
    [
        ("$ repo-gardener diff . --base HEAD~1", BLUE),
    ],
    [
        ("$ repo-gardener diff . --base HEAD~1", BLUE),
        ("", TEXT),
        ("Repo Gardener", TEXT),
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
        ("$ repo-gardener fix . --base HEAD~1 --dry-run", BLUE),
    ],
    [
        ("$ repo-gardener fix . --base HEAD~1 --dry-run", BLUE),
        ("", TEXT),
        ("Repo Gardener safe deletion plan", TEXT),
        (
            "========================================================================",
            MUTED,
        ),
        ("Mode: DRY RUN", MUTED),
        ("", TEXT),
        ("DELETE parser_v2.py", YELLOW),
        ("  replacement: parser.py", TEXT),
        ("  confidence: 100%", GREEN),
        ("", TEXT),
        ("No files changed.", MUTED),
    ],
]


def render_frame(lines: list[tuple[str, str]]) -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((45, 38, WIDTH - 45, HEIGHT - 38), 22, fill=PANEL)
    draw.ellipse((76, 70, 92, 86), fill="#ff6b6b")
    draw.ellipse((102, 70, 118, 86), fill="#ffd166")
    draw.ellipse((128, 70, 144, 86), fill="#65d6a6")
    title_font = ImageFont.truetype(str(BOLD_FONT_PATH), 24)
    body_font = ImageFont.truetype(str(FONT_PATH), 23)
    draw.text((WIDTH - 260, 62), "repo-gardener", font=title_font, fill=MUTED)
    y = 124
    for line, color in lines:
        draw.text((78, y), line, font=body_font, fill=color)
        y += 43
    return image


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
