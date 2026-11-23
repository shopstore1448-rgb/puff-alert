# -*- coding: utf-8 -*-
"""PUFF restock alert: checks boutiqaat PUFF stock and sends a Telegram message
when a colour/size goes from out of stock (0) to in stock. Runs on GitHub Actions.
Standalone copy of the scraping logic; it does not touch any other program."""
import json, os, re, sys, gzip, time
import urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
BASE = "https://www.boutiqaat.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
EX = re.compile(r"ماصة|غطاء|قاعدة|حامل|دفتر|قالب|shaker|شيكر|ستيكر|sticker|stickii|matcha-set|تحضير الماتشا|\blid\b|straw(?!berry)|handle|boot|journal|ice-?tray|glass|phone|puff-jr|flippii|accessor|bottle-lid", re.I)
LIST_PATH = "/ar-sa/women/puff/br/cid/480/?page=%d"

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
# Optional: only alert for names containing one of these words (comma separated)
WATCH = [w.strip() for w in os.environ.get("WATCH", "").split(",") if w.strip()]


def fetch(path, tries=3):
    url = BASE + path
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.8", "Accept-Encoding": "gzip", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=60) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding", "").lower() == "gzip":
                    raw = gzip.decompress(raw)
            m = NEXT_RE.search(raw.decode("utf-8", "replace"))
            if not m:
                raise ValueError("no __NEXT_DATA__ (blocked?)")
            return json.loads(m.group(1))
        except Exception as e:
            last = e
            time.sleep(2 * (a + 1))
    raise RuntimeError("%s: %s" % (url, last))


def mlv(v):
    v = str(v)
    if v.upper().startswith("1L"):
        return 1000
    d = re.sub(r"\D", "", v)
    return int(d) if d else 0


def list_page(p):
    d = fetch(LIST_PATH % p)
    pr = d["props"]["pageProps"]["response"]["products"]
    if pr and isinstance(pr[0], list):
        pr = pr[0]
    out = {}
    for x in pr or []:
        s = re.sub(r"/p/?$", "", x.get("slug") or "")
        if s:
            out[s] = x.get("name") or ""
    return out


def product(slug):
    d = fetch("/ar-sa/women/" + slug + "/p/")
    r = d["props"]["pageProps"]["response"]
    r = r[0] if isinstance(r, list) else r
    name = r.get("name") or slug
    items = {}
    for o in r.get("multi_configurable_option") or []:
        ch = o.get("single_child_info") or {}
        url = "https://www.boutiqaat.com/ar-sa/women/" + (ch.get("slug") or slug) + "/p/"
        items[url] = {"name": name, "ml": mlv(o.get("value")),
                      "qty": int(float(o.get("available_qty") or 0)),
                      "price": int(float(ch.get("final_price_with_tax") or 0)), "url": url}
    if not items:
        url = "https://www.boutiqaat.com/ar-sa/women/" + slug + "/p/"
        items[url] = {"name": name, "ml": 0, "qty": int(float(r.get("qty_available") or 0)),
                      "price": int(float(r.get("final_price_with_tax") or 0)), "url": url}
    return items


def scrape():
    names = {}
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda p: _safe(list_page, p), range(1, 31)):
            if isinstance(res, dict):
                names.update(res)
    slugs = [s for s in names if not EX.search(s) and not EX.search(names[s])]
    if len(slugs) < 150:
        raise RuntimeError("only %d products found (site blocked or changed?)" % len(slugs))
    live, failed = {}, 0
    with ThreadPoolExecutor(6) as ex:
        for res in ex.map(lambda s: _safe(product, s), slugs):
            if isinstance(res, dict):
                live.update(res)
            else:
                failed += 1
    if failed > len(slugs) * 0.2:
        raise RuntimeError("%d of %d product pages failed" % (failed, len(slugs)))
    print("products: %d, sizes: %d, failed pages: %d" % (len(slugs), len(live), failed))
    return live


def _safe(fn, arg):
    try:
        return fn(arg)
    except Exception as e:
        print("warn:", str(e)[:200])
        return None


def tg(method, data=None):
    url = "https://api.telegram.org/bot%s/%s" % (TOKEN, method)
    body = urllib.parse.urlencode(data).encode() if data else None
    with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def find_chat_id(state):
    if CHAT_ID:
        return CHAT_ID
    if not TOKEN:
        return ""
    try:
        for u in reversed(tg("getUpdates").get("result", [])):
            m = u.get("message") or u.get("channel_post") or {}
            if m.get("chat", {}).get("id"):
                cid = str(m["chat"]["id"])
                # Public repo: never save the chat id in the repo. Tell the user to store it as a secret.
                tg("sendMessage", {"chat_id": cid, "text":
                   "رقم محادثتك هو:\n%s\n\nانسخه وحطه في GitHub كـ Secret باسم TELEGRAM_CHAT_ID" % cid})
                return cid
    except Exception as e:
        print("getUpdates failed:", e)
    return ""


def send(chat, text):
    if not TOKEN or not chat:
        print("(telegram not set up) would send:\n" + text)
        return
    chunks, cur = [], ""
    for block in text.split("\n\n"):
        if len(cur) + len(block) > 3800:
            chunks.append(cur)
            cur = ""
        cur += block + "\n\n"
    chunks.append(cur)
    for c in chunks:
        if c.strip():
            tg("sendMessage", {"chat_id": chat, "text": c.strip(), "disable_web_page_preview": "true"})


def fmt(it, tag):
    size = ("الحجم: %d مل\n" % it["ml"]) if it["ml"] else ""
    return "%s%s\n%sالسعر: %d ر.س\nالكمية: %d\n%s" % (tag, it["name"], size, it["price"], it["qty"], it["url"])


def wanted(it):
    return not WATCH or any(w in it["name"] for w in WATCH)


def main():
    state = {}
    if os.path.exists(STATE):
        state = json.load(open(STATE, encoding="utf-8"))
    old = state.get("items", {})
    before = json.dumps(state, sort_keys=True, ensure_ascii=False)
    chat = find_chat_id(state)
    if TOKEN and not chat:
        print("No chat id yet: open your bot in Telegram, press Start, then run again.")

    if "--test" in sys.argv:
        live = product(next(iter(list_page(1))))
        print("boutiqaat OK:", json.dumps(next(iter(live.values())), ensure_ascii=False))
        send(chat, "✅ تجربة ناجحة: السيرفر قدر يقرأ متجر بوتيكات، والبوت شغّال.")
        save(state, before)
        return

    live = scrape()
    new = {k: (1 if v["qty"] > 0 else 0) for k, v in live.items()}
    msgs = []
    if not old:
        out = sum(1 for v in new.values() if not v)
        msgs.append("👋 بدأت المراقبة.\nأراقب %d حجم/لون، منها %d غير متوفر الآن.\n"
                    "بأرسل لك رسالة أول ما يتوفر أي واحد منها." % (len(new), out))
    else:
        for k, it in sorted(live.items(), key=lambda kv: kv[1]["name"]):
            if not new[k] or not wanted(it):
                continue
            if old.get(k) == 0:
                msgs.append(fmt(it, "🟢 رجع متوفر!\n"))
            elif k not in old:
                msgs.append(fmt(it, "🆕 منتج جديد متوفر!\n"))
    merged = dict(old)
    merged.update(new)  # sizes not read this time keep their old value
    state["items"] = merged
    if msgs:
        send(chat, "\n\n".join(msgs))
    print("alerts sent:", len(msgs))
    save(state, before)


def save(state, before):
    if json.dumps(state, sort_keys=True, ensure_ascii=False) != before:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
