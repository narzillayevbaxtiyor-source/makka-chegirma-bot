"""
Makka chegirmalar boti — @Oziqovaqt_bot -> kanal @tartibliaka

Ishlash tartibi (har ishga tushganda):
  1. config.json dagi manbalardan (RSS + qo'shimcha sahifalar) yangi aksiya
     maqolalarini topadi.
  2. Sahifa matnini DeepSeek'ga beradi -> mahsulot, eski/yangi narx, do'kon,
     hudud, muddatni JSON ko'rinishida ajratib oladi va nomlarni o'zbekchaga
     o'giradi.
  3. Narxlarni manba matni bilan solishtiradi (AI o'ylab topmasligi uchun),
     chegirma foizini o'zi hisoblaydi, kichik chegirmalarni tashlab yuboradi.
  4. Makkaga tegishli bo'lsa, eng katta chegirmalarni bitta chiroyli post
     qilib kanalga joylaydi.
  5. Joylanganlarni state/posted.json ga yozadi (takrorlanmasligi uchun).
"""

import html
import json
import os
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CONFIG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
STATE_FILE = ROOT / "state" / "posted.json"

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "").strip()
DRY_RUN = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

UA = (
    "Mozilla/5.0 (Linux; Android 10; SM-G973F) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"
)
MAKKAH_TZ = timezone(timedelta(hours=3))
UZ_MONTHS = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]

log_lines: list[str] = []


def log(msg: str) -> None:
    print(msg, flush=True)
    log_lines.append(msg)


# --------------------------------------------------------------------------
# Holat (state)
# --------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"sources": {}, "products": {}, "admin_chat_id": None}


def save_state(state: dict) -> None:
    keep_days = int(CONFIG.get("remember_days", 60))
    cutoff = (datetime.now(MAKKAH_TZ) - timedelta(days=keep_days)).date().isoformat()
    for key in ("sources", "products"):
        state[key] = {k: v for k, v in state.get(key, {}).items() if v >= cutoff}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )


# --------------------------------------------------------------------------
# Telegram
# --------------------------------------------------------------------------
def tg(method: str, **payload):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    r = requests.post(url, json=payload, timeout=30)
    data = r.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


def find_admin_chat(state: dict) -> None:
    """Admin botga /start bosgan bo'lsa, uning chat_id sini eslab qoladi."""
    admin = CONFIG.get("admin_username", "").lstrip("@").lower()
    if not admin or not BOT_TOKEN:
        return
    try:
        updates = tg("getUpdates", timeout=0, allowed_updates=["message"])
    except Exception as e:  # webhook o'rnatilgan bo'lsa ham shu yerga tushadi
        log(f"⚠️ getUpdates ishlamadi: {e}")
        return
    for u in updates:
        m = u.get("message") or {}
        frm = m.get("from") or {}
        if (frm.get("username") or "").lower() == admin:
            state["admin_chat_id"] = m["chat"]["id"]
    if updates:
        # o'qilgan xabarlarni tozalash
        try:
            tg("getUpdates", offset=updates[-1]["update_id"] + 1, timeout=0)
        except Exception:
            pass


def notify_admin(state: dict, text: str) -> None:
    chat = state.get("admin_chat_id")
    if not chat or not BOT_TOKEN:
        return
    try:
        tg("sendMessage", chat_id=chat, text=text[:4000], disable_web_page_preview=True)
    except Exception as e:
        print(f"admin xabari ketmadi: {e}")


def publish(caption: str, image_url: str | None, state: dict) -> None:
    channel = CONFIG["channel"]
    target = state.get("admin_chat_id") if DRY_RUN else channel
    if DRY_RUN and not target:
        log("DRY_RUN: admin chat topilmadi, post faqat logda:\n" + caption)
        return
    if image_url:
        try:
            tg("sendPhoto", chat_id=target, photo=image_url, caption=caption, parse_mode="HTML")
            return
        except Exception as e:
            log(f"⚠️ Rasm bilan yuborilmadi ({e}), matn bilan yuboraman")
    tg(
        "sendMessage",
        chat_id=target,
        text=caption,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )


# --------------------------------------------------------------------------
# Manbalarni yig'ish
# --------------------------------------------------------------------------
def http_get(url: str) -> str:
    r = requests.get(
        url,
        headers={"User-Agent": UA, "Accept-Language": "en,ar;q=0.8"},
        timeout=40,
    )
    r.raise_for_status()
    r.encoding = r.encoding or "utf-8"
    return r.text


def rss_items(feed_url: str) -> list[dict]:
    xml = http_get(feed_url)
    root = ET.fromstring(xml.encode("utf-8"))
    items = []
    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if title and link:
            items.append({"title": title, "url": link})
    # Atom formatini ham qo'llab-quvvatlash
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for e in root.findall(".//a:entry", ns):
        title = (e.findtext("a:title", default="", namespaces=ns) or "").strip()
        link_el = e.find("a:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        if title and link:
            items.append({"title": title, "url": link})
    return items


def title_matches(title: str) -> bool:
    t = title.lower()
    stores = [s.lower() for s in CONFIG["stores"]]
    country = [k.lower() for k in CONFIG["country_keywords"]]
    blocked = [k.lower() for k in CONFIG.get("skip_title_keywords", [])]
    if any(b in t for b in blocked):
        return False
    return any(s in t for s in stores) and any(c in t for c in country)


def collect_candidates(state: dict) -> list[dict]:
    cands: list[dict] = []
    for feed in CONFIG.get("rss_feeds", []):
        try:
            items = rss_items(feed)
            log(f"RSS {feed}: {len(items)} ta maqola")
        except Exception as e:
            log(f"⚠️ RSS o'qilmadi {feed}: {e}")
            continue
        for it in items:
            if it["url"] in state["sources"]:
                continue
            if title_matches(it["title"]):
                cands.append(it)
    pages = list(CONFIG.get("extra_pages", []))
    random.shuffle(pages)  # har safar boshqa sahifalardan boshlash
    for url in pages:
        cands.append({"title": "", "url": url, "always_recheck": True})
    # takrorlarni olib tashlash
    seen, uniq = set(), []
    for c in cands:
        if c["url"] not in seen:
            seen.add(c["url"])
            uniq.append(c)
    return uniq[: int(CONFIG.get("max_pages_per_run", 6))]


def page_to_text(raw: str) -> tuple[str, str | None]:
    img = None
    m = re.search(
        r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', raw, re.I
    ) or re.search(
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', raw, re.I
    )
    if m:
        img = html.unescape(m.group(1))
    body = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", raw)
    body = re.sub(r"(?i)<br\s*/?>|</(p|li|div|h\d|tr)>", "\n", body)
    body = re.sub(r"<[^>]+>", " ", body)
    body = html.unescape(body)
    body = re.sub(r"[ \t\r\f\v]+", " ", body)
    body = re.sub(r"\n\s*\n+", "\n", body).strip()
    return body[:18000], img


# --------------------------------------------------------------------------
# DeepSeek
# --------------------------------------------------------------------------
EXTRACT_PROMPT = """You extract supermarket promotion data from a web page about grocery offers in Saudi Arabia.
Return ONLY a JSON object with this exact shape:
{
  "store": "store/brand name in Latin letters, e.g. Danube, Panda, Othaim, BinDawood, Lulu, Carrefour, Tamimi, Farm",
  "region_text": "region/city/branch info exactly as the page says, or empty",
  "covers_makkah": "yes" | "no" | "unknown",
  "valid_from": "YYYY-MM-DD or empty",
  "valid_to": "YYYY-MM-DD or empty",
  "products": [
    {"store": "store name for THIS product in Latin letters (pages may list several stores)",
     "name": "product name as on the page (English)",
     "name_uz": "short natural Uzbek (Latin script) name of the product, keep brand names, e.g. 'Nido sut kukuni'",
     "size": "size/weight/pack as on the page, e.g. '2.25 kg', '4x200 ml', or empty",
     "old_price": number,
     "new_price": number,
     "emoji": "one fitting food emoji"}
  ]
}
Rules:
- covers_makkah = "yes" if the offer is valid in Makkah / Mecca / Western Region / Jeddah & Makkah / all branches in KSA / all Saudi Arabia.
  "no" if it is clearly only for another region or city (Riyadh, Eastern, Southern, Central, Qassim, etc.) or another country (UAE, Qatar, Kuwait, Oman, Bahrain).
  "unknown" if the page does not say.
- Only include products where BOTH the old (regular) price and the new (offer) price are written on the page. Never guess or compute prices.
- Prices are numbers in SAR, without currency.
- Skip non-food items unless they are household essentials (detergent, tissues, diapers). Skip electronics, luggage, furniture, cosmetics.
- If the page has no such products, return "products": [].
"""


def deepseek_extract(text: str, title: str) -> dict:
    r = requests.post(
        "https://api.deepseek.com/chat/completions",
        headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"},
        json={
            "model": CONFIG.get("deepseek_model", "deepseek-chat"),
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": EXTRACT_PROMPT},
                {"role": "user", "content": f"PAGE TITLE: {title}\n\nPAGE TEXT:\n{text}"},
            ],
        },
        timeout=180,
    )
    r.raise_for_status()
    content = r.json()["choices"][0]["message"]["content"]
    return json.loads(content)


# --------------------------------------------------------------------------
# Tekshirish va post tayyorlash
# --------------------------------------------------------------------------
def numbers_in(text: str) -> set[float]:
    out = set()
    for n in re.findall(r"\d+(?:[.,]\d+)?", text):
        try:
            out.add(round(float(n.replace(",", ".")), 2))
        except ValueError:
            pass
    return out


def to_float(x) -> float | None:
    try:
        return round(float(str(x).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def clean_products(data: dict, page_text: str) -> list[dict]:
    nums = numbers_in(page_text)
    min_pct = float(CONFIG.get("min_discount_percent", 25))
    good = []
    for p in data.get("products") or []:
        old, new = to_float(p.get("old_price")), to_float(p.get("new_price"))
        if not old or not new or new <= 0 or new >= old:
            continue
        # AI narxni o'ylab topmaganini tekshirish: ikkala narx ham sahifada bo'lishi shart
        if old not in nums or new not in nums:
            continue
        pct = round((old - new) / old * 100)
        if pct < min_pct or pct > 90:
            continue
        p["old_price"], p["new_price"], p["pct"] = old, new, pct
        good.append(p)
    good.sort(key=lambda x: x["pct"], reverse=True)
    return good


def fmt_price(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def fmt_date(s: str) -> str | None:
    try:
        d = date.fromisoformat(s)
    except (TypeError, ValueError):
        return None
    return f"{d.day}-{UZ_MONTHS[d.month - 1]}"


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
    return (s or "Do'kon").strip().title()


def hashtag(s: str) -> str:
    return "#" + re.sub(r"[^0-9A-Za-z_]", "", s.title().replace(" ", ""))


def build_post(store: str, data: dict, products: list[dict]) -> str:
    e = html.escape
    stores = {norm_store(p.get("store") or store) for p in products}
    single = len(stores) == 1
    if single:
        store = next(iter(stores))
        head = [f"🔥 <b>{e(store.upper())}</b> — katta chegirmalar!"]
    else:
        head = ["🔥 <b>Makkadagi eng katta chegirmalar!</b>"]
    vf, vt = fmt_date(data.get("valid_from", "")), fmt_date(data.get("valid_to", ""))
    if vf and vt:
        head.append(f"📅 {vf} – {vt}")
    elif vt:
        head.append(f"📅 {vt} gacha")
    head.append("📍 Makka filiallarida")
    footer = [
        "",
        "⏳ Aksiya zaxira tugaguncha amal qiladi.",
        " ".join(hashtag(s) for s in sorted(stores)[:4]) + " #chegirma #Makka",
        f"👉 {CONFIG['channel']}",
    ]
    max_items = int(CONFIG.get("max_items_per_post", 10))
    lines: list[str] = []
    for i, p in enumerate(products[:max_items], 1):
        name = p.get("name_uz") or p.get("name") or ""
        size = f" {p['size']}" if p.get("size") and p["size"].lower() not in name.lower() else ""
        where = "" if single else f"  🏬 {e(norm_store(p.get('store') or store))}"
        item = (
            f"{p.get('emoji') or '🛒'} <b>{e(name)}{e(size)}</b>{where}\n"
            f"     <s>{fmt_price(p['old_price'])}</s> ➜ <b>{fmt_price(p['new_price'])} SAR</b>"
            f"  (−{p['pct']}%)"
        )
        candidate = "\n".join(head + [""] + lines + [item] + footer)
        if len(candidate) > 1024:  # Telegram rasm izohi chegarasi
            break
        lines.append(item)
    return "\n".join(head + [""] + lines + footer)


# --------------------------------------------------------------------------
# Asosiy
# --------------------------------------------------------------------------
def main() -> int:
    if not BOT_TOKEN or not DEEPSEEK_KEY:
        print("❌ TELEGRAM_BOT_TOKEN yoki DEEPSEEK_API_KEY berilmagan (GitHub Secrets).")
        return 1

    state = load_state()
    find_admin_chat(state)
    today = datetime.now(MAKKAH_TZ).date()
    today_s = today.isoformat()

    posted = 0
    max_posts = int(CONFIG.get("max_posts_per_run", 3))
    errors = 0

    for c in collect_candidates(state):
        if posted >= max_posts:
            break
        url = c["url"]
        try:
            raw = http_get(url)
            text, img = page_to_text(raw)
            if len(text) < 200:
                log(f"– Matn juda qisqa: {url}")
                state["sources"][url] = today_s
                continue
            data = deepseek_extract(text, c["title"])
        except Exception as e:
            errors += 1
            log(f"⚠️ {url}: {e}")
            continue

        if not c.get("always_recheck"):
            state["sources"][url] = today_s

        region = (data.get("covers_makkah") or "unknown").lower()
        if region == "no" or (region == "unknown" and not CONFIG.get("post_if_region_unknown", True)):
            log(f"– Makkaga tegishli emas ({data.get('region_text')}): {url}")
            continue
        vt = data.get("valid_to") or ""
        if vt and vt < today_s:
            log(f"– Muddati o'tgan ({vt}): {url}")
            continue

        store = (data.get("store") or "Do'kon").strip()
        products = []
        for p in clean_products(data, text):
            key = f"{norm_store(p.get('store') or store)}|{p.get('name','').lower()}|{p['new_price']}"
            if key not in state["products"]:
                p["_key"] = key
                products.append(p)
        if len(products) < int(CONFIG.get("min_items_per_post", 3)):
            log(f"– Yetarli katta chegirma yo'q ({len(products)} ta): {url}")
            continue

        caption = build_post(store, data, products)
        try:
            publish(caption, img if CONFIG.get("attach_image", True) else None, state)
        except Exception as e:
            errors += 1
            log(f"⚠️ Kanalga yuborilmadi: {e}")
            continue
        if not DRY_RUN:  # sinovda eslab qolmaymiz, haqiqiy ishga tushishda chiqsin
            for p in products[: int(CONFIG.get("max_items_per_post", 10))]:
                state["products"][p["_key"]] = today_s
        posted += 1
        log(f"✅ Joylandi: {store} — {len(products)} ta mahsulot ({url})")
        time.sleep(3)

    summary = f"Bot ishladi: {posted} ta post, {errors} ta xato."
    log(summary)
    save_state(state)
    try:
        (ROOT / "state" / "last_run.txt").write_text(
            datetime.now(MAKKAH_TZ).strftime("%Y-%m-%d %H:%M") + "\n" + "\n".join(log_lines),
            encoding="utf-8",
        )
    except Exception:
        pass
    if posted or errors or DRY_RUN:
        notify_admin(state, "🤖 @Oziqovaqt_bot hisoboti\n" + "\n".join(log_lines[-25:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
