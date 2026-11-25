# -*- coding: utf-8 -*-
"""PUFF Sippii stock alert for an Android phone (Termux), on home internet.
Light: checks only the colours below, one page at a time, every 10 minutes.
Usage:  python puff_phone.py YOUR_NTFY_TOPIC"""
import json, os, re, sys, gzip, time, urllib.request

EVERY_MIN = 10      # minutes between rounds
GAP_SEC = 4         # seconds between pages
LOW = 10            # alert when a size drops to this many or fewer
SLUGS = [
    "puff-sippii-chocolate-brown-350-ml-chrome-i-00000194241-1",
    "puff-sippii-rose-gold-350-ml-chrome-i-00000194243-1",
    "puff-sippii-pink-350-ml-chrome-i-00000194240-1",
    "puff-sippii-african-violet-350-ml-matte-i-00000171480-1",
    "puff-sippii-butter-350-ml-matte-i-00000171454-1",
    "puff-sippii-purple-350-ml-matte-i-00000171482-1",
    "puff-sippii-burgandy-350-ml-matte-i-00000171486-1",
    "puff-sippii-burgandy-350-ml-rubber-i-00000171488-1",
    "puff-sippii-baby-blue-350-ml-matte-i-00000171468-1",
    "puff-sippii-baby-pink-350-ml-matte-i-00000171470-1",
    "puff-sippii-baby-pink-350-ml-shine-i-00000171472-1",
    "puff-sippii-eggplant-350-ml-matte-i-00000171484-1",
    "puff-sippii-chocolate-brown-350-ml-matte-i-00000171442-1",
    "puff-sippii-tiffany-350-ml-matte-i-00000171462-1",
    "puff-sippii-dessert-sage-350-ml-matte-i-00000171446-1",
    "puff-sippii-mykanos-blue-350-ml-matte-i-00000171464-1",
    "puff-sippii-mykanos-blue-350-ml-rubber-i-00000171466-1"
]

TOPIC = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("NTFY_TOPIC", "")).strip()
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "puff_phone_state.json")
UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


class Blocked(Exception):
    pass


def ntfy(msg, title="PUFF", click=None, prio=4):
    body = {"topic": TOPIC, "title": title, "message": msg, "priority": prio}
    if click:
        body["click"] = click
    for _ in range(3):
        try:
            urllib.request.urlopen(urllib.request.Request("https://ntfy.sh/", json.dumps(body).encode(),
                                   {"Content-Type": "application/json"}), timeout=30).read()
            return
        except Exception as e:
            print("ntfy failed:", e)
            time.sleep(5)


def mlv(v):
    d = re.sub(r"\D", "", str(v))
    return 1000 if str(v).upper().startswith("1L") else (int(d) if d else 0)


def product(slug):
    url = "https://www.boutiqaat.com/ar-sa/women/%s/p/" % slug
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "ar,en;q=0.8",
                                               "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
            if r.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise Blocked(e.code)
        raise
    m = NEXT_RE.search(raw.decode("utf-8", "replace"))
    if not m:
        raise Blocked("no data")
    r = json.loads(m.group(1))["props"]["pageProps"]["response"]
    r = r[0] if isinstance(r, list) else r
    name = r.get("name") or slug
    out = {}
    for o in r.get("multi_configurable_option") or []:
        ch = o.get("single_child_info") or {}
        u = "https://www.boutiqaat.com/ar-sa/women/" + re.sub(r"/p/?$", "", ch.get("slug") or slug) + "/p/"
        out[u] = {"name": name, "ml": mlv(o.get("value")), "qty": int(float(o.get("available_qty") or 0)),
                  "price": int(float(ch.get("final_price_with_tax") or 0)), "url": u}
    return out


def line(it):
    return "%s\nالحجم: %s مل\nالسعر: %s ر.س\nالكمية المتوفرة: %s حبة" % (it["name"], it["ml"], it["price"], it["qty"])


def main():
    if not TOPIC:
        sys.exit("اكتب اسم قناة ntfy بعد الأمر، مثال: python puff_phone.py puff-123")
    try:
        state = json.load(open(STATE))
    except Exception:
        state = {}
    first = not state
    while True:
        live = {}
        try:
            for s in SLUGS:
                try:
                    live.update(product(s))
                except Blocked:
                    raise
                except Exception as e:
                    print("skip", s, e)
                time.sleep(GAP_SEC)
        except Blocked as e:
            print("blocked:", e)
            ntfy("⛔ بوتيكات رفض الفحص من جوالك (%s)، فوقفت المراقبة عشان ما ينحظر النت عندك.\nشغّلها مرة ثانية بعدين." % e)
            sys.exit(1)
        if first:
            out = sum(1 for i in live.values() if i["qty"] == 0)
            ntfy("👋 بدأت المراقبة من الجوال.\nأراقب %d حجم/لون، منها %d غير متوفر الآن.\nالفحص كل %d دقائق." % (len(live), out, EVERY_MIN))
            first = False
        else:
            for u, it in live.items():
                old = state.get(u)
                if old is None:
                    continue
                if old == 0 and it["qty"] > 0:
                    ntfy("🟢 رجع متوفر!\n" + line(it), "🟢 رجع متوفر: %d حبة" % it["qty"], u, 5)
                elif old > LOW and 0 < it["qty"] <= LOW:
                    ntfy("🟠 قربت تخلص!\n" + line(it), "🟠 باقي %d حبة" % it["qty"], u, 4)
        state.update({u: it["qty"] for u, it in live.items()})
        json.dump(state, open(STATE, "w"))
        print(time.strftime("%H:%M"), "checked", len(live), "sizes")
        time.sleep(EVERY_MIN * 60)


if __name__ == "__main__":
    main()
