"""
Календарь выступлений для телефона.

1. Подписка: бот раздаёт календарь по секретной ссылке (http://IP:порт/cal/<секрет>.ics).
   Телефон (iPhone «Календарь», Google Календарь) подписывается один раз и сам подтягивает изменения.
2. Файл .ics по одной заявке — нажал в Telegram, и событие добавилось в календарь телефона.
"""
import logging
import re
import secrets
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

import config
import storage

logger = logging.getLogger(__name__)

CAL_STATUSES = ("work", "done")  # в календарь попадают заявки «в работе» и «выполнена»
_public = {"base": None}


# ---------- даты и формат iCalendar ----------

def parse_date(value):
    try:
        return datetime.strptime((value or "").strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def _esc(text) -> str:
    return (str(text or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
            .replace("\r", "").replace("\n", "\\n"))


def _fold(line: str) -> str:
    """Строки iCalendar не длиннее 75 байт: длинные переносятся с пробелом в начале."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 74:
            out.append(cur.decode("utf-8"))
            cur = b" " + b
        else:
            cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n".join(out)


def _event_lines(order, with_phone: bool, bot_username: str):
    d = order["data"]
    day = parse_date(d.get("event_date"))
    if not day:
        return []
    title = "Oasis Dance: " + (d.get("event_type") or "выступление")
    if d.get("contact_name"):
        title += " — " + d["contact_name"]
    desc = [f"Заявка №{order['id']}"]
    for key, name in (("program", "Программа"), ("wish", "Номер"), ("comment", "Пожелания")):
        if d.get(key) and d[key] != "—":
            desc.append(f"{name}: {d[key]}")
    if with_phone and d.get("contact_phone"):
        desc.append(f"Телефон: {d['contact_phone']}")
    if bot_username:
        desc.append(f"Открыть в боте: https://t.me/{bot_username}?start=o_{order['id']}")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VEVENT",
             f"UID:oasis-order-{order['id']}@{bot_username or 'oasisdance'}",
             f"DTSTAMP:{stamp}",
             f"DTSTART;VALUE=DATE:{day:%Y%m%d}",
             f"DTEND;VALUE=DATE:{day + timedelta(days=1):%Y%m%d}",
             f"SUMMARY:{_esc(title)}",
             f"DESCRIPTION:{_esc(chr(10).join(desc))}",
             f"STATUS:{'CONFIRMED' if order['status'] in CAL_STATUSES else 'TENTATIVE'}",
             "TRANSP:OPAQUE"]
    if d.get("event_location"):
        lines.append(f"LOCATION:{_esc(d['event_location'])}")
    lines += ["BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY",
              f"DESCRIPTION:{_esc('Завтра: ' + title)}", "END:VALARM", "END:VEVENT"]
    return lines


def build_ics(orders, name="Oasis Dance — выступления", with_phone=False) -> bytes:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Oasis Dance//Bot//RU", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(name)}", "X-WR-TIMEZONE:Europe/Moscow",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for o in orders:
        lines += _event_lines(o, with_phone, config.BOT_USERNAME)
    lines.append("END:VCALENDAR")
    return ("\r\n".join(_fold(l) for l in lines) + "\r\n").encode("utf-8")


def calendar_orders():
    rows = storage.list_orders(list(CAL_STATUSES), limit=1000)
    return [o for o in rows if o["kind"] == "form" and parse_date(o["data"].get("event_date"))]


def upcoming(limit=15):
    today = date.today()
    items = [(parse_date(o["data"]["event_date"]), o) for o in calendar_orders()]
    items = sorted([x for x in items if x[0] >= today], key=lambda x: x[0])
    return items[:limit]


# ---------- ссылка для подписки ----------

def secret() -> str:
    s = storage.get_setting("calendar_secret")
    if not s:
        s = secrets.token_urlsafe(18)
        storage.set_setting("calendar_secret", s)
    return s


def reset_secret() -> str:
    storage.set_setting("calendar_secret", secrets.token_urlsafe(18))
    return secret()


def public_base() -> str:
    if config.PUBLIC_URL:
        return config.PUBLIC_URL.rstrip("/")
    if not _public["base"]:
        ip = None
        for url in ("https://api.ipify.org", "https://ifconfig.me/ip"):
            try:
                ip = requests.get(url, timeout=5).text.strip()
                if re.fullmatch(r"[\d.]+", ip):
                    break
            except Exception:
                continue
        _public["base"] = f"http://{ip}:{config.CALENDAR_PORT}" if ip else ""
    return _public["base"]


def subscribe_url() -> str:
    base = public_base()
    return f"{base}/cal/{secret()}.ics" if base else ""


# ---------- маленький веб-сервер, отдающий календарь ----------

class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = self.path.split("?")[0]
        if path == f"/cal/{secret()}.ics":
            body = build_ics(calendar_orders())
            self.send_response(200)
            self.send_header("Content-Type", "text/calendar; charset=utf-8")
            self.send_header("Content-Disposition", 'inline; filename="oasis-dance.ics"')
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            storage.set_setting("calendar_last_fetch", str(int(time.time())))
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, *args):  # не засоряем журнал (в адресе — секрет)
        pass


def start_server():
    if not config.CALENDAR_PORT:
        return
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", config.CALENDAR_PORT), _Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        logger.info("Календарь для телефона раздаётся на порту %s", config.CALENDAR_PORT)
    except Exception as e:
        logger.error("Не удалось запустить раздачу календаря на порту %s: %s", config.CALENDAR_PORT, e)
