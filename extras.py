"""
Полезные функции: проверка свободной даты, отзыв после выступления, утренняя сводка и выгрузка заявок.
"""
import csv
import json
import html
import io
import logging
import time
from datetime import date, datetime, timedelta, timezone

import admin
import config
import order_form
import storage
import tg
import ui

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


# ---------- «Свободна ли дата?» ----------

def date_check_start():
    t = date.today()
    return [("📅 <b>Проверим дату</b>\n\nВыберите день — менеджер ответит, свободен ли он. "
             "Ответ придёт сюда же.", order_form.calendar_kb(t.year, t.month, prefix="dc"))]


def date_check_callback(chat_id, user, data):
    if data.startswith("dc_cal_"):
        y, m = map(int, data[7:].split("-"))
        return [("📅 <b>Проверим дату</b>\n\nВыберите день:", order_form.calendar_kb(y, m, prefix="dc"))]
    if data.startswith("dc_day_"):
        if storage.too_many_submits(chat_id):
            return order_form.LIMIT_MSG
        d = datetime.strptime(data[7:], "%Y-%m-%d").date()
        when = d.strftime("%d.%m.%Y")
        u = {k: user.get(k) for k in ("id", "username", "first_name", "last_name")}
        name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)
        oid = storage.add_order(chat_id, "date", u, {"event_date": when, "contact_name": name})
        storage.log_event(chat_id, "callback")  # общий лимит заявок
        sent = admin.notify_new(oid)
        text = (f"🙌 Спасибо! Уточняю у менеджера, свободна ли дата <b>{when}</b>.\n"
                "Ответ придёт сюда — обычно в течение дня.")
        if not sent:
            text += f"\n\nЕсли срочно — напишите @{config.MANAGER_USERNAME}."
        return [(text, {"inline_keyboard": [[{"text": "🎭 Оформить заявку сразу", "callback_data": f"dc_go_{oid}"}],
                                            ui.home_row()]})]
    if data.startswith("dc_go_"):
        order = storage.get_order(int(data[6:]))
        preset = {"event_date": order["data"]["event_date"]} if order and order["chat_id"] == chat_id else None
        return order_form.start(chat_id, user, preset=preset)
    return None


def answer_date(admin_id, oid: int, free: bool):
    """Ответ администратора на запрос даты — клиенту уходит сообщение."""
    order = storage.get_order(oid)
    if not order:
        return
    when = order["data"].get("event_date", "")
    if free:
        text = (f"🎉 Дата <b>{html.escape(when)}</b> свободна!\n\n"
                "Оформим заявку? Это займёт минуту, а менеджер закрепит дату за вами.")
        kb = {"inline_keyboard": [[{"text": "🎭 Оформить заявку", "callback_data": f"dc_go_{oid}"}],
                                  [{"text": "📞 Перезвоните мне", "callback_data": "callme"}]]}
    else:
        text = (f"😔 К сожалению, <b>{html.escape(when)}</b> уже занята.\n\n"
                "Выберите другую дату или свяжитесь с менеджером — подскажем варианты.")
        kb = {"inline_keyboard": [[{"text": "📅 Другая дата", "callback_data": "date"}],
                                  [{"text": "✉️ Написать менеджеру", "url": config.MANAGER_URL}]]}
    with tg.via("client"):
        r = tg.send_message(order["chat_id"], text, kb)
    order["data"]["answer"] = "свободна" if free else "занята"
    storage._q("UPDATE orders SET data=? , status=?, updated=? WHERE id=?",
               (json.dumps(order["data"], ensure_ascii=False), "done", time.time(), oid), commit=True)
    admin._refresh_cards(oid)
    ok = bool(r and r.get("ok"))
    tg.send_message(admin_id, f"{'✅' if ok else '⚠️'} Клиенту "
                              f"{'отправлен ответ' if ok else 'не удалось отправить ответ'}: дата {when} "
                              f"{'свободна' if free else 'занята'}.")


# ---------- отзыв после выступления ----------

def ask_review(oid: int):
    order = storage.get_order(oid)
    if not order or order["kind"] != "form" or order["data"].get("review_asked"):
        return
    order["data"]["review_asked"] = True
    storage._q("UPDATE orders SET data=? WHERE id=?",
               (json.dumps(order["data"], ensure_ascii=False), oid), commit=True)
    stars = [{"text": "⭐" * n, "callback_data": f"rv_{oid}_{n}"} for n in (5, 4, 3)]
    with tg.via("client"):
        tg.send_message(order["chat_id"],
                        "💃 Спасибо, что выбрали Oasis Dance!\n\nКак вам выступление? Поставьте оценку — "
                        "это займёт секунду и очень поможет коллективу.",
                        {"inline_keyboard": [stars[:1], stars[1:],
                                             [{"text": "⭐⭐", "callback_data": f"rv_{oid}_2"},
                                              {"text": "⭐", "callback_data": f"rv_{oid}_1"}]]})


_review_wait = {}


def review_callback(chat_id, data):
    _, oid, n = data.split("_")
    oid, n = int(oid), int(n)
    order = storage.get_order(oid)
    if not order or order["chat_id"] != chat_id:
        return [("Эта оценка уже недоступна.", None)]
    order["data"]["rating"] = n
    storage._q("UPDATE orders SET data=? WHERE id=?",
               (json.dumps(order["data"], ensure_ascii=False), oid), commit=True)
    with tg.via("admin"):
        for admin_id in config.ADMIN_CHAT_IDS:
            tg.send_message(admin_id, f"{'⭐' * n} Оценка по заявке №{oid} от {admin._who(order)}")
    _review_wait[chat_id] = oid
    if n >= 4:
        return [("Спасибо! 🥰 Будем рады, если напишете пару слов о выступлении — "
                 "просто отправьте сообщение. Или нажмите «Пропустить».",
                 {"inline_keyboard": [[{"text": "Пропустить", "callback_data": "rv_skip"}]]})]
    return [("Спасибо за честную оценку. Расскажите, что можно сделать лучше, — "
             "сообщение получит руководитель коллектива.",
             {"inline_keyboard": [[{"text": "Пропустить", "callback_data": "rv_skip"}]]})]


def review_waiting(chat_id):
    return _review_wait.get(chat_id)


def review_text(chat_id, text):
    oid = _review_wait.pop(chat_id, None)
    order = storage.get_order(oid) if oid else None
    if not order:
        return None
    with tg.via("admin"):
        for admin_id in config.ADMIN_CHAT_IDS:
            tg.send_message(admin_id, f"📝 <b>Отзыв</b> по заявке №{oid} ({'⭐' * order['data'].get('rating', 0)}) "
                                      f"от {admin._who(order)}:\n\n{html.escape(text[:2000])}")
    return [("Спасибо за отзыв! Передали коллективу 💛", ui.MAIN_REPLY_KB)]


def review_skip(chat_id):
    _review_wait.pop(chat_id, None)
    return [("Хорошо! Спасибо, что были с нами 💛", None)]


# ---------- утренняя сводка администраторам (10:00 по Москве) ----------

def morning_digest():
    now = datetime.now(MSK)
    today = now.strftime("%Y-%m-%d")
    if now.hour < 10 or storage.get_setting("digest_day") == today:
        return
    storage.set_setting("digest_day", today)
    day_ago = time.time() - 86400
    n_new = storage.count_orders(["new"])
    n_work = storage.count_orders(["work"])
    fresh = storage.count_orders(["new", "work", "done", "rejected"], day_ago)
    drafts = storage.count_drafts("pending")
    if not (n_new or n_work or fresh or drafts):
        return  # нечего сообщать — не беспокоим
    text = ("☀️ <b>Доброе утро! Сводка по боту</b>\n\n"
            f"📥 Новых заявок за сутки: <b>{fresh}</b>\n"
            f"🆕 Ждут ответа: <b>{n_new}</b>\n"
            f"🔄 В работе: {n_work}\n"
            f"📝 Черновиков для канала: {drafts}")
    kb = {"inline_keyboard": [[{"text": "🗂 Открыть заявки", "callback_data": "adm_menu"}]]}
    with tg.via("admin"):
        for admin_id in config.ADMIN_CHAT_IDS:
            tg.send_message(admin_id, text, kb)


# ---------- выгрузка заявок в таблицу ----------

def export_orders(admin_id):
    rows = storage._q("SELECT * FROM orders WHERE status != 'merged' ORDER BY id")
    kinds = {"form": "Заявка", "callback": "Перезвонить", "abandoned": "Не дописал", "date": "Проверка даты"}
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["№", "Дата заявки", "Тип", "Статус", "Имя", "Телефон", "Telegram", "Мероприятие",
                "Дата мероприятия", "Место", "Программа", "Номер", "Пожелания", "Оценка"])
    for r in rows:
        d, u = json.loads(r["data"] or "{}"), json.loads(r["user"] or "{}")
        w.writerow([r["id"], datetime.fromtimestamp(r["created"], MSK).strftime("%d.%m.%Y %H:%M"),
                    kinds.get(r["kind"], r["kind"]), admin.STATUS.get(r["status"], r["status"]),
                    d.get("contact_name", ""), d.get("contact_phone", ""),
                    ("@" + u["username"]) if u.get("username") else "", d.get("event_type", ""),
                    d.get("event_date", ""), d.get("event_location", ""), d.get("program", ""),
                    d.get("wish", ""), d.get("comment", ""), d.get("rating", "")])
    data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM — чтобы Excel открыл кириллицу
    name = f"zayavki_{datetime.now(MSK):%Y-%m-%d}.csv"
    r = tg.send_document(admin_id, name, data, f"📊 Заявки: {len(rows)}. Файл открывается в Excel.")
    if not (r and r.get("ok")):
        tg.send_message(admin_id, "⚠️ Не удалось отправить файл.")
