# -*- coding: utf-8 -*-
"""PUFF restock alert: checks boutiqaat PUFF stock and sends a Telegram message
when a colour/size goes from out of stock (0) to in stock. Runs on GitHub Actions.
Standalone copy of the scraping logic; it does not touch any other program."""
import base64
import json, os, re, sys, gzip, time
import urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")
BASE = "https://www.boutiqaat.com"
BOUTIQAAT_HOSTS = {"www.boutiqaat.com", "boutiqaat.com"}
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36")
NEXT_RE = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
EX = re.compile(r"ماصة|غطاء|قاعدة|حامل|دفتر|قالب|shaker|شيكر|ستيكر|sticker|stickii|matcha-set|تحضير الماتشا|\blid\b|straw(?!berry)|handle|boot|journal|ice-?tray|glass|phone|puff-jr|flippii|accessor|bottle-lid", re.I)
RULES = [("gosippii", "go-sippii"), ("grippii", "grippii"), ("koffii", "koffi|coffee-bottle"),
         ("sipping", "sipping"), ("loopii", "loopii"), ("hanger", "hanger"),
         ("widemouth", "widemouth|puff-wide-"), ("smoothie", "mega-bottle"),
         ("cups", "cup-set|puff-cup|cups-set"), ("sippii", "sippii"), ("bottll", "bottle")]
# Which PUFF types to watch (comma separated keys from RULES). Default: Sippii only. "all" = everything.
TYPES = [t.strip() for t in (os.environ.get("TYPES") or "sippii").split(",") if t.strip()]
LIST_PATH = "/ar-sa/women/puff/br/cid/480/?page=%d"

TOKEN = os.environ.get("TELEGRAM_TOKEN", "").strip()
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
# Optional: only alert for names containing one of these words (comma separated)
NTFY_TOPIC = os.environ.get("NTFY_TOPIC", "").strip()
LOW = int(os.environ.get("LOW_STOCK") or 10)  # alert when a size drops to this many or fewer
WATCH = [w.strip() for w in os.environ.get("WATCH", "").split(",") if w.strip()]


class BoutiqaatProxyHandler(urllib.request.HTTPSHandler):
    def __init__(self, address, auth):
        super().__init__()
        self.address = address
        self.auth = auth

    def https_open(self, req):
        # Set the CONNECT tunnel explicitly so NO_PROXY cannot bypass it.
        req.set_proxy(self.address, "http")
        if self.auth:
            req.add_unredirected_header("Proxy-Authorization", self.auth)
        return super().https_open(req)


class BoutiqaatRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urllib.parse.urlsplit(newurl)
        if (
            destination.scheme != "https"
            or destination.hostname not in BOUTIQAAT_HOSTS
            or destination.port not in (None, 443)
            or destination.username is not None
        ):
            raise RuntimeError("Boutiqaat redirected outside its HTTPS product site")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@lru_cache(maxsize=1)
def boutiqaat_opener():
    value = os.environ.get("BOUTIQAAT_PROXY_URL", "").strip()
    if not value:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            raise SystemExit("Set the BOUTIQAAT_PROXY_URL secret before running the check.")
        return None
    try:
        proxy = urllib.parse.urlsplit(value)
        if (
            proxy.scheme != "http"
            or not proxy.hostname
            or any(ch.isspace() or ord(ch) < 32 for ch in value)
            or proxy.path not in ("", "/")
            or proxy.query
            or proxy.fragment
            or proxy.port == 0
            or (proxy.username is not None and (not proxy.username or proxy.password is None))
        ):
            raise ValueError
    except ValueError:
        raise SystemExit("BOUTIQAAT_PROXY_URL must be a valid HTTP proxy URL.") from None
    address = proxy.netloc.rsplit("@", 1)[-1]
    auth = None
    if proxy.username is not None:
        user = urllib.parse.unquote(proxy.username)
        password = urllib.parse.unquote(proxy.password)
        encoded = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        auth = "Basic " + encoded
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        BoutiqaatProxyHandler(address, auth),
        BoutiqaatRedirectHandler(),
    )


def fetch(path, tries=3):
    url = BASE + path
    opener = boutiqaat_opener()
    open_url = opener.open if opener is not None else urllib.request.urlopen
    last = None
    for a in range(tries):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "ar,en;q=0.8", "Accept-Encoding": "gzip", "Cache-Control": "no-cache"})
            with open_url(req, timeout=60) as r:
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


def typ(slug):
    for k, rule in RULES:
        if re.search(rule, slug):
            return k


def watched_type(slug):
    return "all" in TYPES or typ(slug) in TYPES


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
        url = "https://www.boutiqaat.com/ar-sa/women/" + re.sub(r"/p/?$", "", ch.get("slug") or slug) + "/p/"
        items[url] = {"name": name, "slug": slug, "ml": mlv(o.get("value")),
                      "qty": int(float(o.get("available_qty") or 0)),
                      "price": int(float(ch.get("final_price_with_tax") or 0)), "url": url}
    if not items:
        url = "https://www.boutiqaat.com/ar-sa/women/" + slug + "/p/"
        items[url] = {"name": name, "slug": slug, "ml": 0, "qty": int(float(r.get("qty_available") or 0)),
                      "price": int(float(r.get("final_price_with_tax") or 0)), "url": url}
    return items


def scrape(slugs=None):
    if slugs:
        return scrape_products(slugs), slugs
    names = {}
    with ThreadPoolExecutor(4) as ex:
        for res in ex.map(lambda p: _safe(list_page, p), range(1, 31)):
            if isinstance(res, dict):
                names.update(res)
    slugs = [s for s in names if not EX.search(s) and not EX.search(names[s])]
    if len(slugs) < 150:
        raise RuntimeError("only %d products found (site blocked or changed?)" % len(slugs))
    slugs = [s for s in slugs if watched_type(s)]
    if WATCH:  # only the colours the user cares about: far fewer pages per check
        slugs = [s for s in slugs if any(w in names[s] for w in WATCH)]
    if not slugs:
        raise RuntimeError("no products of type %s found" % TYPES)
    live = scrape_products(slugs)
    focus = int(os.environ.get("FOCUS_BELOW") or 0)
    if focus:  # from now on only re-check colours that are sold out or running low (< focus)
        slugs = sorted({v["slug"] for v in live.values() if v["qty"] < focus})
        print("focusing on %d colours with a size under %d" % (len(slugs), focus))
    return live, slugs


def scrape_products(slugs):
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


def tg(method, data=None, tries=3):
    url = "https://api.telegram.org/bot%s/%s" % (TOKEN, method)
    body = urllib.parse.urlencode(data).encode() if data else None
    for a in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=body), timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception as e:
            if a == tries - 1:
                raise
            wait = 5 * (a + 1)
            if getattr(e, "code", None) == 429:
                try:
                    wait = int(json.loads(e.read().decode())["parameters"]["retry_after"]) + 1
                except Exception:
                    pass
            print("telegram retry in %ds: %s" % (wait, e))
            time.sleep(min(wait, 40))


def find_chat_id(state):
    if CHAT_ID:
        return CHAT_ID
    if not TOKEN or "--test" not in sys.argv:
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


def ntfy(text, title="PUFF", click=""):
    msg = {"topic": NTFY_TOPIC, "title": title, "message": text, "priority": 4, "tags": ["bell"]}
    if click:
        msg["click"] = click
    for a in range(3):
        try:
            req = urllib.request.Request("https://ntfy.sh/", data=json.dumps(msg).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
            return True
        except Exception as e:
            print("ntfy retry:", e)
            time.sleep(5 * (a + 1))
    return False


def send(chat, text, items=None):
    """Send to every channel that is set up. items: list of (text, url) for one ntfy push each."""
    ok = False
    if NTFY_TOPIC:
        for t, url, title in (items or [(text, "", "PUFF")]):
            ok = ntfy(t, title, url) or ok
    if TOKEN and chat:
        chunks, cur = [], ""
        for block in text.split("\n\n"):
            if len(cur) + len(block) > 3800:
                chunks.append(cur)
                cur = ""
            cur += block + "\n\n"
        chunks.append(cur)
        try:
            for c in chunks:
                if c.strip():
                    tg("sendMessage", {"chat_id": chat, "text": c.strip(), "disable_web_page_preview": "true"})
            ok = True
        except Exception as e:
            print("telegram failed:", e)
    if not ok:
        print("(no channel delivered) message was:\n" + text)
        if NTFY_TOPIC or TOKEN:
            raise RuntimeError("could not deliver the alert")


def fmt(it, tag):
    size = ("الحجم: %d مل\n" % it["ml"]) if it["ml"] else ""
    return "%s%s\n%sالسعر: %d ر.س\nالكمية المتوفرة: %d حبة\n%s" % (
        tag, it["name"], size, it["price"], it["qty"], it["url"])


def title(it, word):
    return "%s %d حبة" % (word, it["qty"])


def wanted(it):
    return not WATCH or any(w in it["name"] for w in WATCH)


def main():
    boutiqaat_opener()
    state = {}
    if os.path.exists(STATE):
        state = json.load(open(STATE, encoding="utf-8"))
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

    if not (NTFY_TOPIC or (TOKEN and chat)):
        print("No alert channel set up yet (NTFY_TOPIC or TELEGRAM_TOKEN + TELEGRAM_CHAT_ID). Skipping.")
        return
    loop = int(os.environ.get("LOOP_SECONDS") or 0)   # keep checking inside one run
    every = int(os.environ.get("EVERY_SECONDS") or 60)
    start, first = time.time(), True
    # Be gentle with the site: reuse the product list for an hour, and pause after a refusal.
    if time.time() < state.get("pause_until", 0):
        print("paused after a refusal until", time.strftime("%H:%M", time.gmtime(state["pause_until"])), "UTC")
        return
    fresh = time.time() - state.get("slugs_at", 0) < 6 * 3600 and state.get("watch") == [WATCH, os.environ.get("FOCUS_BELOW") or ""]
    slugs = state.get("slugs") if fresh else None
    while True:
        t0 = time.time()
        try:
            live, slugs = scrape(slugs)
        except RuntimeError as e:
            # Boutiqaat sometimes refuses one GitHub server (403). Don't fail the run for that
            # (no failure emails); only warn on the phone if it lasts about 30 minutes.
            print("blocked/failed:", e)
            state["blocked"] = state.get("blocked", 0) + 1
            state["pause_until"] = int(time.time()) + 60 * min(60, 15 * state["blocked"])
            if state["blocked"] == 2:
                send(chat, "⚠️ بوتيكات يرفض الفحص حالياً. بأخفف المحاولات وأرجع أجرّب لحالي.")
            save(state, before)
            return
        if state.get("blocked", 0) >= 2:
            send(chat, "✅ رجع الفحص يشتغل عادي.")
        state["blocked"] = 0
        if slugs != state.get("slugs"):
            state["slugs"], state["slugs_at"], state["watch"] = slugs, int(time.time()), [WATCH, os.environ.get("FOCUS_BELOW") or ""]
        check_once(state, live, chat, first)
        first = False
        save(state, before)
        before = json.dumps(state, sort_keys=True, ensure_ascii=False)
        if time.time() - start + every > loop:
            break
        time.sleep(max(0, every - (time.time() - t0)))


def check_once(state, live, chat, first):
    old = state.get("items", {})
    import datetime
    snap = {"time": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "items": sorted(live.values(), key=lambda v: (v["name"], v["ml"]))}
    with open(os.path.join(HERE, "snapshot.json"), "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=1)
    # 0 = out of stock, 1 = low (1..LOW), 2 = plenty
    new = {k: (0 if v["qty"] <= 0 else 1 if v["qty"] <= LOW else 2) for k, v in live.items()}
    msgs, items = [], []
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
                items.append((msgs[-1], it["url"], title(it, "🟢 رجع متوفر:")))
            elif k not in old:
                msgs.append(fmt(it, "🆕 منتج جديد متوفر!\n"))
                items.append((msgs[-1], it["url"], title(it, "🆕 جديد:")))
            elif new[k] == 1 and old.get(k) != 1:
                msgs.append(fmt(it, "⚠️ قرب يخلص! باقي %d حبة بس\n" % it["qty"]))
                items.append((msgs[-1], it["url"], "⚠️ باقي %d حبة بس" % it["qty"]))
    merged = {k: v for k, v in old.items() if watched_type(k)}
    merged.update(new)  # sizes not read this time keep their old value
    if msgs:
        send(chat, "\n\n".join(msgs), items or None)
    state["items"] = merged
    print(time.strftime("%H:%M:%S"), "sizes: %d, alerts sent: %d" % (len(live), len(msgs)))


def save(state, before):
    if json.dumps(state, sort_keys=True, ensure_ascii=False) != before:
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=0, sort_keys=True)


if __name__ == "__main__":
    main()
