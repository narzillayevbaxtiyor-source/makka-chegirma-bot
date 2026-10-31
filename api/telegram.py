"""
Telegram webhook (Vercel) — skrinshot rejimi.

@Foydaliadmi botga rasm yuboradi (do'kon sayti skrinshoti, katalog sahifasi yoki
narx yorlig'i) -> Gemini rasmni o'qiydi -> bot 2 xil tayyor post ko'rsatadi:
  A) sizning rasmingiz bilan
  B) bot yasagan kartochka bilan
Har birida «✅ Kanalga joylash» tugmasi bor. Bosilgani kanalga chiqadi.

Buyruqlar: /start, /linklar
"""

import base64
import html
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "").strip()
ADMIN = CONFIG.get("admin_username", "").lstrip("@").lower()
CHANNEL = CONFIG["channel"]

STORE_ALIASES = {
    "othaim": "Othaim", "panda": "Panda", "lulu": "Lulu", "carrefour": "Carrefour",
    "danube": "Danube", "bindawood": "BinDawood", "bin dawood": "BinDawood",
    "nokhba": "Al Nokhba", "noori": "Noori", "wafa": "Al Wafa", "abraj": "Abraj",
    "tamimi": "Tamimi", "farm": "Farm", "raya": "Al Raya", "manuel": "Manuel",
}


def norm_store(s: str) -> str:
    low = (s or "").lower()
    for k, v in STORE_ALIASES.items():
        if k in low:
            return v
    return (s or "").strip().title()


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def price(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def tg(method: str, **payload):
    r = requests.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{method}", json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description')}")
    return data["result"]


def tg_photo_bytes(chat_id, photo: bytes, caption: str, markup: dict | None = None):
    data = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if markup:
        data["reply_markup"] = json.dumps(markup)
    r = requests.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data=data,
        files={"photo": ("kartochka.jpg", photo, "image/jpeg")},
        timeout=60,
    )
    res = r.json()
    if not res.get("ok"):
        raise RuntimeError(f"sendPhoto: {res.get('description')}")
    return res["result"]


def download_file(file_id: str) -> tuple[bytes, str]:
    info = tg("getFile", file_id=file_id)
    path = info["file_path"]
    r = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{path}", timeout=60)
    r.raise_for_status()
    ext = path.rsplit(".", 1)[-1].lower()
    mime = {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg")
    return r.content, mime


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------
def pick_model() -> str:
    forced = os.environ.get("GEMINI_MODEL", "").strip()
    if forced:
        return forced
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": GEMINI_KEY, "pageSize": 200},
            timeout=20,
        )
        models = r.json().get("models", [])
    except Exception:
        models = []
    best, best_score = None, -1.0
    for m in models:
        name = m.get("name", "").replace("models/", "")
        if "generateContent" not in m.get("supportedGenerationMethods", []):
            continue
        if not re.fullmatch(r"gemini-\d+(\.\d+)?-flash(-latest)?", name):
            continue  # lite, image, tts, preview va h.k. emas
        ver = float(re.search(r"gemini-(\d+(?:\.\d+)?)", name).group(1))
        if ver > best_score:
            best, best_score = name, ver
    return best or "gemini-flash-latest"


GEMINI_PROMPT = """This image is a screenshot or photo of grocery offers in Makkah, Saudi Arabia
(a store website/app, a flyer page, or a shelf price tag).
Read it carefully and return ONLY JSON:
{
  "is_offer": true/false,
  "store": "store name in Latin letters if visible or given in the hint, else empty",
  "valid_to": "YYYY-MM-DD if an end date is visible, else empty",
  "products": [
    {"name": "product name as written (English or transliterated)",
     "name_uz": "short natural Uzbek (Latin script) name, keep brand names",
     "size": "size/weight/pack, or empty",
     "store": "store for this product if different stores are shown, else empty",
     "old_price": number or null,
     "new_price": number,
     "emoji": "one fitting emoji"}
  ]
}
Rules: only prices clearly visible in the image (SAR). old_price = the crossed-out/regular price,
new_price = the offer price. Never guess a number. Max 12 products, biggest discounts first.
"""


def gemini_read(img: bytes, mime: str, hint: str) -> dict:
    model = pick_model()
    body = {
        "contents": [{
            "parts": [
                {"text": GEMINI_PROMPT + (f"\nHint from admin: {hint}" if hint else "")},
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(img).decode()}},
            ]
        }],
        "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
    }
    r = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        params={"key": GEMINI_KEY},
        json=body,
        timeout=90,
    )
    data = r.json()
    if "candidates" not in data:
        raise RuntimeError(f"Gemini ({model}): {str(data.get('error', data))[:300]}")
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    text = re.sub(r"^```(?:json)?|```$", "", text.strip()).strip()
    return json.loads(text)


# --------------------------------------------------------------------------
# Post tayyorlash
# --------------------------------------------------------------------------
UZ_MONTHS = ["yanvar", "fevral", "mart", "aprel", "may", "iyun", "iyul", "avgust",
             "sentabr", "oktabr", "noyabr", "dekabr"]


def clean(data: dict, hint_store: str) -> tuple[str, list[dict]]:
    store = norm_store(data.get("store") or hint_store)
    items = []
    for p in data.get("products") or []:
        try:
            new = round(float(p.get("new_price")), 2)
        except (TypeError, ValueError):
            continue
        try:
            old = round(float(p.get("old_price")), 2) if p.get("old_price") else None
        except (TypeError, ValueError):
            old = None
        if new <= 0:
            continue
        pct = round((old - new) / old * 100) if old and old > new else 0
        if pct > 90:
            continue
        items.append({
            "name": p.get("name_uz") or p.get("name") or "",
            "size": p.get("size") or "",
            "store": norm_store(p.get("store") or store),
            "old_price": old if pct else None,
            "new_price": new,
            "pct": pct,
            "emoji": p.get("emoji") or "🛒",
        })
    items.sort(key=lambda x: x["pct"], reverse=True)
    return store, items


def build_caption(store: str, items: list[dict], valid_to: str, max_items: int) -> str:
    stores = sorted({i["store"] for i in items if i["store"]})
    single = len(stores) <= 1
    title = (stores[0] if stores else store) or "Makka"
    head = [f"🔥 <b>{esc(title.upper())}</b> — chegirmalar!" if single and title
            else "🔥 <b>Makkadagi chegirmalar!</b>"]
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", valid_to or "")
    if m:
        head.append(f"📅 {int(m.group(3))}-{UZ_MONTHS[int(m.group(2)) - 1]} gacha")
    head.append("📍 Makka")
    lines = []
    for it in items[:max_items]:
        size = f" {it['size']}" if it["size"] and it["size"].lower() not in it["name"].lower() else ""
        where = "" if single else f"  🏬 {esc(it['store'])}"
        if it["pct"]:
            pr = f"     <s>{price(it['old_price'])}</s> ➜ <b>{price(it['new_price'])} SAR</b>  (−{it['pct']}%)"
        else:
            pr = f"     <b>{price(it['new_price'])} SAR</b>"
        lines.append(f"{it['emoji']} <b>{esc(it['name'])}{esc(size)}</b>{where}\n{pr}")
    rest = len(items) - max_items
    tags = " ".join("#" + re.sub(r"[^0-9A-Za-z]", "", s) for s in stores[:4])
    footer = [""]
    if rest > 0:
        footer.append(f"➕ Yana {rest} ta mahsulot — rasmda 👆")
    footer += ["⏳ Aksiya zaxira tugaguncha amal qiladi.", f"{tags} #chegirma #Makka".strip(), f"👉 {CHANNEL}"]
    out = "\n".join(head + [""] + lines + footer)
    while len(out) > 1024 and lines:
        lines.pop()
        out = "\n".join(head + [""] + lines + footer)
    return out


POST_BTN = {"inline_keyboard": [[
    {"text": "✅ Kanalga joylash", "callback_data": "post"},
    {"text": "❌ Bekor", "callback_data": "del"},
]]}


def handle_photo(msg: dict) -> None:
    chat = msg["chat"]["id"]
    hint = (msg.get("caption") or "").strip()
    if msg.get("photo"):
        file_id = msg["photo"][-1]["file_id"]
    else:
        file_id = msg["document"]["file_id"]
    tg("sendMessage", chat_id=chat, text="⏳ Rasmni o'qiyapman...")
    img, mime = download_file(file_id)
    data = gemini_read(img, mime, hint)
    store, items = clean(data, hint)
    if not items:
        tg("sendMessage", chat_id=chat,
           text="😕 Rasmda narxlarni topa olmadim. Narxlar aniq ko'rinadigan skrinshot yuboring.")
        return

    # A) sizning rasmingiz bilan
    cap_a = build_caption(store, items, data.get("valid_to", ""), 6)
    tg("sendPhoto", chat_id=chat, photo=file_id, caption=cap_a, parse_mode="HTML", reply_markup=POST_BTN)

    # B) kartochka bilan (faqat chegirmali mahsulotlar)
    disc = [i for i in items if i["pct"]]
    if disc:
        try:
            from card import render_card
            stores = {i["store"] for i in disc if i["store"]}
            single = len(stores) <= 1
            title = (next(iter(stores)) if stores else store or "Makka").upper() if single else "Makkadagi chegirmalar"
            m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", data.get("valid_to") or "")
            when = f"{int(m.group(3))}-{UZ_MONTHS[int(m.group(2)) - 1]} gacha" if m else "Bugungi aksiyalar"
            card = render_card(title, f"{when}  •  Makka", disc, CHANNEL, not single, 8)
            cap_b = build_caption(store, disc, data.get("valid_to", ""), 3)
            tg_photo_bytes(chat, card, cap_b, POST_BTN)
        except Exception as e:
            tg("sendMessage", chat_id=chat, text=f"⚠️ Kartochka yasalmadi: {e}")
    tg("sendMessage", chat_id=chat, text="👆 Qaysi biri yoqsa, «✅ Kanalga joylash» ni bosing.")


def handle_callback(cq: dict) -> None:
    msg = cq.get("message") or {}
    chat = msg.get("chat", {}).get("id")
    if cq.get("data") == "del":
        tg("deleteMessage", chat_id=chat, message_id=msg["message_id"])
        tg("answerCallbackQuery", callback_query_id=cq["id"], text="O'chirildi")
        return
    if cq.get("data") == "post":
        payload = {"chat_id": CHANNEL, "caption": msg.get("caption", "")}
        if msg.get("caption_entities"):
            payload["caption_entities"] = msg["caption_entities"]
        tg("sendPhoto", photo=msg["photo"][-1]["file_id"], **payload)
        tg("editMessageReplyMarkup", chat_id=chat, message_id=msg["message_id"],
           reply_markup={"inline_keyboard": [[{"text": "✅ Joylandi", "callback_data": "done"}]]})
        tg("answerCallbackQuery", callback_query_id=cq["id"], text="✅ Kanalga joylandi")
        return
    tg("answerCallbackQuery", callback_query_id=cq["id"])


HELP = (
    "Assalomu alaykum! 👋\n\n"
    "📸 Menga do'kon sayti skrinshotini, katalog sahifasini yoki narx yorlig'i rasmini yuboring. "
    "Rasm izohiga do'kon nomini yozsangiz (masalan: <i>Panda Aziziya</i>), yanada aniq bo'ladi.\n\n"
    "Men narxlarni o'qib, 2 xil tayyor post ko'rsataman. Yoqqanini «✅ Kanalga joylash» bilan "
    f"{CHANNEL} ga chiqarasiz.\n\n"
    "/linklar — chegirma sahifalari ro'yxati"
)


def handle_update(upd: dict) -> None:
    if "callback_query" in upd:
        cq = upd["callback_query"]
        if (cq.get("from", {}).get("username") or "").lower() != ADMIN:
            tg("answerCallbackQuery", callback_query_id=cq["id"], text="Ruxsat yo'q")
            return
        handle_callback(cq)
        return
    msg = upd.get("message") or {}
    if not msg:
        return
    chat = msg["chat"]["id"]
    user = (msg.get("from", {}).get("username") or "").lower()
    if user != ADMIN:
        if msg.get("chat", {}).get("type") == "private":
            tg("sendMessage", chat_id=chat, text=f"Assalomu alaykum! Chegirmalarni {CHANNEL} kanalida kuzatib boring 🛒")
        return
    text = (msg.get("text") or "").strip()
    if msg.get("photo") or (msg.get("document", {}).get("mime_type", "").startswith("image/")):
        try:
            handle_photo(msg)
        except Exception as e:
            tg("sendMessage", chat_id=chat, text=f"⚠️ Xato: {str(e)[:500]}")
        return
    if text.startswith("/linklar"):
        links = "\n".join(f"• {u}" for u in CONFIG.get("extra_pages", []))
        tg("sendMessage", chat_id=chat, disable_web_page_preview=True,
           text="🔗 Chegirma sahifalari (ochib, skrinshot qilib menga yuboring):\n\n" + links)
        return
    tg("sendMessage", chat_id=chat, text=HELP, parse_mode="HTML")


class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        if WEBHOOK_SECRET and self.headers.get("X-Telegram-Bot-Api-Secret-Token") != WEBHOOK_SECRET:
            self.send_response(403)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length") or 0)
        try:
            upd = json.loads(self.rfile.read(length) or b"{}")
            handle_update(upd)
        except Exception as e:  # Telegram qayta yubormasligi uchun baribir 200
            print("xato:", e)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_GET(self):
        missing = [n for n, v in (("TELEGRAM_BOT_TOKEN", BOT_TOKEN), ("GEMINI_API_KEY", GEMINI_KEY),
                                  ("WEBHOOK_SECRET", WEBHOOK_SECRET)) if not v]
        if missing:
            out = "⚠️ Vercel'da kalitlar yetishmayapti: " + ", ".join(missing)
        elif "setup" in (self.path or ""):
            # Webhookni faqat shu manzilning o'ziga ulaydi — xavfsiz, qayta bosilsa ham zarar yo'q
            host = self.headers.get("x-forwarded-host") or self.headers.get("host")
            try:
                tg("setWebhook", url=f"https://{host}/api/telegram", secret_token=WEBHOOK_SECRET,
                   allowed_updates=["message", "callback_query"])
                out = "✅ Skrinshot boti ulandi! Endi @Oziqovaqt_bot ga rasm yuboring."
            except Exception as e:
                out = f"⚠️ Ulanmadi: {e}"
        else:
            try:
                info = tg("getWebhookInfo")
                ok = "/api/telegram" in info.get("url", "")
                out = "Bot ishlayapti ✅" if ok else "Kalitlar bor ✅, lekin webhook ulanmagan. Manzil oxiriga ?setup=1 qo'shib oching."
                if info.get("last_error_message"):
                    out += f"\nOxirgi xato: {info['last_error_message']}"
            except Exception as e:
                out = f"⚠️ Telegram: {e}"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(out.encode())
