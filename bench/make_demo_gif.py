"""把真实运行记录渲染成一段 GIF，贴 README 用。

不是屏幕录制，是把 `docs/demo-transcript.txt` 里的真实输出按终端的样子画出来。
为什么这么做：录屏要装软件、文件大、还要挑机器；而这个项目的演示是「几条 curl
的输出」，文本本身就是全部信息量，渲染一遍比录屏更清楚。

用 Pillow 画，系统字体 Consolas。每帧对应一段命令加输出，按 `$ ` 开头切分。

用法：
    uv run python bench/make_demo_gif.py
    uv run python bench/make_demo_gif.py --transcript docs/demo-transcript.txt --out docs/demo.gif
"""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

WIDTH, HEIGHT = 1000, 560
MARGIN = 18
LINE_HEIGHT = 21
MAX_LINES = 15
BG = (13, 17, 23)
TITLE_BG = (28, 34, 44)
PROMPT = (126, 231, 135)
OUTPUT = (214, 222, 235)
DIM = (120, 132, 148)
FRAME_MS = 6000


def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # 必须优先选带中文字形的字体：Consolas 没有中文，会渲染成一排方块（踩过）。
    # SimSun 的拉丁字符是等宽的，放代码输出不难看。
    for candidate in (
        r"C:\Windows\Fonts\simsun.ttc",
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def split_blocks(text: str) -> list[list[str]]:
    """按 `$ ` 开头的命令行切块，块内是这条命令和它的输出。"""
    blocks: list[list[str]] = []
    current: list[str] = []
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if line.startswith("$ "):
            if current:
                blocks.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append(current)
    return blocks


def pack_frames(blocks: list[list[str]]) -> list[list[str]]:
    """把块打包成不超过 MAX_LINES 的帧。"""
    frames: list[list[str]] = []
    frame: list[str] = []
    for block in blocks:
        if frame and len(frame) + len(block) + 1 > MAX_LINES:
            frames.append(frame)
            frame = []
        if len(frame) > 0:
            frame.append("")
        frame.extend(block)
    if frame:
        frames.append(frame)
    return frames


def render_frame(lines: list[str], index: int, total: int) -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)
    font = load_font(15)
    small = load_font(12)

    draw.rectangle([(0, 0), (WIDTH, 34)], fill=TITLE_BG)
    draw.ellipse([(14, 12), (26, 24)], fill=(255, 95, 86))
    draw.ellipse([(32, 12), (44, 24)], fill=(255, 189, 46))
    draw.ellipse([(50, 12), (62, 24)], fill=(39, 201, 63))
    draw.text((78, 10), "short-link-service  ·  live output", font=small, fill=DIM)
    draw.text((WIDTH - 90, 10), f"{index + 1}/{total}", font=small, fill=DIM)

    y = 48
    for line in lines:
        color = PROMPT if line.startswith("$ ") else OUTPUT
        draw.text((MARGIN, y), line, font=font, fill=color)
        y += LINE_HEIGHT
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--transcript", default="docs/demo-transcript.txt")
    ap.add_argument("--out", default="docs/demo.gif")
    args = ap.parse_args()

    src = Path(args.transcript)
    if not src.is_absolute():
        src = ROOT / src
    if not src.exists():
        raise SystemExit(f"找不到 {src}，先跑一遍 bench/demo_transcript.ps1 生成")

    blocks = split_blocks(src.read_text(encoding="utf-8"))
    packed = pack_frames(blocks)
    frames = [render_frame(lines, i, len(packed)) for i, lines in enumerate(packed)]
    if not frames:
        raise SystemExit("transcript 是空的")

    dst = Path(args.out)
    if not dst.is_absolute():
        dst = ROOT / dst
    dst.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(
        dst,
        save_all=True,
        append_images=frames[1:],
        duration=FRAME_MS,
        loop=0,
        optimize=True,
    )
    print(f"写了 {dst}（{len(frames)} 帧，约 {len(frames) * FRAME_MS / 1000:.0f} 秒）")


if __name__ == "__main__":
    main()
