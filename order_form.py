"""
Пошаговая форма заказа выступления.
Незавершённые формы хранятся в data/order_forms.json, готовые заявки — в data/orders.jsonl.
"""
import html
import json
import logging
import os
import threading
from datetime import datetime

import config
import tg

logger = logging.getLogger(__name__)

FORMS_FILE = os.path.join(config.DATA_DIR, "order_forms.json")
ORDERS_FILE = os.path.join(config.DATA_DIR, "orders.jsonl")
_lock = threading.Lock()

FIELDS = [
    ("event_type", "Тип мероприятия",
     "Выберите тип мероприятия или напишите свой вариант."),
    ("event_date", "Дата мероприятия",
     "Укажите дату мероприятия в формате ДД.ММ.ГГГГ (например: 15.06.2027)."),
    ("event_time", "Время начала",
     "Укажите время начала в формате ЧЧ:ММ (например: 18:00)."),
    ("event_location", "Место проведения",
     "Укажите город и адрес или название площадки."),
    ("guest_count", "Количество гостей",
     "Укажите примерное количество гостей (числом)."),
    ("program_type", "Программа",
     "Выберите программу или опишите свои пожелания."),
    ("contact_name", "Ваше имя",
     "Как к вам обращаться?"),
    ("contact_phone", "Телефон",
     "Укажите контактный телефон."),
    ("additional_info", "Дополнительно",
     "Особые пожелания (или напишите «нет»)."),
]

OPTIONS = {
    "event_type": ["Свадьба", "Корпоратив", "День рождения", "Юбилей", "Выпускной",
                   "Открытие", "Презентация", "Вечеринка", "Другое"],
    "program_type": ["Стандартная (2–3 номера)", "Расширенная (4–5 номеров)",
                     "Индивидуальная программа", "Интерактивное шоу", "Мастер-класс"],
}


# ---------- хранение ----------

def _load() -> dict:
    try:
        with open(FORMS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        logger.error("Не удалось прочитать формы: %s", e)
        return {}


_forms = _load()


def _save():
    with _lock:
        tmp = FORMS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_forms, f, ensure_ascii=False, indent=1)
        os.replace(tmp, FORMS_FILE)


def _get(chat_id):
    return _forms.get(str(chat_id))


def _delete(chat_id):
    if _forms.pop(str(chat_id), None) is not None:
        _save()


def is_filling(chat_id) -> bool:
    f = _get(chat_id)
    return bool(f) and f["step"] < len(FIELDS)


# ---------- клавиатуры и тексты ----------

def _step_keyboard(step: int) -> dict:
    rows = []
    key = FIELDS[step][0]
    # В callback_data передаём номер варианта, а не текст: лимит Telegram — 64 байта
    for i, opt in enumerate(OPTIONS.get(key, [])):
        rows.append([{"text": opt, "callback_data": f"order_opt_{i}"}])
    nav = []
    if step > 0:
        nav.append({"text": "◀️ Назад", "callback_data": "order_prev"})
    nav.append({"text": "❌ Отменить", "callback_data": "order_cancel"})
    rows.append(nav)
    return {"inline_keyboard": rows}


def _confirm_keyboard() -> dict:
    return {"inline_keyboard": [
        [{"text": "✅ Подтвердить заказ", "callback_data": "order_confirm"}],
        [{"text": "✏️ Заполнить заново", "callback_data": "order_edit"}],
        [{"text": "❌ Отменить", "callback_data": "order_cancel"}],
    ]}


MENU_KB = {"inline_keyboard": [[{"text": "◀️ В главное меню", "callback_data": "main_menu"}]]}


def _summary(form: dict) -> str:
    lines = ["📋 <b>Заявка на выступление</b>\n"]
    for key, name, _ in FIELDS:
        if key in form["data"]:
            lines.append(f"<b>{name}:</b> {html.escape(form['data'][key])}")
    return "\n".join(lines)


def _ask(form: dict, prefix: str = ""):
    step = form["step"]
    if step >= len(FIELDS):
        return _summary(form) + "\n\nПроверьте данные и подтвердите заказ.", _confirm_keyboard()
    return prefix + FIELDS[step][2], _step_keyboard(step)


# ---------- валидация ----------

def _validate(key: str, value: str):
    value = value.strip()
    if not value:
        return None
    if key == "event_date":
        try:
            d = datetime.strptime(value, "%d.%m.%Y")
        except ValueError:
            return None
        if d.date() < datetime.now().date():
            return None
    elif key == "event_time":
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError:
            return None
    elif key == "guest_count":
        if not value.isdigit():
            return None
    return value[:500]


# ---------- публичные функции ----------

def start(chat_id, user: dict):
    f = _get(chat_id)
    if f and f["step"] < len(FIELDS):
        return ("У вас есть незавершённая заявка. Продолжить или начать заново?",
                {"inline_keyboard": [
                    [{"text": "✏️ Продолжить", "callback_data": "order_continue"}],
                    [{"text": "🔄 Начать заново", "callback_data": "order_restart"}],
                    [{"text": "❌ Отменить", "callback_data": "order_cancel"}],
                ]})
    form = {"user": {k: user.get(k) for k in ("id", "username", "first_name", "last_name")},
            "data": {}, "step": 0}
    _forms[str(chat_id)] = form
    _save()
    return _ask(form, "📝 <b>Заказ выступления шоу-балета Oasis Dance</b>\n\n"
                      "Ответьте на несколько вопросов. Отменить можно в любой момент командой /cancel.\n\n")


def cancel(chat_id):
    _delete(chat_id)
    return "❌ Заполнение заявки отменено.", MENU_KB


def process_text(chat_id, text: str):
    form = _get(chat_id)
    if not form or form["step"] >= len(FIELDS):
        return None, None
    if text.strip().lower() in ("/cancel", "отмена", "отменить", "cancel"):
        return cancel(chat_id)
    key, name, _ = FIELDS[form["step"]]
    value = _validate(key, text)
    if value is None:
        return f"⚠️ Не удалось принять значение для поля «{name}».\n\n{FIELDS[form['step']][2]}", \
            _step_keyboard(form["step"])
    form["data"][key] = value
    form["step"] += 1
    _save()
    return _ask(form, "✅ Принято.\n\n")


def handle_callback(chat_id, data: str, user: dict):
    """Возвращает (текст, клавиатура) для callback вида order_*."""
    form = _get(chat_id)

    if data in ("order", "order_start"):
        return start(chat_id, user)
    if data == "order_cancel":
        return cancel(chat_id)
    if data == "order_restart":
        _delete(chat_id)
        return start(chat_id, user)
    if not form:
        return start(chat_id, user)

    if data == "order_continue":
        return _ask(form, "📝 Продолжаем.\n\n")
    if data == "order_prev":
        form["step"] = max(0, form["step"] - 1)
        _save()
        return _ask(form, "⬅️ Вернулись к предыдущему вопросу.\n\n")
    if data.startswith("order_opt_"):
        if form["step"] >= len(FIELDS):
            return _ask(form)
        key = FIELDS[form["step"]][0]
        opts = OPTIONS.get(key, [])
        try:
            form["data"][key] = opts[int(data[len("order_opt_"):])]
        except (ValueError, IndexError):
            return _ask(form)
        form["step"] += 1
        _save()
        return _ask(form, "✅ Принято.\n\n")
    if data == "order_edit":
        form["step"] = 0
        form["data"] = {}
        _save()
        return _ask(form, "✏️ Заполняем заново.\n\n")
    if data == "order_confirm" and form["step"] >= len(FIELDS):
        _store_order(chat_id, form)
        sent = _notify_admins(chat_id, form)
        _delete(chat_id)
        text = ("✅ <b>Заявка отправлена!</b>\n\n"
                "Менеджер свяжется с вами в ближайшее время. Спасибо, что выбрали Oasis Dance!")
        if not sent:
            text += f"\n\nЕсли хотите ускорить ответ — напишите менеджеру: @{config.MANAGER_USERNAME}"
        return text, MENU_KB
    return _ask(form)


def _store_order(chat_id, form):
    record = {"time": datetime.now().isoformat(timespec="seconds"), "chat_id": chat_id,
              "user": form["user"], "data": form["data"]}
    with _lock, open(ORDERS_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("Новая заявка от %s сохранена", chat_id)


def _notify_admins(chat_id, form) -> bool:
    if not config.ADMIN_CHAT_IDS:
        logger.warning("ADMIN_CHAT_IDS не задан — уведомление о заявке никому не отправлено")
        return False
    u = form["user"]
    who = html.escape(" ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "Без имени")
    if u.get("username"):
        who += f" (@{html.escape(u['username'])})"
    text = f"🔔 <b>Новая заявка</b> от {who}\n\n" + _summary(form)
    kb = {"inline_keyboard": [[{"text": "📞 Открыть чат с клиентом", "url": f"tg://user?id={chat_id}"}]]}
    ok = False
    for admin in config.ADMIN_CHAT_IDS:
        r = tg.send_message(admin, text, kb)
        if not (r and r.get("ok")):
            # Кнопка tg://user может не работать для скрытых профилей — шлём без неё
            r = tg.send_message(admin, text)
        ok = ok or bool(r and r.get("ok"))
    return ok
