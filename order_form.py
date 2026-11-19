"""
Короткая заявка на выступление: 6 шагов, календарь, номер телефона одной кнопкой.
Функции возвращают список сообщений [(текст, клавиатура), ...] — их отправляет bot.py.
"""
import calendar
import html
import re
from datetime import date, datetime

import admin
import config
import geo
import storage
import ui

MONTHS = ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль",
          "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"]

# Шаги: ключ, название в сводке, вопрос
STEPS = [
    ("event_type", "Мероприятие", "🎉 <b>Шаг 1 из 7.</b> Какое у вас мероприятие?\nВыберите или напишите своё."),
    ("event_date", "Дата", "📅 <b>Шаг 2 из 7.</b> Выберите дату мероприятия."),
    ("event_time", "Время", "⏰ <b>Шаг 3 из 7.</b> Во сколько начало выступления?\n"
                            "Выберите или напишите, например 19:30."),
    ("event_location", "Место", "📍 <b>Шаг 4 из 7.</b> Где пройдёт мероприятие?\n"
                                "Напишите название площадки или адрес — найду на карте.\n"
                                "Или нажмите «📍 Отправить геолокацию» внизу экрана."),
    ("program", "Программа", "💃 <b>Шаг 5 из 7.</b> Какая программа интересует?"),
    ("contact_phone", "Телефон", "📱 <b>Шаг 6 из 7.</b> Как с вами связаться?\n"
                                 "Нажмите кнопку «Отправить номер» внизу экрана или напишите номер."),
    ("comment", "Пожелания", "✍️ <b>Шаг 7 из 7.</b> Есть пожелания? Напишите их или нажмите «Пропустить»."),
]
KEYS = [s[0] for s in STEPS]
CONTACT_STEP = KEYS.index("contact_phone")
LOCATION_STEP = KEYS.index("event_location")
REPLY_KB_STEPS = {CONTACT_STEP, LOCATION_STEP}  # шаги с кнопками внизу экрана (номер, геолокация)

OPTIONS = {
    "event_type": ["Свадьба", "Корпоратив", "День рождения", "Юбилей", "Выпускной",
                   "Открытие / презентация", "Частный праздник", "Другое"],
    "event_time": ["12:00", "14:00", "16:00", "17:00", "18:00", "19:00", "20:00", "21:00",
                   "22:00", "23:00", "Пока не знаю"],
    "program": ["Стандартная (2–3 номера)", "Расширенная (4–5 номеров)",
                "Индивидуальная программа", "Пока не знаю — помогите выбрать"],
}

BTN_SEND_PHONE = "📱 Отправить номер"
BTN_TG_ONLY = "✉️ Лучше написать в Telegram"
BTN_BACK = "⬅️ Назад"
BTN_CANCEL = "❌ Отменить"
TG_ONLY_VALUE = "не указан — связаться в Telegram"

REMOVE_KB = ui.MAIN_REPLY_KB  # после шага с телефоном возвращаем нижнее меню
LIMIT_MSG = [("Вы уже отправили несколько заявок — менеджер с вами свяжется. "
              "Если срочно — напишите ему напрямую.", None)]
MENU_KB = {"inline_keyboard": [[{"text": "🏠 Главное меню", "callback_data": "main_menu"}]]}


def consent_line() -> str:
    return ("\n\n<i>Отправляя данные, вы соглашаетесь на обработку персональных данных "
            f"({html.escape(config.OPERATOR_NAME)}) для связи по заявке. Подробнее: /privacy</i>")


# ---------- клавиатуры ----------

def _nav(step):
    row = []
    if step > 0:
        row.append({"text": "⬅️ Назад", "callback_data": "of_back"})
    row.append({"text": "❌ Отменить", "callback_data": "of_cancel"})
    return row


def _options_kb(step):
    key = KEYS[step]
    opts = OPTIONS[key]
    rows, row = [], []
    per_row = 4 if key == "event_time" else 2
    for i, opt in enumerate(opts):
        row.append({"text": opt, "callback_data": f"of_opt_{i}"})
        if len(row) == per_row or len(opt) > 14:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(_nav(step))
    return {"inline_keyboard": rows}


def calendar_kb(year: int, month: int, step: int = 0, prefix: str = "of"):
    today = date.today()
    rows = [[{"text": f"{MONTHS[month - 1]} {year}", "callback_data": "of_noop"}],
            [{"text": d, "callback_data": "of_noop"} for d in ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")]]
    for week in calendar.monthcalendar(year, month):
        row = []
        for d in week:
            if d == 0:
                row.append({"text": " ", "callback_data": "of_noop"})
            elif date(year, month, d) < today:
                row.append({"text": "·", "callback_data": "of_noop"})
            else:
                row.append({"text": str(d), "callback_data": f"{prefix}_day_{year}-{month:02d}-{d:02d}"})
        rows.append(row)
    prev_y, prev_m = (year, month - 1) if month > 1 else (year - 1, 12)
    next_y, next_m = (year, month + 1) if month < 12 else (year + 1, 1)
    nav = []
    if (prev_y, prev_m) >= (today.year, today.month):
        nav.append({"text": "◀️", "callback_data": f"{prefix}_cal_{prev_y}-{prev_m:02d}"})
    nav.append({"text": "▶️", "callback_data": f"{prefix}_cal_{next_y}-{next_m:02d}"})
    rows.append(nav)
    if prefix == "of":
        rows.append([{"text": "🤷 Дата пока не известна", "callback_data": "of_nodate"}])
        rows.append(_nav(step))
    else:
        rows.append(ui.home_row())
    return {"inline_keyboard": rows}


BTN_SEND_GEO = "📍 Отправить геолокацию"


def location_kb():
    return {"keyboard": [[{"text": BTN_SEND_GEO, "request_location": True}],
                         [{"text": BTN_BACK}, {"text": BTN_CANCEL}]],
            "resize_keyboard": True, "one_time_keyboard": False,
            "input_field_placeholder": "Название площадки или адрес"}


def contact_kb():
    return {"keyboard": [[{"text": BTN_SEND_PHONE, "request_contact": True}],
                         [{"text": BTN_TG_ONLY}],
                         [{"text": BTN_BACK}, {"text": BTN_CANCEL}]],
            "resize_keyboard": True, "one_time_keyboard": True}


def _confirm_kb():
    return {"inline_keyboard": [
        [{"text": "✅ Отправить заявку", "callback_data": "of_confirm"}],
        [{"text": "✏️ Изменить", "callback_data": "of_restart"}, {"text": "❌ Отменить", "callback_data": "of_cancel"}],
    ]}


# ---------- вывод шага ----------

def _one_line(value) -> str:
    return html.escape(" ".join(str(value).split()))


def summary(data: dict) -> str:
    lines = []
    for key, name, _ in STEPS:
        if data.get(key):
            lines.append(f"<b>{name}:</b> {_one_line(data[key])}")
    if data.get("wish"):
        lines.append(f"<b>Номер:</b> {_one_line(data['wish'])}")
    if data.get("contact_name"):
        lines.append(f"<b>Имя:</b> {_one_line(data['contact_name'])}")
    return "\n".join(lines)


def _ask(form, prefix=""):
    step = form["step"]
    if step >= len(STEPS):
        return [("📋 <b>Проверьте заявку</b>\n\n" + summary(form["data"]) + consent_line(), _confirm_kb())]
    key, _, question = STEPS[step]
    text = prefix + question
    if key in OPTIONS:
        return [(text, _options_kb(step))]
    if key == "event_date":
        t = date.today()
        return [(text, calendar_kb(t.year, t.month, step))]
    if key == "contact_phone":
        return [(text + consent_line(), contact_kb())]
    if key == "event_location":
        return [(text, location_kb())]
    if key == "comment":
        return [(text, {"inline_keyboard": [[{"text": "Пропустить ➡️", "callback_data": "of_skip"}], _nav(step)]})]
    return [(text, {"inline_keyboard": [_nav(step)]})]


def _skip_preset(form):
    while form["step"] < len(STEPS) and KEYS[form["step"]] in form["data"].get("_preset", []) \
            and form["data"].get(KEYS[form["step"]]):
        form["step"] += 1


def _advance(chat_id, form, key, value):
    leaving_reply_step = form["step"] in REPLY_KB_STEPS
    form["data"][key] = value
    form["step"] += 1
    _skip_preset(form)
    storage.save_form(chat_id, form)
    msgs = _ask(form, "✅ Принято.\n\n")
    # Если следующий шаг тоже с кнопками внизу — его клавиатура сама заменит текущую
    if leaving_reply_step and form["step"] not in REPLY_KB_STEPS:
        msgs.insert(0, ("Спасибо! 👍", REMOVE_KB))
    return msgs


# ---------- публичные функции ----------

def is_filling(chat_id) -> bool:
    f = storage.get_form(chat_id)
    return bool(f) and f["step"] < len(STEPS)


def form_step(chat_id):
    f = storage.get_form(chat_id)
    return f["step"] if f else None


def start(chat_id, user: dict, force_new=False, preset: dict = None):
    f = storage.get_form(chat_id)
    if f and preset and f["step"] < len(STEPS):
        # Клиент выбрал номер или дату, уже заполняя заявку, — просто добавим это в неё
        f["data"].update(preset)
        f["data"]["_preset"] = list(set(f["data"].get("_preset", [])) | set(preset))
        storage.save_form(chat_id, f)
        return _ask(f, "👍 Добавил в заявку.\n\n")
    if f and not force_new and 0 < f["step"]:
        return [("У вас есть незаконченная заявка. Продолжим?", {"inline_keyboard": [
            [{"text": "✏️ Продолжить", "callback_data": "of_continue"}],
            [{"text": "🔄 Начать заново", "callback_data": "of_restart"}],
            [{"text": "❌ Отменить", "callback_data": "of_cancel"}]]})]
    storage.delete_form(chat_id)  # новая форма — заново следим, не брошена ли она
    form = {"step": 0, "data": {}, "user": {k: user.get(k) for k in ("id", "username", "first_name", "last_name")}}
    if user.get("first_name"):
        form["data"]["contact_name"] = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
    if preset:
        form["data"].update(preset)
        form["data"]["_preset"] = list(preset)  # хранится вместе с заявкой: эти шаги пропускаем
        _skip_preset(form)
    storage.save_form(chat_id, form)
    intro = "🎭 <b>Заявка на выступление</b>\nКороткие вопросы — около минуты.\n"
    if preset.get("wish") if preset else False:
        intro += f"Номер: <b>{html.escape(preset['wish'])}</b>\n"
    if preset.get("event_date") if preset else False:
        intro += f"Дата: <b>{html.escape(preset['event_date'])}</b>\n"
    return _ask(form, intro + "\n")


def cancel(chat_id):
    storage.delete_form(chat_id)
    return [("Заявку отменил. Если передумаете — нажмите «🎭 Заказать шоу».", REMOVE_KB)]


def _parse_phone(text: str):
    digits = re.sub(r"\D", "", text)
    if 10 <= len(digits) <= 15:
        return text.strip()
    return None


def process_text(chat_id, text: str):
    form = storage.get_form(chat_id)
    if not form or form["step"] >= len(STEPS):
        return None
    t = text.strip()
    if t in (BTN_CANCEL, "/cancel") or t.lower() in ("отмена", "отменить"):
        return cancel(chat_id)
    if t == BTN_BACK:
        return go_back(chat_id)
    step = form["step"]
    key = KEYS[step]

    if key == "contact_phone":
        if t == BTN_TG_ONLY:
            return _advance(chat_id, form, key, TG_ONLY_VALUE)
        phone = _parse_phone(t)
        if not phone:
            return [("⚠️ Не похоже на номер телефона. Нажмите «Отправить номер» или напишите номер, "
                     "например +7 900 123-45-67.", contact_kb())]
        return _advance(chat_id, form, key, phone)

    if key == "event_time":
        t = t.replace(".", ":")
        if re.fullmatch(r"\d{1,2}", t) and int(t) < 24:
            t = f"{int(t):02d}:00"
        return _advance(chat_id, form, key, t[:40])

    if key == "event_location":
        return _location_results(chat_id, form, t)

    if key == "event_date":
        try:
            d = datetime.strptime(t, "%d.%m.%Y").date()
            if d < date.today():
                return [("⚠️ Эта дата уже прошла. Выберите дату в календаре.", _ask(form)[0][1])]
            t = d.strftime("%d.%m.%Y")
        except ValueError:
            pass  # свободный ответ вроде «в середине июня» тоже принимаем
    return _advance(chat_id, form, key, t[:300])


# ---------- поиск площадки ----------

def _location_results(chat_id, form, query: str):
    found, by_ai = geo.search_smart(query)
    form["data"]["_loc_q"] = query[:200]
    form["data"]["_loc_c"] = found[:5]
    storage.save_form(chat_id, form)
    raw_btn = [{"text": "✍️ Оставить как написал", "callback_data": "of_loc_raw"}]
    if not found:
        return [(f"🔎 Не нашёл на карте «{html.escape(query[:60])}».\n\n"
                 "Можно оставить как написали — менеджер уточнит. Или напишите точнее: название и город, "
                 "либо улицу и дом.", {"inline_keyboard": [raw_btn]})]
    rows = [[{"text": "📍 " + c["label"][:60], "callback_data": f"of_loc_{i}"}] for i, c in enumerate(found[:5])]
    rows.append(raw_btn)
    note = " (с помощью ИИ)" if by_ai else ""
    return [(f"🔎 Нашёл на карте{note} — выберите вашу площадку.\n"
             "Нет нужной? Напишите точнее: название и город, либо улицу и дом.", {"inline_keyboard": rows})]


def _choose_location(chat_id, form, label, lat=None, lon=None):
    if lat is not None:
        form["data"]["event_geo"] = f"{lat:.6f},{lon:.6f}"
    else:
        form["data"].pop("event_geo", None)
    form["data"].pop("_loc_c", None)
    form["data"].pop("_loc_q", None)
    return _advance(chat_id, form, "event_location", label)


def process_location(chat_id, location: dict):
    """Геолокация, отправленная кнопкой. None — если форма её не ждёт."""
    form = storage.get_form(chat_id)
    if not form or form["step"] != LOCATION_STEP:
        return None
    lat, lon = location.get("latitude"), location.get("longitude")
    return _choose_location(chat_id, form, geo.reverse(lat, lon), lat, lon)


def process_contact(chat_id, contact: dict):
    """Номер, отправленный кнопкой «Отправить номер». Возвращает None, если форма не ждёт номер."""
    form = storage.get_form(chat_id)
    if not form or form["step"] != CONTACT_STEP:
        return None
    if contact.get("first_name") and not form["data"].get("contact_name"):
        form["data"]["contact_name"] = contact["first_name"]
    return _advance(chat_id, form, "contact_phone", contact.get("phone_number", ""))


def go_back(chat_id):
    form = storage.get_form(chat_id)
    if not form:
        return [("Заявка не найдена.", MENU_KB)]
    was_reply_step = form["step"] in REPLY_KB_STEPS
    form["step"] = max(0, form["step"] - 1)
    storage.save_form(chat_id, form)
    msgs = _ask(form, "⬅️ Вернулись назад.\n\n")
    if was_reply_step and form["step"] not in REPLY_KB_STEPS:
        msgs.insert(0, ("Хорошо.", REMOVE_KB))
    return msgs


def handle_callback(chat_id, data: str, user: dict):
    """Обрабатывает кнопки of_*. Возвращает список сообщений или None (ничего не делать)."""
    if data == "of_noop":
        return None
    if data == "of_cancel":
        return cancel(chat_id)
    if data == "of_restart":
        return start(chat_id, user, force_new=True)

    form = storage.get_form(chat_id)
    if not form:
        if data == "of_confirm":
            return [("Эта заявка уже отправлена ✅", MENU_KB)]
        return start(chat_id, user)
    if data == "of_continue":
        return _ask(form, "📝 Продолжаем.\n\n")
    if data == "of_back":
        return go_back(chat_id)

    if form["step"] >= len(STEPS):
        if data == "of_confirm":
            return submit(chat_id, form)
        return _ask(form)

    key = KEYS[form["step"]]
    if data.startswith("of_opt_") and key in OPTIONS:
        try:
            return _advance(chat_id, form, key, OPTIONS[key][int(data[7:])])
        except (ValueError, IndexError):
            return _ask(form)
    if data.startswith("of_cal_") and key == "event_date":
        y, m = map(int, data[7:].split("-"))
        return [(STEPS[form["step"]][2], calendar_kb(y, m, form["step"]))]
    if data.startswith("of_day_") and key == "event_date":
        d = datetime.strptime(data[7:], "%Y-%m-%d").date()
        return _advance(chat_id, form, key, d.strftime("%d.%m.%Y"))
    if data == "of_nodate" and key == "event_date":
        return _advance(chat_id, form, key, "пока не известна")
    if data.startswith("of_loc_") and key == "event_location":
        if data == "of_loc_raw":
            return _choose_location(chat_id, form, form["data"].get("_loc_q") or "уточнить у клиента")
        cands = form["data"].get("_loc_c") or []
        try:
            c = cands[int(data[7:])]
        except (ValueError, IndexError):
            return _ask(form)
        return _choose_location(chat_id, form, c["label"], c["lat"], c["lon"])
    if data == "of_skip" and key == "comment":
        return _advance(chat_id, form, key, "—")
    return _ask(form)


def submit(chat_id, form):
    form["data"] = {k: v for k, v in form["data"].items() if not k.startswith("_")}
    if storage.too_many_submits(chat_id):
        return LIMIT_MSG
    order_id = storage.add_order(chat_id, "form", form["user"], form["data"])
    storage.close_abandoned(chat_id)
    storage.delete_form(chat_id)
    storage.log_event(chat_id, "order")
    sent = admin.notify_new(order_id)
    text = (f"✅ <b>Заявка №{order_id} отправлена!</b>\n\n"
            "Менеджер свяжется с вами в ближайшее время. Спасибо, что выбрали Oasis Dance! 💃")
    if not sent:
        text += f"\n\nЕсли хотите ускорить ответ — напишите менеджеру: @{config.MANAGER_USERNAME}"
    return [(text, MENU_KB)]


def abandoned_snapshot(chat_id):
    """Данные незавершённой заявки для уведомления администратора."""
    form = storage.get_form(chat_id)
    if not form:
        return None
    return form
