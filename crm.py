"""
Удобства для менеджера в духе CRM: скорость ответа, шаблоны, заметки, сумма, напоминания, поиск,
история клиента и дружелюбный «рабочий стол».
"""
import html
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

import config
import storage
import tg

logger = logging.getLogger(__name__)
MSK = timezone(timedelta(hours=3))

SLA_FIRST_MIN = 10   # первое напоминание о неотвеченной заявке
SLA_SECOND_MIN = 60  # второе
QUIET_HOURS = (23, 9)  # ночью не будим: такие заявки попадут в утреннюю сводку

TEMPLATES = [
    ("👋 Перезвоню", "Здравствуйте! Спасибо за заявку 💛 Я менеджер шоу-балета Oasis Dance — перезвоню вам "
                    "в течение 15 минут, чтобы обсудить детали."),
    ("📅 Дата свободна", "Хорошая новость: на вашу дату мы свободны! Давайте обсудим программу и детали — "
                        "когда вам удобно созвониться?"),
    ("📞 Не дозвонился", "Не получилось до вас дозвониться. Напишите, пожалуйста, когда вам удобно поговорить, "
                        "— перезвоню в это время."),
    ("💰 Отправлю цену", "Спасибо за интерес! Подготовлю стоимость и варианты программы под ваше мероприятие "
                        "и пришлю сюда в ближайшее время."),
]

_input = {}  # admin_id -> ("note" | "amount", order_id)
_lock = threading.Lock()


# ---------- данные заявки ----------

def _save_data(oid, data):
    storage._q("UPDATE orders SET data=?, updated=? WHERE id=?",
               (json.dumps(data, ensure_ascii=False), time.time(), oid), commit=True)


def mark_taken(oid):
    """Запоминаем, когда заявку впервые взяли в работу, — для статистики скорости ответа."""
    order = storage.get_order(oid)
    if order and not order["data"].get("taken_at"):
        order["data"]["taken_at"] = time.time()
        _save_data(oid, order["data"])


def _ago(ts) -> str:
    minutes = int((time.time() - ts) // 60)
    if minutes < 1:
        return "только что"
    if minutes < 60:
        return f"{minutes} мин"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} ч {minutes % 60} мин"
    return f"{hours // 24} дн"


def client_history(order) -> int:
    r = storage._q("SELECT COUNT(*) c FROM orders WHERE chat_id=? AND id<>? AND status<>'merged'",
                   (order["chat_id"], order["id"]), one=True)
    return r["c"] if r else 0


# ---------- дополнения к карточке ----------

def card_lines(order):
    d = order["data"]
    lines = []
    if order["status"] == "new" and order["kind"] != "abandoned":
        lines.append(f"⏱ <b>Ждёт ответа: {_ago(order['created'])}</b>")
    n = client_history(order)
    if n:
        lines.append(f"🔁 Клиент обращался раньше: {n}")
    if d.get("amount"):
        lines.append(f"💰 Сумма: <b>{html.escape(str(d['amount']))}</b>")
    notes = d.get("notes") or []
    if notes:
        lines.append("📝 <b>Заметки:</b>")
        for nt in notes[-3:]:
            when = datetime.fromtimestamp(nt["ts"], MSK).strftime("%d.%m %H:%M")
            lines.append(f"  • {when} — {html.escape(nt['text'])}")
    rem = storage._q("SELECT due FROM reminders WHERE order_id=? AND done=0 ORDER BY due LIMIT 1",
                     (order["id"],), one=True)
    if rem:
        lines.append(f"⏰ Напоминание: {datetime.fromtimestamp(rem['due'], MSK):%d.%m %H:%M}")
    return lines


def card_rows(order):
    oid = order["id"]
    return [[{"text": "📝 Заметка", "callback_data": f"crm_note_{oid}"},
             {"text": "💰 Сумма", "callback_data": f"crm_sum_{oid}"},
             {"text": "⏰ Напомнить", "callback_data": f"crm_rem_{oid}"}]]


# ---------- шаблоны ответов ----------

def templates_kb(oid):
    rows = [[{"text": title, "callback_data": f"crm_tpl_{oid}_{i}"}] for i, (title, _) in enumerate(TEMPLATES)]
    return {"inline_keyboard": rows}


def send_template(admin_id, oid, i):
    import admin
    try:
        text = TEMPLATES[i][1]
    except IndexError:
        return
    with admin._lock:
        admin._reply_to[admin_id] = oid
    admin.send_to_client(admin_id, text)


# ---------- ввод заметки и суммы ----------

def start_input(admin_id, kind, oid):
    with _lock:
        _input[admin_id] = (kind, oid)
    prompt = ("📝 Напишите заметку к заявке №{oid} — её увидят все менеджеры (клиенту она не уходит)."
              if kind == "note" else
              "💰 Напишите сумму по заявке №{oid}, например «45 000» или «45000 ₽, предоплата 10 000».")
    tg.send_message(admin_id, prompt.format(oid=oid) + "\nОтмена — /cancel")


def awaiting(admin_id):
    with _lock:
        return _input.get(admin_id)


def cancel(admin_id):
    with _lock:
        return _input.pop(admin_id, None) is not None


def process_input(admin_id, text):
    import admin
    with _lock:
        kind, oid = _input.pop(admin_id, (None, None))
    order = storage.get_order(oid) if oid else None
    if not order:
        tg.send_message(admin_id, "Заявка не найдена.")
        return
    d = order["data"]
    if kind == "note":
        d.setdefault("notes", []).append({"ts": time.time(), "text": text[:500]})
        done = "📝 Заметка сохранена."
    else:
        d["amount"] = text[:100]
        done = "💰 Сумма сохранена."
    _save_data(oid, d)
    admin._refresh_cards(oid)
    order = storage.get_order(oid)
    tg.send_message(admin_id, done + "\n\n" + admin.card_text(order), admin.card_kb(order, "work"))


def parse_amount(value) -> float:
    m = re.search(r"\d[\d\s]*", str(value or ""))
    return float(re.sub(r"\s", "", m.group(0))) if m else 0.0


# ---------- напоминания ----------

def reminder_options(oid):
    return {"inline_keyboard": [
        [{"text": "через 1 час", "callback_data": f"crm_remset_{oid}_h1"},
         {"text": "через 3 часа", "callback_data": f"crm_remset_{oid}_h3"}],
        [{"text": "завтра в 10:00", "callback_data": f"crm_remset_{oid}_t10"},
         {"text": "через 3 дня", "callback_data": f"crm_remset_{oid}_d3"}],
        [{"text": "⬅️ К заявке", "callback_data": f"adm_open_{oid}_work"}]]}


def set_reminder(admin_id, oid, code):
    now = datetime.now(MSK)
    if code == "h1":
        due = now + timedelta(hours=1)
    elif code == "h3":
        due = now + timedelta(hours=3)
    elif code == "t10":
        due = (now + timedelta(days=1)).replace(hour=10, minute=0, second=0, microsecond=0)
    else:
        due = (now + timedelta(days=3)).replace(hour=10, minute=0, second=0, microsecond=0)
    storage._q("INSERT INTO reminders(order_id, admin_id, due, done) VALUES(?,?,?,0)",
               (oid, admin_id, due.timestamp()), commit=True)
    return f"⏰ Напомню {due:%d.%m в %H:%M} про заявку №{oid}."


def check_reminders():
    import admin
    rows = storage._q("SELECT rowid, * FROM reminders WHERE done=0 AND due<=?", (time.time(),))
    for r in rows:
        storage._q("UPDATE reminders SET done=1 WHERE rowid=?", (r["rowid"],), commit=True)
        order = storage.get_order(r["order_id"])
        if not order:
            continue
        with tg.via("admin"):
            tg.send_message(r["admin_id"], "⏰ <b>Напоминание</b>\n\n" + admin.card_text(order),
                            admin.card_kb(order, "work"))


# ---------- скорость ответа: заявка без ответа ----------

def _quiet_now() -> bool:
    h = datetime.now(MSK).hour
    return h >= QUIET_HOURS[0] or h < QUIET_HOURS[1]


def check_sla():
    import admin
    now = time.time()
    for o in storage.list_orders(["new"], limit=50):
        if o["kind"] == "abandoned":
            continue
        age = (now - o["created"]) / 60
        d = o["data"]
        stage = None
        if age >= SLA_SECOND_MIN and not d.get("sla2"):
            stage = "sla2"
        elif age >= SLA_FIRST_MIN and not d.get("sla1"):
            stage = "sla1"
        if not stage:
            continue
        d["sla1"] = True
        if stage == "sla2":
            d["sla2"] = True
        _save_data(o["id"], d)
        if _quiet_now():
            continue  # ночью не тревожим — заявка будет в утренней сводке
        tip = ("Клиенты чаще выбирают тех, кто ответил первым 🙂" if stage == "sla1"
               else "Прошёл уже час — возможно, клиент сравнивает с другими коллективами.")
        with tg.via("admin"):
            for admin_id in config.ADMIN_CHAT_IDS:
                tg.send_message(admin_id, f"⏰ Заявка №{o['id']} ждёт ответа {_ago(o['created'])}. {tip}",
                                {"inline_keyboard": [
                                    [{"text": "📄 Открыть", "callback_data": f"adm_open_{o['id']}_new"},
                                     {"text": "💬 Ответить шаблоном", "callback_data": f"adm_msg_{o['id']}"}]]})


# ---------- поиск ----------

def search(query: str):
    q = query.strip().lower().lstrip("№#")
    digits = re.sub(r"\D", "", q)
    rows = storage._q("SELECT * FROM orders WHERE status<>'merged' ORDER BY id DESC LIMIT 1000")
    found = []
    for r in rows:
        o = storage._order(r)
        d, u = o["data"], o["user"] or {}
        hay = " ".join(str(x) for x in (d.get("contact_name"), d.get("event_type"), d.get("event_location"),
                                         d.get("wish"), d.get("comment"), u.get("username"),
                                         u.get("first_name"), u.get("last_name")) if x).lower()
        phone = re.sub(r"\D", "", str(d.get("contact_phone", "")))
        if (q.isdigit() and int(q) == o["id"]) or (len(digits) >= 4 and digits in phone) \
                or (len(q) >= 2 and not q.isdigit() and q in hay):
            found.append(o)
        if len(found) >= 8:
            break
    return found


def search_screen(query: str):
    import admin
    found = search(query)
    if not found:
        return (f"🔎 По запросу «{html.escape(query[:40])}» ничего не нашёл.\n\n"
                "Ищите по имени клиента, телефону (от 4 цифр), номеру заявки, месту или типу мероприятия.\n"
                "Кнопки меню — внизу экрана 👇", None)
    rows = [[{"text": admin._short(o) + f" · {admin.STATUS.get(o['status'], '')[:2]}",
              "callback_data": f"adm_open_{o['id']}_work"}] for o in found]
    return f"🔎 Нашёл: {len(found)}", {"inline_keyboard": rows}


# ---------- рабочий стол менеджера ----------

def greeting() -> str:
    h = datetime.now(MSK).hour
    if 5 <= h < 12:
        return "☀️ Доброе утро"
    if 12 <= h < 18:
        return "🌤 Добрый день"
    if 18 <= h < 23:
        return "🌆 Добрый вечер"
    return "🌙 Доброй ночи"


def _day_line(label, day):
    import calendar_ics
    items = [(d, o) for d, o in calendar_ics.upcoming(50) if d == day]
    if not items:
        return f"{label}: выступлений нет"
    parts = []
    for _, o in items:
        d = o["data"]
        t = d.get("event_time") if calendar_ics.parse_time(d.get("event_time")) else ""
        parts.append(" ".join(x for x in (t, d.get("event_type") or "выступление",
                                          "— " + d["event_location"][:30] if d.get("event_location") else "") if x))
    return f"{label}: " + "; ".join(html.escape(p) for p in parts)


def home(user):
    today = datetime.now(MSK).date()
    new = storage.list_orders(["new"], limit=50)
    new = [o for o in new if o["kind"] != "abandoned"]
    n_work = storage.count_orders(["work"])
    name = html.escape(user.get("first_name") or "")
    lines = [f"{greeting()}{', ' + name if name else ''}!\n",
             "📅 " + _day_line("Сегодня", today),
             "📅 " + _day_line("Завтра", today + timedelta(days=1)), ""]
    if new:
        oldest = min(o["created"] for o in new)
        lines.append(f"🆕 Ждут ответа: <b>{len(new)}</b> (самая давняя — {_ago(oldest)})")
    else:
        lines.append("🆕 Новых заявок нет — всё обработано 👌")
    lines.append(f"🔄 В работе: <b>{n_work}</b>")
    lines.append("\n🔎 Чтобы найти заявку, просто напишите имя, телефон или номер.")
    rows = []
    if new:
        rows.append([{"text": f"🆕 Ответить на новые ({len(new)})", "callback_data": "adm_list_new_0"}])
    rows.append([{"text": "🔄 В работе", "callback_data": "adm_list_work_0"},
                 {"text": "📁 Закрытые", "callback_data": "adm_list_closed_0"}])
    rows.append([{"text": "📅 Календарь", "callback_data": "adm_cal"},
                 {"text": "📊 Статистика", "callback_data": "adm_stats"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def stats_lines():
    month = time.time() - 30 * 86400
    rows = storage._q("SELECT * FROM orders WHERE created>=? AND status<>'merged'", (month,))
    waits, total = [], 0.0
    for r in rows:
        o = storage._order(r)
        if o["data"].get("taken_at"):
            waits.append((o["data"]["taken_at"] - o["created"]) / 60)
        if o["status"] in ("work", "done"):
            total += parse_amount(o["data"].get("amount"))
    lines = []
    if waits:
        avg = sum(waits) / len(waits)
        fast = sum(1 for w in waits if w <= 5)
        lines.append(f"⏱ Средняя скорость ответа: <b>{avg:.0f} мин</b> (за 5 минут — {fast} из {len(waits)})")
    if total:
        lines.append(f"💰 Сумма по заявкам в работе и выполненным: <b>{total:,.0f} ₽</b>".replace(",", " "))
    return lines
