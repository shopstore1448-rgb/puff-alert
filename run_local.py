# -*- coding: utf-8 -*-
"""Runs the PUFF Sippii stock check on this computer/server, forever, every few minutes.
Sends alerts to the ntfy app. Separate from the old PUFF program; touches none of its files."""
import os, sys, time, socket, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
LOG = os.path.join(HERE, "log.txt")

# one copy only
_lock = socket.socket()
try:
    _lock.bind(("127.0.0.1", 8768))
except OSError:
    print("Already running.")
    sys.exit(0)

cfg = {}
for line in open(os.path.join(HERE, "settings.txt"), encoding="utf-8-sig"):
    if "=" in line and not line.strip().startswith("#"):
        k, v = line.split("=", 1)
        cfg[k.strip()] = v.strip()
every = max(1, int(float(cfg.get("EVERY_MINUTES", "5")))) * 60
os.environ["NTFY_TOPIC"] = cfg.get("NTFY_TOPIC", "")
os.environ["TYPES"] = cfg.get("TYPES", "sippii")
os.environ["LOW_STOCK"] = cfg.get("LOW_STOCK", "10")
os.environ["FOCUS_BELOW"] = cfg.get("FOCUS_BELOW", "200")
os.environ["LOOP_SECONDS"] = "0"   # one check per call; this file does the looping
for k in ("TELEGRAM_TOKEN", "TELEGRAM_CHAT_ID"):
    os.environ.pop(k, None)

sys.path.insert(0, HERE)
import check


def log(msg):
    line = time.strftime("%Y-%m-%d %H:%M:%S ") + msg
    print(line)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        if os.path.getsize(LOG) > 2_000_000:
            os.replace(LOG, LOG + ".old")
    except Exception:
        pass


log("started, every %d min" % (every // 60))
while True:
    t0 = time.time()
    try:
        check.main()
        log("check done")
    except Exception as e:
        log("error: %s" % e)
        traceback.print_exc()
    time.sleep(max(10, every - (time.time() - t0)))
