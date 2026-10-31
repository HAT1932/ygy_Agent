"""生成演示用穿搭示意图（离线可用），让真实 VLM 链路可以不依赖外部素材跑起来。

这些图是**示意性平铺图**，不是照片：只用于验证"图片 → 单品结构化 → 规则 → report"
全链路，以及在无外部素材时演示。真实效果请替换为真人穿搭照。
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "assets" / "samples"
S = 640


def canvas(bg: str = "#F5F3EE") -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (S, S), bg)
    return img, ImageDraw.Draw(img)


def rect(d, box, color, outline=None):
    d.rectangle(box, fill=color, outline=outline or color, width=3)


def make_commute_polished() -> Image.Image:
    img, d = canvas()
    rect(d, (210, 40, 430, 300), "#1F2A44")          # 藏青针织衫
    rect(d, (250, 30, 300, 60), "#1F2A44")           # 领口
    rect(d, (200, 320, 440, 540), "#DDD3C0")         # 燕麦色直筒裤
    rect(d, (205, 320, 435, 345), "#C9BDA6")
    rect(d, (215, 560, 305, 610), "#2B2B2B")         # 黑色乐福鞋
    rect(d, (335, 560, 425, 610), "#2B2B2B")
    rect(d, (250, 420, 390, 425), "#8A7A62")         # 腰带
    return img


def make_color_clash() -> Image.Image:
    img, d = canvas()
    rect(d, (210, 40, 430, 300), "#E4002B")          # 高饱和大红
    rect(d, (200, 320, 440, 540), "#00A651")         # 鲜绿
    rect(d, (215, 560, 305, 610), "#FFD100")         # 亮黄鞋
    rect(d, (335, 560, 425, 610), "#FFD100")
    rect(d, (250, 150, 390, 160), "#FFFFFF")
    return img


def make_sport_active() -> Image.Image:
    img, d = canvas("#EDEFF2")
    rect(d, (200, 50, 440, 290), "#3A3F47")          # 灰色运动卫衣
    rect(d, (230, 40, 410, 80), "#3A3F47")
    rect(d, (210, 310, 430, 470), "#111827")         # 黑色运动短裤
    rect(d, (205, 490, 300, 545), "#E8E8E8")         # 白色运动鞋
    rect(d, (340, 490, 435, 545), "#E8E8E8")
    rect(d, (205, 540, 300, 560), "#9CA3AF")
    rect(d, (340, 540, 435, 560), "#9CA3AF")
    return img


def make_formal_suit() -> Image.Image:
    img, d = canvas("#EFEFEF")
    rect(d, (190, 40, 450, 320), "#1A1A1A")          # 黑色西装外套
    rect(d, (300, 60, 340, 300), "#FFFFFF")          # 白衬衫
    rect(d, (250, 70, 300, 190), "#1A1A1A")
    rect(d, (340, 70, 390, 190), "#1A1A1A")
    rect(d, (300, 80, 340, 110), "#6B0F1A")          # 领带
    rect(d, (300, 110, 340, 280), "#6B0F1A")
    rect(d, (205, 330, 435, 560), "#232323")         # 西裤
    rect(d, (210, 570, 310, 620), "#0F0F0F")         # 牛津鞋
    rect(d, (335, 570, 435, 620), "#0F0F0F")
    return img


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    items = {
        "commute_polished.png": make_commute_polished,
        "color_clash.png": make_color_clash,
        "sport_active.png": make_sport_active,
        "formal_suit.png": make_formal_suit,
    }
    for name, fn in items.items():
        p = OUT / name
        fn().save(p, format="PNG", optimize=True)
        print(f"wrote {p}  ({p.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
