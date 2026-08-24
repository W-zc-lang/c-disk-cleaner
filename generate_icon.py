"""生成清理主题 ICO 图标 (broom / 扫帚)."""
import os
from PIL import Image, ImageDraw


def draw_broom(size):
    """在 size x size 画布上绘制扫帚图标."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # 背景圆
    pad = size // 16
    draw.ellipse([pad, pad, size - pad, size - pad], fill="#0ea5e9")

    # 扫帚尺寸参数
    margin = size // 5
    handle_w = max(2, size // 16)
    handle_h = size // 2
    bristle_w = size // 3
    bristle_h = size // 4
    cx = size // 2
    top_y = margin
    handle_bottom = top_y + handle_h
    bristle_top = handle_bottom - size // 24
    bristle_bottom = bristle_top + bristle_h

    # 手柄
    draw.rounded_rectangle(
        [cx - handle_w // 2, top_y, cx + handle_w // 2, handle_bottom],
        radius=handle_w // 2,
        fill="white",
    )

    # 扫帚头
    draw.rounded_rectangle(
        [cx - bristle_w // 2, bristle_top, cx + bristle_w // 2, bristle_bottom],
        radius=max(2, size // 20),
        fill="white",
    )

    # 扫帚纹理线
    line_color = "#0ea5e9"
    line_w = max(1, size // 40)
    n = 3
    for i in range(1, n + 1):
        x = cx - bristle_w // 4 + (bristle_w // 2) * i // (n + 1)
        draw.line([(x, bristle_top + size // 20), (x, bristle_bottom - size // 20)], fill=line_color, width=line_w)

    return img


def main():
    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src", "gui", "assets")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "icon.ico")

    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_broom(s) for s in sizes]
    images[0].save(out_path, format="ICO", sizes=[(s, s) for s in sizes], append_images=images[1:])
    print("Generated icon:", out_path)


if __name__ == "__main__":
    main()
