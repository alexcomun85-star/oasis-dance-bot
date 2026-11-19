"""
Календарь выступлений для телефона.

1. Подписка: бот раздаёт календарь по секретной ссылке (http://IP:порт/cal/<секрет>.ics).
   Телефон (iPhone «Календарь», Google Календарь) подписывается один раз и сам подтягивает изменения.
2. Файл .ics по одной заявке — нажал в Telegram, и событие добавилось в календарь телефона.
"""
import logging
import re
from urllib.parse import urlencode
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


MSK = timezone(timedelta(hours=3))
EVENT_HOURS = 1  # длительность события, если известно время начала


def parse_time(value):
    m = re.fullmatch(r"\s*(\d{1,2})[:.](\d{2})\s*", value or "")
    if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
        return int(m.group(1)), int(m.group(2))
    return None


def event_span(d: dict):
    """(начало, конец, весь_день) в UTC; None — если даты нет."""
    day = parse_date(d.get("event_date"))
    if not day:
        return None
    t = parse_time(d.get("event_time"))
    if not t:
        return day, day + timedelta(days=1), True
    start = datetime(day.year, day.month, day.day, t[0], t[1], tzinfo=MSK).astimezone(timezone.utc)
    return start, start + timedelta(hours=EVENT_HOURS), False


def event_title(d: dict, for_client=False) -> str:
    if for_client:
        return "Шоу-балет Oasis Dance — " + (d.get("event_type") or "выступление")
    title = "Oasis Dance: " + (d.get("event_type") or "выступление")
    if d.get("contact_name"):
        title += " — " + d["contact_name"]
    return title


def event_details(order, for_client=False, bot_username="", with_phone=True) -> str:
    d = order["data"]
    lines = [f"Заявка №{order['id']}"]
    if for_client:
        lines.append(f"Связь с менеджером: @{config.MANAGER_USERNAME} — https://t.me/{config.MANAGER_USERNAME}")
    else:
        u = order.get("user") or {}
        if d.get("contact_name"):
            lines.append(f"Клиент: {d['contact_name']}")
        if with_phone and d.get("contact_phone"):
            lines.append(f"Телефон: {d['contact_phone']}")
        if u.get("username"):
            lines.append(f"Telegram: https://t.me/{u['username']}")
    for key, name in (("program", "Программа"), ("wish", "Номер"), ("comment", "Пожелания")):
        if d.get(key) and d[key] != "—":
            lines.append(f"{name}: {d[key]}")
    if d.get("event_geo"):
        lat, lon = d["event_geo"].split(",")
        lines.append(f"Карта: https://yandex.ru/maps/?pt={lon},{lat}&z=16&l=map")
    if bot_username and not for_client:
        lines.append(f"Открыть в боте: https://t.me/{bot_username}?start=o_{order['id']}")
    return "\n".join(lines)


def google_url(order, for_client=False) -> str:
    """Ссылка «Добавить в Google Календарь» — открывает готовое событие, остаётся нажать «Сохранить»."""
    span = event_span(order["data"])
    if not span:
        return ""
    start, end, all_day = span
    fmt = "%Y%m%d" if all_day else "%Y%m%dT%H%M%SZ"
    params = {"action": "TEMPLATE", "text": event_title(order["data"], for_client),
              "dates": f"{start.strftime(fmt)}/{end.strftime(fmt)}",
              "details": event_details(order, for_client, config.ADMIN_BOT_USERNAME or config.BOT_USERNAME)[:900],
              "ctz": "Europe/Moscow"}
    if order["data"].get("event_location"):
        params["location"] = order["data"]["event_location"]
    return "https://calendar.google.com/calendar/render?" + urlencode(params)


def calendar_buttons(order, for_client=False):
    """Кнопки «в календарь»: Google — ссылкой, iPhone и другие — файлом .ics."""
    url = google_url(order, for_client)
    if not url:
        return []
    ics_cb = f"cal_my_{order['id']}" if for_client else f"adm_ics_{order['id']}"
    return [[{"text": "📅 Google Календарь", "url": url},
             {"text": "🍏 Календарь iPhone", "callback_data": ics_cb}]]


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


def _event_lines(order, with_phone: bool, bot_username: str, for_client=False):
    d = order["data"]
    span = event_span(d)
    if not span:
        return []
    start, end, all_day = span
    title = event_title(d, for_client)
    desc = event_details(order, for_client, bot_username, with_phone)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if all_day:
        when = [f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}"]
    else:
        when = [f"DTSTART:{start:%Y%m%dT%H%M%SZ}", f"DTEND:{end:%Y%m%dT%H%M%SZ}"]
    lines = ["BEGIN:VEVENT",
             f"UID:oasis-order-{order['id']}@{bot_username or 'oasisdance'}",
             f"DTSTAMP:{stamp}",
             *when,
             f"SUMMARY:{_esc(title)}",
             f"DESCRIPTION:{_esc(desc)}",
             f"STATUS:{'CONFIRMED' if order['status'] in CAL_STATUSES else 'TENTATIVE'}",
             "TRANSP:OPAQUE"]
    if d.get("event_location"):
        lines.append(f"LOCATION:{_esc(d['event_location'])}")
    if d.get("event_geo"):
        lines.append("GEO:" + d["event_geo"].replace(",", ";"))
    lines += ["BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY",
              f"DESCRIPTION:{_esc('Завтра: ' + title)}", "END:VALARM", "END:VEVENT"]
    return lines


def build_ics(orders, name="Oasis Dance — выступления", with_phone=False, for_client=False) -> bytes:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Oasis Dance//Bot//RU", "CALSCALE:GREGORIAN",
             "METHOD:PUBLISH", f"X-WR-CALNAME:{_esc(name)}", "X-WR-TIMEZONE:Europe/Moscow",
             "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for o in orders:
        lines += _event_lines(o, with_phone, config.ADMIN_BOT_USERNAME or config.BOT_USERNAME, for_client)
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
