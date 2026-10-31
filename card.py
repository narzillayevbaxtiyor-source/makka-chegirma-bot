"""Chegirma posti uchun rasm-kartochka (PNG/JPEG) yasaydi."""

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parent / "fonts"
W = 1080
PAD = 48

# Ranglar
ORANGE_1 = (232, 65, 24)
ORANGE_2 = (255, 145, 0)
CREAM = (255, 247, 237)
WHITE = (255, 255, 255)
DARK = (31, 31, 46)
GREY = (128, 128, 140)
GREEN = (0, 150, 80)
BADGE = (229, 30, 54)
CHIP = (255, 232, 204)
CHIP_TXT = (176, 74, 0)


def _f(weight: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONT_DIR / f"Poppins-{weight}.ttf"), size)


def _price(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _wrap(draw, text, font, max_w, max_lines=2):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font) <= max_w:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while draw.textlength(lines[-1] + "…", font=font) > max_w and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines


def _gradient(w, h, c1, c2):
    img = Image.new("RGB", (w, h), c1)
    d = ImageDraw.Draw(img)
    for x in range(w):
        t = x / max(w - 1, 1)
        col = tuple(int(c1[i] + (c2[i] - c1[i]) * t) for i in range(3))
        d.line([(x, 0), (x, h)], fill=col)
    return img


def render_card(
    title: str,
    subtitle: str,
    items: list[dict],
    channel: str,
    show_store: bool,
    max_items: int = 8,
) -> bytes:
    """items: {name, size, store, old_price, new_price, pct}"""
    items = items[:max_items]
    ROW_H = 150 if show_store else 128
    GAP = 18
    HEAD_H = 300
    FOOT_H = 130
    H = HEAD_H + PAD + len(items) * (ROW_H + GAP) - GAP + PAD + FOOT_H
    H = max(H, 1080)

    img = Image.new("RGB", (W, H), CREAM)
    img.paste(_gradient(W, HEAD_H, ORANGE_1, ORANGE_2), (0, 0))
    d = ImageDraw.Draw(img)

    # --- sarlavha
    small = _f("Medium", 34)
    d.rounded_rectangle([PAD, 44, PAD + 330, 98], radius=27, fill=WHITE)
    d.text((PAD + 165, 71), "KATTA CHEGIRMA", font=small, fill=ORANGE_1, anchor="mm")
    tfont = _f("Bold", 84)
    while d.textlength(title, font=tfont) > W - 2 * PAD and tfont.size > 44:
        tfont = _f("Bold", tfont.size - 4)
    d.text((PAD, 125), title, font=tfont, fill=WHITE)
    d.text((PAD, 232), subtitle, font=_f("Medium", 36), fill=(255, 238, 220))

    # --- mahsulotlar
    y = HEAD_H + PAD
    name_f = _f("Bold", 36)
    size_f = _f("Regular", 28)
    old_f = _f("Regular", 32)
    new_f = _f("Bold", 50)
    cur_f = _f("Medium", 26)
    badge_f = _f("Bold", 30)
    chip_f = _f("Medium", 24)
    PRICE_W = 250
    BADGE_D = 104
    for it in items:
        x0, x1 = PAD, W - PAD
        d.rounded_rectangle([x0 + 4, y + 6, x1 + 4, y + ROW_H + 6], radius=26, fill=(240, 226, 210))
        d.rounded_rectangle([x0, y, x1, y + ROW_H], radius=26, fill=WHITE)

        # foiz nishoni
        bx, by = x0 + 22, y + (ROW_H - BADGE_D) // 2
        d.ellipse([bx, by, bx + BADGE_D, by + BADGE_D], fill=BADGE)
        d.text((bx + BADGE_D / 2, by + BADGE_D / 2), f"-{it['pct']}%", font=badge_f, fill=WHITE, anchor="mm")

        # nom
        tx = bx + BADGE_D + 24
        text_w = x1 - PRICE_W - tx - 16
        lines = _wrap(d, it["name"], name_f, text_w, 2)
        ty = y + 18
        for ln in lines:
            d.text((tx, ty), ln, font=name_f, fill=DARK)
            ty += 44
        meta_y = ty + 2
        if it.get("size"):
            d.text((tx, meta_y), it["size"], font=size_f, fill=GREY)
            meta_y += 36
        if show_store and it.get("store"):
            sw = d.textlength(it["store"], font=chip_f) + 28
            cy = min(meta_y, y + ROW_H - 42)
            if it.get("size"):  # o'lchov yonida chiqaramiz
                sx = tx + d.textlength(it["size"], font=size_f) + 16
                cy = meta_y - 36
            else:
                sx = tx
            if sx + sw < x1 - PRICE_W:
                d.rounded_rectangle([sx, cy, sx + sw, cy + 34], radius=17, fill=CHIP)
                d.text((sx + sw / 2, cy + 17), it["store"], font=chip_f, fill=CHIP_TXT, anchor="mm")

        # narxlar
        px = x1 - 26
        old = _price(it["old_price"])
        oy = y + 22
        d.text((px, oy), old, font=old_f, fill=GREY, anchor="ra")
        ow = d.textlength(old, font=old_f)
        d.line([(px - ow - 4, oy + 22), (px + 4, oy + 22)], fill=BADGE, width=3)
        new = _price(it["new_price"])
        ny = y + ROW_H - 26
        cw = d.textlength("SAR", font=cur_f)
        d.text((px, ny), "SAR", font=cur_f, fill=GREEN, anchor="rs")
        d.text((px - cw - 8, ny), new, font=new_f, fill=GREEN, anchor="rs")
        y += ROW_H + GAP

    # --- pastki qism
    fy = H - FOOT_H
    d.rectangle([0, fy, W, H], fill=DARK)
    d.text((PAD, fy + FOOT_H / 2), channel, font=_f("Bold", 44), fill=WHITE, anchor="lm")
    d.text(
        (W - PAD, fy + FOOT_H / 2),
        "Makkadagi eng arzon narxlar",
        font=_f("Regular", 30),
        fill=(200, 200, 210),
        anchor="rm",
    )

    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()
