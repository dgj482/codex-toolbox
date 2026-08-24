# -*- coding: utf-8 -*-
"""生成 Codex 工具箱的统一原创图标。

图标仅使用几何图形，不包含 OpenAI、Codex 或第三方品牌标识。
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


CANVAS = 1024
# 主程序、托盘、窗口和快捷方式都使用这一份图标，避免不同入口显示不一致。
MASTER = Path(__file__).resolve().parents[1] / "assets" / "codex_toolbox_master.png"
OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "codex_toolbox.ico"


def _rounded_mask(size, radius):
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((28, 28, size - 28, size - 28), radius=radius, fill=255)
    return mask


def _gradient_background(size):
    image = Image.new("RGBA", (size, size))
    pixels = image.load()
    for y in range(size):
        for x in range(size):
            horizontal = x / (size - 1)
            vertical = y / (size - 1)
            r = int(22 + 27 * horizontal + 15 * vertical)
            g = int(36 + 24 * horizontal + 13 * vertical)
            b = int(83 + 56 * horizontal + 40 * vertical)
            pixels[x, y] = (r, g, b, 255)
    return image


def _draw_glow(layer, center, color, radius):
    glow = Image.new("RGBA", layer.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(glow)
    x, y = center
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    glow = glow.filter(ImageFilter.GaussianBlur(radius * 0.58))
    layer.alpha_composite(glow)


def build_icon():
    background = _gradient_background(CANVAS)
    background.putalpha(_rounded_mask(CANVAS, 210))

    # 内侧半透明玻璃板
    panel = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(panel)
    panel_draw.rounded_rectangle((126, 164, 898, 860), radius=132, fill=(255, 255, 255, 28), outline=(255, 255, 255, 52), width=8)
    background.alpha_composite(panel)

    # 三条控制轨道：代表通道、会话、外观三项核心能力。
    content = Image.new("RGBA", (CANVAS, CANVAS), (0, 0, 0, 0))
    draw = ImageDraw.Draw(content)
    tracks = [
        (315, 372, 708, (103, 220, 255, 255), 610),
        (315, 510, 708, (153, 124, 255, 255), 420),
        (315, 648, 708, (77, 222, 169, 255), 650),
    ]
    for start, y, end, color, knob_x in tracks:
        draw.rounded_rectangle((start, y - 20, end, y + 20), radius=20, fill=(222, 232, 255, 80))
        draw.rounded_rectangle((start, y - 20, knob_x, y + 20), radius=20, fill=color)
        _draw_glow(content, (knob_x, y), color[:3] + (115,), 57)
        draw.ellipse((knob_x - 48, y - 48, knob_x + 48, y + 48), fill=(247, 250, 255, 255), outline=color, width=13)
        draw.ellipse((knob_x - 13, y - 13, knob_x + 13, y + 13), fill=color)

    # 角落的小星点让图标在桌面上更有辨识度。
    draw.ellipse((194, 210, 230, 246), fill=(246, 205, 106, 240))
    draw.ellipse((805, 744, 829, 768), fill=(246, 205, 106, 210))
    background.alpha_composite(content)
    return background


def main():
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    # 优先使用项目内保存的主图标源图；没有源图时才回退到旧的几何图形生成器。
    image = Image.open(MASTER).convert("RGBA") if MASTER.is_file() else build_icon()
    image.save(OUTPUT, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
    print(OUTPUT)


if __name__ == "__main__":
    main()
