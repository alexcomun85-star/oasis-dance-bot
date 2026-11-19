"""
Кабинет заявок для администраторов: уведомления с кнопками статусов, списки, статистика,
переписка с клиентом через бота.
"""
import html
import json
import logging
import threading
import time
from datetime import datetime

import config
import storage
import tg

logger = logging.getLogger(__name__)

STATUS = {
    "new": "🆕 Новая",
    "work": "🔄 В работе",
    "done": "✅ Выполнена",
    "rejected": "❌ Отклонена",
}
KIND = {
    "form": "🎭 Заявка на выступление",
    "callback": "📞 Просьба перезвонить",
    "abandoned": "⏳ Незавершённая заявка",
    "date": "📅 Проверка даты",
}
TABS = {
    "new": (["new"], "🆕 Новые"),
    "work": (["work"], "🔄 В работе"),
    "closed": (["done", "rejected"], "📁 Закрытые"),
}
PAGE = 8

# Кто из администраторов сейчас пишет ответ клиенту: admin_id -> order_id
_reply_to = {}
# Какой клиент отвечает менеджеру: chat_id -> order_id
_client_reply = {}
_lock = threading.Lock()


def is_admin(chat_id) -> bool:
    return chat_id in config.ADMIN_CHAT_IDS


# ---------- карточка заявки ----------

def _who(order) -> str:
    u = order["user"] or {}
    name = " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x) or "Клиент"
    link = f'<a href="tg://user?id={order["chat_id"]}">{html.escape(name)}</a>'
    if u.get("username"):
        link += f" (@{html.escape(u['username'])})"
    return link


def card_text(order) -> str:
    import order_form  # здесь, чтобы избежать циклического импорта
    created = datetime.fromtimestamp(order["created"]).strftime("%d.%m.%Y %H:%M")
    lines = [f"<b>{KIND.get(order['kind'], 'Заявка')} №{order['id']}</b>",
             f"Статус: {STATUS.get(order['status'], order['status'])}",
             f"Клиент: {_who(order)}",
             f"Получена: {created}", ""]
    body = order_form.summary(order["data"]) if order["data"] else ""
    lines.append(body or "<i>Данных нет</i>")
    if order["kind"] == "abandoned":
        lines.append("\n<i>Клиент начал заполнять заявку и не закончил. Можно написать ему первым.</i>")
    if order["kind"] == "date" and order["status"] == "new":
        lines.append("\n<i>Клиент спрашивает, свободна ли дата. Нажмите «Свободна» или «Занята» — "
                     "бот сам ответит ему.</i>")
    if order["data"].get("answer"):
        lines.append(f"<b>Ответ клиенту:</b> дата {order['data']['answer']}")
    import crm
    extra = crm.card_lines(order)
    if extra:
        lines += [""] + extra
    return "\n".join(lines)


def card_kb(order, from_list: str = None):
    oid, st = order["id"], order["status"]
    rows = []
    if order["kind"] == "date" and st == "new":
        rows.append([{"text": "✅ Свободна", "callback_data": f"adm_df_{oid}_1"},
                     {"text": "❌ Занята", "callback_data": f"adm_df_{oid}_0"}])
        rows.append([{"text": "💬 Написать клиенту", "callback_data": f"adm_msg_{oid}"}])
        if from_list:
            rows.append([{"text": "⬅️ К списку", "callback_data": f"adm_list_{from_list}_0"},
                         {"text": "🗂 Кабинет", "callback_data": "adm_menu"}])
        return {"inline_keyboard": rows}
    if st == "new":
        rows.append([{"text": "🔄 Взять в работу", "callback_data": f"adm_st_{oid}_work"},
                     {"text": "❌ Отклонить", "callback_data": f"adm_st_{oid}_rejected"}])
    elif st == "work":
        rows.append([{"text": "✅ Выполнена", "callback_data": f"adm_st_{oid}_done"},
                     {"text": "❌ Отклонить", "callback_data": f"adm_st_{oid}_rejected"}])
        rows.append([{"text": "↩️ Вернуть в новые", "callback_data": f"adm_st_{oid}_new"}])
    else:
        rows.append([{"text": "↩️ Вернуть в работу", "callback_data": f"adm_st_{oid}_work"}])
    import calendar_ics
    import crm
    rows.append([{"text": "💬 Написать клиенту", "callback_data": f"adm_msg_{oid}"}])
    rows += crm.card_rows(order)
    rows += calendar_ics.calendar_buttons(order)
    geo = (order.get("data") or {}).get("event_geo")
    if geo:
        lat, lon = geo.split(",")
        rows.append([{"text": "🗺 Площадка на карте", "url": f"https://yandex.ru/maps/?pt={lon},{lat}&z=16&l=map"}])
    if from_list:
        rows.append([{"text": "⬅️ К списку", "callback_data": f"adm_list_{from_list}_0"},
                     {"text": "🗂 Кабинет", "callback_data": "adm_menu"}])
    return {"inline_keyboard": rows}


def notify_new(order_id: int) -> bool:
    """Рассылает карточку новой заявки всем администраторам. True — хотя бы одному дошло."""
    order = storage.get_order(order_id)
    if not order or not config.ADMIN_CHAT_IDS:
        if not config.ADMIN_CHAT_IDS:
            logger.warning("ADMIN_CHAT_IDS не задан — заявка №%s никому не отправлена", order_id)
        return False
    ok = False
    with tg.via("admin"):
        for admin_id in config.ADMIN_CHAT_IDS:
            r = tg.send_message(admin_id, "🔔 " + card_text(order), card_kb(order))
            if r and r.get("ok"):
                ok = True
                storage.add_order_msg(order_id, admin_id, r["result"]["message_id"])
    return ok


def _refresh_cards(order_id, skip=None):
    """Обновляет карточку заявки у всех администраторов, чтобы статус был везде одинаковый."""
    order = storage.get_order(order_id)
    for m in storage.get_order_msgs(order_id):
        if skip and (m["admin_id"], m["message_id"]) == skip:
            continue
        with tg.via("admin"):
            tg.call("editMessageText", {"chat_id": m["admin_id"], "message_id": m["message_id"],
                                        "text": "🔔 " + card_text(order), "parse_mode": "HTML",
                                        "reply_markup": card_kb(order)})


# ---------- экраны кабинета ----------

def menu_screen():
    n_new = storage.count_orders(["new"])
    n_work = storage.count_orders(["work"])
    text = ("🗂 <b>Кабинет заявок</b>\n\n"
            f"🆕 Новых: <b>{n_new}</b>\n"
            f"🔄 В работе: <b>{n_work}</b>\n\n"
            "Новые заявки приходят сюда сразу с кнопками. Здесь — списки и статистика.")
    kb = {"inline_keyboard": [
        [{"text": f"🆕 Новые ({n_new})", "callback_data": "adm_list_new_0"},
         {"text": f"🔄 В работе ({n_work})", "callback_data": "adm_list_work_0"}],
        [{"text": "📁 Закрытые", "callback_data": "adm_list_closed_0"},
         {"text": "📊 Статистика", "callback_data": "adm_stats"}],
        [{"text": "📅 Календарь", "callback_data": "adm_cal"},
         {"text": "📊 Выгрузить в Excel", "callback_data": "adm_export"}],
        [{"text": "📢 Пост в канал", "callback_data": "ch_menu"},
         {"text": "🔗 Соцсети → канал", "callback_data": "src_menu"}],
    ]}
    return text, kb


def _short(order) -> str:
    d = order["data"]
    parts = [f"№{order['id']}"]
    if order["kind"] == "callback":
        parts.append("📞 перезвонить")
    elif order["kind"] == "abandoned":
        parts.append("⏳ не дописал")
    elif order["kind"] == "date":
        parts.append("📅 дата?")
    if d.get("event_type"):
        parts.append(d["event_type"])
    if d.get("event_date") and d["event_date"] != "пока не известна":
        parts.append(d["event_date"][:5])
    name = d.get("contact_name") or (order["user"] or {}).get("first_name")
    if name:
        parts.append(name)
    return " · ".join(parts)[:60]


def list_screen(tab: str, page: int):
    statuses, title = TABS.get(tab, TABS["new"])
    total = storage.count_orders(statuses)
    orders = storage.list_orders(statuses, PAGE, page * PAGE)
    text = f"{title}: <b>{total}</b>"
    if not orders:
        text += "\n\nЗдесь пока пусто."
    else:
        text += "\n\nНажмите на заявку, чтобы открыть её."
    rows = [[{"text": _short(o), "callback_data": f"adm_open_{o['id']}_{tab}"}] for o in orders]
    nav = []
    if page > 0:
        nav.append({"text": "◀️", "callback_data": f"adm_list_{tab}_{page - 1}"})
    if (page + 1) * PAGE < total:
        nav.append({"text": "▶️", "callback_data": f"adm_list_{tab}_{page + 1}"})
    if nav:
        rows.append(nav)
    rows.append([{"text": "🗂 Кабинет", "callback_data": "adm_menu"}])
    return text, {"inline_keyboard": rows}


def stats_screen():
    now = time.time()
    week, month = now - 7 * 86400, now - 30 * 86400
    users = storage._q("SELECT COUNT(*) c FROM users", one=True)["c"]
    new_w = storage._q("SELECT COUNT(*) c FROM users WHERE first_seen>=?", (week,), one=True)["c"]
    new_m = storage._q("SELECT COUNT(*) c FROM users WHERE first_seen>=?", (month,), one=True)["c"]
    real = ["new", "work", "done", "rejected"]
    o_w = storage.count_orders(real, week, "form") + storage.count_orders(real, week, "callback")
    o_m = storage.count_orders(real, month, "form") + storage.count_orders(real, month, "callback")
    ab_m = storage.count_orders(None, month, "abandoned")
    done = storage.count_orders(["done"])
    import crm
    crm_lines = crm.stats_lines()
    ai_w = storage.count_events("ai", week)
    from_ch = storage.count_events("src_channel", month)
    conv = f"{o_m / new_m * 100:.0f}%" if new_m else "—"
    text = ("📊 <b>Статистика</b>\n\n"
            f"👥 Пользователей всего: <b>{users}</b>\n"
            f"   новых за 7 дней: {new_w}, за 30 дней: {new_m}\n\n"
            f"📥 Заявок за 7 дней: <b>{o_w}</b>, за 30 дней: <b>{o_m}</b>\n"
            f"⏳ Брошенных за 30 дней: {ab_m}\n"
            f"✅ Выполнено всего: {done}\n"
            f"📈 Заявок на нового пользователя (30 дн.): {conv}\n\n"
            + ("\n".join(crm_lines) + "\n\n" if crm_lines else "") +
            f"📢 Пришли из канала за 30 дней: {from_ch}\n"
            f"🤖 Вопросов ассистенту за 7 дней: {ai_w}")
    return text, {"inline_keyboard": [[{"text": "🗂 Кабинет", "callback_data": "adm_menu"}]]}


# ---------- обработка кнопок администратора ----------

def handle_callback(chat_id, message_id, data: str):
    """Возвращает (текст, клавиатура) для редактирования сообщения или None."""
    if not is_admin(chat_id):
        return None
    if data == "adm_menu":
        return menu_screen()
    if data == "adm_stats":
        return stats_screen()
    if data == "adm_status":
        return status_screen()
    if data == "adm_cal":
        return calendar_screen()
    if data == "adm_calsub":
        return calendar_subscribe_screen()
    if data == "adm_calreset":
        import calendar_ics
        calendar_ics.reset_secret()
        return calendar_subscribe_screen()
    if data == "adm_calall":
        import calendar_ics
        data_ics = calendar_ics.build_ics(calendar_ics.calendar_orders(), with_phone=True)
        tg.send_document(chat_id, "oasis-dance-vse.ics", data_ics,
                         "📅 Все выступления: нажмите на файл, чтобы добавить их в календарь.")
        return None
    if data.startswith("adm_ics_"):
        send_order_ics(chat_id, int(data[8:]))
        return None
    if data == "adm_settings":
        return settings_screen()
    if data == "adm_export":
        import extras
        extras.export_orders(chat_id)
        return None
    if data.startswith("crm_"):
        import crm
        parts = data.split("_")
        oid = int(parts[2])
        if parts[1] == "note":
            crm.start_input(chat_id, "note", oid)
        elif parts[1] == "sum":
            crm.start_input(chat_id, "amount", oid)
        elif parts[1] == "rem":
            return f"⏰ Когда напомнить про заявку №{oid}?", crm.reminder_options(oid)
        elif parts[1] == "remset":
            note = crm.set_reminder(chat_id, oid, parts[3])
            order = storage.get_order(oid)
            return note + "\n\n" + card_text(order), card_kb(order, "work")
        elif parts[1] == "tpl":
            crm.send_template(chat_id, oid, int(parts[3]))
        return None
    if data.startswith("adm_df_"):
        import extras
        _, _, oid, free = data.split("_")
        extras.answer_date(chat_id, int(oid), free == "1")
        return None
    if data == "adm_aitest":
        tg.send_message(chat_id, "🧪 Проверяю ИИ, это займёт до минуты…")
        tg.send_message(chat_id, ai_test())
        return None
    if data.startswith("adm_list_"):
        _, _, tab, page = data.split("_")
        return list_screen(tab, int(page))
    if data.startswith("adm_open_"):
        _, _, oid, tab = data.split("_")
        order = storage.get_order(int(oid))
        if not order:
            return "Заявка не найдена.", {"inline_keyboard": [[{"text": "🗂 Кабинет", "callback_data": "adm_menu"}]]}
        return card_text(order), card_kb(order, tab)
    if data.startswith("adm_st_"):
        _, _, oid, status = data.split("_")
        oid = int(oid)
        if status not in STATUS or not storage.get_order(oid):
            return None
        storage.set_status(oid, status)
        _refresh_cards(oid, skip=(chat_id, message_id))
        if status == "done":
            import extras
            extras.ask_review(oid)  # клиенту — просьба оценить выступление
            n_done = storage.count_orders(["done"])
            tg.send_message(chat_id, f"🎉 Отлично, заявка №{oid} выполнена! Это уже {n_done}-е выступление в боте. "
                                     "Клиенту отправлена просьба оставить отзыв 💛")
        if status == "work":
            import crm
            crm.mark_taken(oid)
            notify_client_in_work(oid)
        order = storage.get_order(oid)
        is_notification = any(m["admin_id"] == chat_id and m["message_id"] == message_id
                              for m in storage.get_order_msgs(oid))
        if is_notification:
            return "🔔 " + card_text(order), card_kb(order)
        tab = {"new": "new", "work": "work"}.get(status, "closed")
        return card_text(order), card_kb(order, tab)
    if data.startswith("adm_msg_"):
        oid = int(data.split("_")[2])
        with _lock:
            _reply_to[chat_id] = oid
        import crm
        tg.send_message(chat_id, f"✍️ Ответ клиенту по заявке №{oid}\n\n"
                                 "Выберите готовый текст — он уйдёт сразу. Или напишите своё сообщение: "
                                 "бот перешлёт его от имени шоу-балета. Отмена — /cancel", crm.templates_kb(oid))
        return None
    return None


def awaiting_reply(chat_id):
    with _lock:
        return _reply_to.get(chat_id)


def cancel_reply(chat_id):
    with _lock:
        return _reply_to.pop(chat_id, None) is not None


def send_to_client(admin_id, text: str):
    with _lock:
        oid = _reply_to.pop(admin_id, None)
    order = storage.get_order(oid) if oid else None
    if not order:
        tg.send_message(admin_id, "Заявка не найдена.")
        return
    with tg.via("client"):
        r = tg.send_message(order["chat_id"],
                            f"💬 <b>Сообщение от менеджера Oasis Dance</b> (заявка №{oid}):\n\n{html.escape(text)}",
                            {"inline_keyboard": [[{"text": "↩️ Ответить менеджеру", "callback_data": f"cli_rep_{oid}"}]]})
    if r and r.get("ok"):
        if order["status"] == "new":
            import crm
            storage.set_status(oid, "work")
            crm.mark_taken(oid)
            _refresh_cards(oid)
        tg.send_message(admin_id, f"✅ Отправлено клиенту по заявке №{oid}.",
                        {"inline_keyboard": [[{"text": "📄 Открыть заявку", "callback_data": f"adm_open_{oid}_work"}]]})
    else:
        tg.send_message(admin_id, "⚠️ Не удалось отправить: клиент, возможно, остановил бота. "
                                  "Попробуйте связаться по телефону или в Telegram напрямую.")


# ---------- ответы клиента ----------

def client_start_reply(chat_id, oid: int) -> bool:
    order = storage.get_order(oid)
    if not order or order["chat_id"] != chat_id:
        return False
    with _lock:
        _client_reply[chat_id] = oid
    return True


def client_awaiting(chat_id):
    with _lock:
        return _client_reply.get(chat_id)


def client_cancel(chat_id):
    with _lock:
        _client_reply.pop(chat_id, None)


def forward_client_reply(chat_id, text: str) -> bool:
    with _lock:
        oid = _client_reply.pop(chat_id, None)
    order = storage.get_order(oid) if oid else None
    if not order:
        return False
    delivered = False
    for admin_id in config.ADMIN_CHAT_IDS:
        with tg.via("admin"):
            r = tg.send_message(admin_id, f"💬 <b>Ответ клиента</b> {_who(order)} по заявке №{oid}:\n\n"
                                          f"{html.escape(text)}",
                                {"inline_keyboard": [[{"text": "💬 Ответить", "callback_data": f"adm_msg_{oid}"},
                                                      {"text": "📄 Заявка", "callback_data": f"adm_open_{oid}_work"}]]})
        delivered = delivered or bool(r and r.get("ok"))
    return delivered


# ---------- брошенные заявки ----------

def check_abandoned():
    for chat_id in storage.stale_forms(config.ABANDON_MINUTES * 60):
        form = storage.get_form(chat_id)
        storage.mark_form_notified(chat_id)
        if not form or form["step"] < 1:
            continue  # клиент ничего не успел заполнить
        data = {k: v for k, v in form["data"].items()}
        oid = storage.add_order(chat_id, "abandoned", form["user"], data)
        notify_new(oid)
        logger.info("Брошенная заявка от %s сохранена как №%s", chat_id, oid)


# ---------- состояние подключений ----------

def status_screen():
    import ai
    from urllib.parse import urlparse
    yes, no = "✅", "❌"
    fb_host = urlparse(config.AI_FALLBACK_URL).netloc
    lu = ai.last_used
    last = (f"{lu['model']}, {time.strftime('%d.%m %H:%M', time.localtime(lu['time']))}"
            if lu["model"] else "с момента запуска ещё не отвечал")
    text = ("⚙️ <b>Что подключено</b>\n\n"
            f"{yes} Telegram: бот работает\n"
            f"{yes if config.GEMINI_API_KEY else no} Gemini (основной ИИ)\n"
            f"{yes if config.AI_FALLBACK_KEY else no} Запасной ИИ: {html.escape(fb_host)}"
            f"{' (модель ' + html.escape(config.AI_FALLBACK_MODEL) + ')' if config.AI_FALLBACK_MODEL != 'auto' else ''}\n"
            f"   последний ответ ИИ: {html.escape(last)}\n"
            f"{yes if config.VK_SERVICE_TOKEN else no} ВКонтакте (ключ)\n"
            f"{yes if config.FEED_SOURCES else no} Ленты (Instagram и др.): {len(config.FEED_SOURCES)}\n"
            f"📢 Канал: {html.escape(config.CHANNEL_ID)}\n"
            f"👤 Администраторы: {', '.join(map(str, config.ADMIN_CHAT_IDS)) or '—'}")
    return text, {"inline_keyboard": [[{"text": "🧪 Проверить ИИ", "callback_data": "adm_aitest"}],
                                      [{"text": "🗂 Кабинет", "callback_data": "adm_menu"}]]}


def ai_test():
    import ai
    started = time.time()
    try:
        ans = ai._call("Ответь коротко по-русски.", [{"role": "user", "parts": [{"text": "Скажи: работает"}]}],
                       temperature=0, max_tokens=500)
        return (f"🧪 ИИ отвечает ✅\nОтветил: {html.escape(ai.last_used['model'] or '?')} "
                f"за {time.time() - started:.1f} с\nОтвет: {html.escape(ans[:100])}")
    except Exception as e:
        return f"🧪 ИИ не ответил ❌\n{html.escape(str(e))[:300]}\nПодробности — в журнале бота."


# ---------- календарь ----------

WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]


def calendar_screen():
    import calendar_ics
    items = calendar_ics.upcoming()
    lines = ["📅 <b>Календарь выступлений</b>\n"]
    rows = []
    if not items:
        lines.append("Ближайших выступлений нет. Сюда попадают заявки с датой, которые вы взяли в работу.")
    for day, o in items:
        d = o["data"]
        what = d.get("event_type") or "выступление"
        who = d.get("contact_name") or ""
        place = d.get("event_location") or ""
        lines.append(f"<b>{day:%d.%m} ({WEEKDAYS[day.weekday()]})</b> — {html.escape(what)}"
                     f"{', ' + html.escape(who) if who else ''}{', ' + html.escape(place[:30]) if place else ''}")
        if len(rows) < 8:
            rows.append([{"text": f"{day:%d.%m} · №{o['id']} · {what}"[:40], "callback_data": f"adm_open_{o['id']}_work"}])
    rows.append([{"text": "📥 Все выступления — в календарь телефона", "callback_data": "adm_calall"}])
    rows.append([{"text": "🔄 Автообновление (подписка по ссылке)", "callback_data": "adm_calsub"}])
    return "\n".join(lines), {"inline_keyboard": rows}


def calendar_subscribe_screen():
    import calendar_ics
    url = calendar_ics.subscribe_url()
    if not url:
        return ("⚠️ Не удалось определить адрес сервера. Впишите PUBLIC_URL в настройки бота.",
                {"inline_keyboard": [[{"text": "⬅️ Календарь", "callback_data": "adm_cal"}]]})
    last = storage.get_setting("calendar_last_fetch")
    seen = (f"\n\n✅ Телефон последний раз обновлял календарь: "
            f"{datetime.fromtimestamp(int(last)).strftime('%d.%m %H:%M')}") if last else ""
    text = ("📲 <b>Календарь выступлений на телефоне</b>\n\n"
            "Подпишитесь один раз — новые и изменённые выступления будут появляться сами. "
            "В календарь попадают заявки с датой в статусе «В работе» и «Выполнена», "
            "за день до выступления придёт напоминание.\n\n"
            f"Ссылка (никому не пересылайте):\n<code>{html.escape(url)}</code>\n\n"
            "<b>iPhone:</b> Настройки → Приложения → Календарь → Учётные записи → Новая учётная запись → "
            "Другое → «Подписной календарь» → вставьте ссылку → Далее → Сохранить.\n\n"
            "<b>Android (Google Календарь):</b> на компьютере откройте calendar.google.com → слева «Другие "
            "календари» → «+» → «Добавить по URL» → вставьте ссылку. Через несколько минут календарь "
            "появится в приложении на телефоне (включите его в настройках приложения).\n\n"
            "Подписка обновляется не сразу (Google — до нескольких часов). Чтобы выступление появилось "
            "мгновенно, нажмите в карточке заявки «📅 Google Календарь» или «🍏 Календарь iPhone»." + seen)
    return text, {"inline_keyboard": [
        [{"text": "🔄 Выдать новую ссылку (старая перестанет работать)", "callback_data": "adm_calreset"}],
        [{"text": "⬅️ Календарь", "callback_data": "adm_cal"}]]}


def send_order_ics(chat_id, oid, for_client=False) -> bool:
    import calendar_ics
    order = storage.get_order(oid)
    if not order or not calendar_ics.parse_date(order["data"].get("event_date")):
        tg.send_message(chat_id, "У этой заявки нет точной даты — добавить в календарь нельзя.")
        return False
    data = calendar_ics.build_ics([order], name="Oasis Dance", with_phone=not for_client, for_client=for_client)
    when = order["data"]["event_date"].replace(".", "-")
    caption = ("📅 Нажмите на файл, чтобы добавить выступление в календарь телефона."
               if for_client else f"📅 Заявка №{oid}: нажмите на файл — событие добавится в календарь.")
    with tg.via("client" if for_client else "admin"):
        r = tg.send_document(chat_id, f"oasis-dance-{when}.ics", data, caption)
    return bool(r and r.get("ok"))


def notify_client_in_work(oid):
    """Клиенту — что заявку взяли в работу, и кнопка «Добавить в календарь»."""
    import calendar_ics
    order = storage.get_order(oid)
    if not order or order["kind"] != "form" or order["data"].get("work_notified"):
        return
    order["data"]["work_notified"] = True
    storage._q("UPDATE orders SET data=? WHERE id=?", (json.dumps(order["data"], ensure_ascii=False), oid),
               commit=True)
    rows = calendar_ics.calendar_buttons(order, for_client=True)
    with tg.via("client"):
        tg.send_message(order["chat_id"], f"✅ Ваша заявка №{oid} принята в работу. "
                                          "Менеджер свяжется с вами, чтобы обсудить детали."
                                          + ("\n\nДобавьте выступление в календарь телефона 👇" if rows else ""),
                        {"inline_keyboard": rows} if rows else None)


# ---------- настройки менеджера ----------

def settings_screen():
    text, _ = status_screen()
    text += "\n\n<b>Команды менеджера:</b> /admin, /orders, /stats, /export, /post, /sources, /status, /myid"
    return text, {"inline_keyboard": [
        [{"text": "🧪 Проверить ИИ", "callback_data": "adm_aitest"},
         {"text": "📊 Выгрузить в Excel", "callback_data": "adm_export"}],
        [{"text": "📲 Календарь на телефоне", "callback_data": "adm_calsub"}],
    ]}


def manager_welcome(user):
    n_new = storage.count_orders(["new"])
    n_work = storage.count_orders(["work"])
    name = html.escape(user.get("first_name") or "")
    return (f"🧑‍💼 <b>Меню менеджера</b>{', ' + name if name else ''}\n\n"
            f"🆕 Новых заявок: <b>{n_new}</b>\n🔄 В работе: <b>{n_work}</b>\n\n"
            "Кнопки — внизу экрана. Новые заявки приходят сюда сразу.\n"
            "Чтобы проверить бота глазами клиента — «👀 Как видит клиент».")
