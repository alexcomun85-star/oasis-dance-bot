#!/usr/bin/env python3
"""
Telegram-бот шоу-балета Oasis Dance (@oasisdance_show_bot).
Запуск: python3 bot.py  (настройки — в файле .env)
"""
import html
import logging
import os
import sys
import time
import traceback
from logging.handlers import RotatingFileHandler

import config

LOG_FILE = os.path.join(config.DATA_DIR, "bot.log")
logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
    handlers=[logging.StreamHandler(),
              RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3, encoding="utf-8")],
)
logger = logging.getLogger("oasis")

import ai  # noqa: E402
import order_form  # noqa: E402
import tg  # noqa: E402

# Режим ИИ-ассистента и история диалогов (в памяти)
ai_sessions: dict = {}
AI_EXIT_WORDS = {"выход", "exit", "quit", "стоп", "stop", "закрыть"}


# ---------- клавиатуры ----------

def main_keyboard():
    rows = [
        [{"text": "🎭 Заказать выступление", "callback_data": "order"}],
        [{"text": "🎬 Видео выступлений", "callback_data": "video"}],
        [{"text": "📱 Связаться с менеджером", "callback_data": "contact"}],
        [{"text": "📸 Instagram", "callback_data": "instagram"},
         {"text": "👥 VK", "callback_data": "vk"}],
        [{"text": "👩‍💼 Предложить сотрудничество", "callback_data": "partnership"}],
    ]
    if ai.is_available():
        rows.insert(3, [{"text": "🤖 Спросить ассистента", "callback_data": "oasis"}])
    return {"inline_keyboard": rows}


def back_row():
    return [{"text": "◀️ Главное меню", "callback_data": "main_menu"}]


def manager_row(text="💬 Написать менеджеру"):
    return [{"text": text, "url": config.MANAGER_URL}]


# ---------- разделы ----------

def show_start(chat_id, user):
    name = html.escape(user.get("first_name") or "Гость")
    tg.send_message(chat_id, (
        f"👋 Здравствуйте, {name}!\n\n"
        "Добро пожаловать в официальный бот шоу-балета <b>Oasis Dance</b>.\n\n"
        "Здесь вы можете:\n"
        "🎭 заказать выступление на ваше мероприятие\n"
        "🎬 посмотреть видео наших выступлений\n"
        "📱 связаться с менеджером\n"
        "👩‍💼 предложить сотрудничество\n\n"
        "Выберите раздел:"
    ), main_keyboard())


def show_help(chat_id):
    text = (
        "📌 <b>Команды бота</b>\n\n"
        "/start — главное меню\n"
        "/order — заказать выступление\n"
        "/video — видео выступлений\n"
        "/contact — контакты менеджера\n"
        "/instagram — наш Instagram\n"
        "/vk — наша группа ВКонтакте\n"
        "/partnership — сотрудничество\n"
    )
    if ai.is_available():
        text += "/oasis — задать вопрос ассистенту\n"
    text += "/cancel — отменить заполнение заявки"
    tg.send_message(chat_id, text, main_keyboard())


def show_video(chat_id):
    tg.send_message(chat_id,
                    "🎬 <b>Видео выступлений шоу-балета Oasis Dance</b>\n\n"
                    "Видео наших номеров — в Instagram и Telegram-канале:",
                    {"inline_keyboard": [
                        [{"text": "📸 Instagram", "url": config.INSTAGRAM_URL}],
                        [{"text": "📢 Telegram-канал", "url": config.CHANNEL_URL}],
                        back_row()]})


def show_contact(chat_id):
    lines = ["📱 <b>Контакты менеджера</b>\n",
             "Менеджер ответит на вопросы о выступлениях, репертуаре и стоимости.\n",
             f"📲 Telegram: @{config.MANAGER_USERNAME}"]
    if config.MANAGER_PHONE:
        lines.append(f"☎️ Телефон: {config.MANAGER_PHONE}")
    if config.MANAGER_EMAIL:
        lines.append(f"📧 Email: {config.MANAGER_EMAIL}")
    if config.WORK_HOURS:
        lines.append(f"\nВремя работы: {config.WORK_HOURS}")
    tg.send_message(chat_id, "\n".join(lines),
                    {"inline_keyboard": [manager_row("💬 Написать в Telegram"), back_row()]})


def show_instagram(chat_id):
    tg.send_message(chat_id,
                    "📸 <b>Наш Instagram</b>\n\nНовости, видео выступлений и фото с мероприятий.",
                    {"inline_keyboard": [[{"text": "📸 Перейти в Instagram", "url": config.INSTAGRAM_URL}],
                                         back_row()]})


def show_vk(chat_id):
    tg.send_message(chat_id,
                    "👥 <b>Наша группа ВКонтакте</b>\n\nНовости и анонсы мероприятий.",
                    {"inline_keyboard": [[{"text": "👥 Перейти во ВКонтакте", "url": config.VK_URL}],
                                         back_row()]})


def show_partnership(chat_id):
    tg.send_message(chat_id,
                    "👩‍💼 <b>Сотрудничество с шоу-балетом Oasis Dance</b>\n\n"
                    "Мы открыты для сотрудничества с агентствами, площадками и организаторами. "
                    "Напишите менеджеру, чтобы обсудить детали.",
                    {"inline_keyboard": [manager_row(), back_row()]})


def show_order(chat_id, user):
    text, kb = order_form.start(chat_id, user)
    tg.send_message(chat_id, text, kb)


# ---------- ИИ-ассистент ----------

def ai_start(chat_id, user):
    if not ai.is_available():
        tg.send_message(chat_id, "⚠️ Ассистент сейчас недоступен. Напишите, пожалуйста, менеджеру.",
                        {"inline_keyboard": [manager_row(), back_row()]})
        return
    ai_sessions[chat_id] = {"on": True, "history": []}
    name = html.escape(user.get("first_name") or "")
    tg.send_message(chat_id, (
        f"🤖 Здравствуйте{', ' + name if name else ''}! Я виртуальный ассистент шоу-балета Oasis Dance.\n\n"
        "Задайте вопрос о коллективе, репертуаре или заказе выступления.\n"
        "Чтобы выйти, напишите «выход» или нажмите кнопку ниже."
    ), {"inline_keyboard": [[{"text": "◀️ Выйти из режима ассистента", "callback_data": "exit_ai"}]]})


def ai_stop(chat_id):
    ai_sessions.pop(chat_id, None)
    tg.send_message(chat_id, "Вы вышли из режима ассистента. Чем ещё помочь?", main_keyboard())


def ai_message(chat_id, text):
    if text.strip().lower() in AI_EXIT_WORDS:
        ai_stop(chat_id)
        return
    session = ai_sessions[chat_id]
    tg.typing(chat_id)
    try:
        answer = ai.generate(text, session["history"])
    except Exception as e:
        logger.error("Ассистент не ответил: %s", e)
        tg.send_message(chat_id, "Извините, ассистент сейчас не может ответить. Напишите менеджеру.",
                        {"inline_keyboard": [manager_row(), back_row()]})
        return
    session["history"] += [{"role": "user", "content": text}, {"role": "assistant", "content": answer}]
    session["history"] = session["history"][-20:]
    tg.send_message(chat_id, html.escape(answer), {"inline_keyboard": [
        [{"text": "🎭 Заказать выступление", "callback_data": "order"}],
        [{"text": "◀️ Выйти из режима ассистента", "callback_data": "exit_ai"}]]})


# ---------- маршрутизация ----------

COMMANDS = {
    "/help": lambda c, u: show_help(c),
    "/video": lambda c, u: show_video(c),
    "/contact": lambda c, u: show_contact(c),
    "/instagram": lambda c, u: show_instagram(c),
    "/vk": lambda c, u: show_vk(c),
    "/partnership": lambda c, u: show_partnership(c),
    "/order": show_order,
    "/oasis": ai_start,
    "/chatgpt": ai_start,
}

CALLBACKS = {
    "main_menu": show_start,
    "video": lambda c, u: show_video(c),
    "contact": lambda c, u: show_contact(c),
    "instagram": lambda c, u: show_instagram(c),
    "vk": lambda c, u: show_vk(c),
    "partnership": lambda c, u: show_partnership(c),
    "oasis": ai_start,
    "exit_ai": lambda c, u: ai_stop(c),
}

TEXT_SHORTCUTS = {
    "заказать": show_order, "заказать шоу": show_order,
    "видео": lambda c, u: show_video(c),
    "контакт": lambda c, u: show_contact(c), "контакты": lambda c, u: show_contact(c),
    "инстаграм": lambda c, u: show_instagram(c), "instagram": lambda c, u: show_instagram(c),
    "вк": lambda c, u: show_vk(c), "vk": lambda c, u: show_vk(c),
    "сотрудничество": lambda c, u: show_partnership(c), "партнерство": lambda c, u: show_partnership(c),
    "привет": show_start, "здравствуйте": show_start, "добрый день": show_start,
    "помощь": lambda c, u: show_help(c), "справка": lambda c, u: show_help(c),
    "ассистент": ai_start, "помощник": ai_start,
}


def on_message(msg):
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return  # в группах и каналах бот не отвечает
    chat_id = chat["id"]
    user = msg.get("from", {})
    text = (msg.get("text") or "").strip()
    if not text:
        tg.send_message(chat_id, "Я понимаю только текстовые сообщения 🙂", main_keyboard())
        return
    logger.info("Сообщение от %s: %s", chat_id, text[:100])

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start":
            ai_sessions.pop(chat_id, None)
            show_start(chat_id, user)
        elif cmd == "/cancel":
            ai_sessions.pop(chat_id, None)
            t, kb = order_form.cancel(chat_id)
            tg.send_message(chat_id, t, kb)
        elif cmd == "/myid":
            tg.send_message(chat_id, f"Ваш Telegram ID: <code>{chat_id}</code>")
        elif cmd in COMMANDS:
            if cmd != "/oasis":
                ai_sessions.pop(chat_id, None)
            COMMANDS[cmd](chat_id, user)
        else:
            show_help(chat_id)
        return

    if order_form.is_filling(chat_id):
        t, kb = order_form.process_text(chat_id, text)
        if t:
            tg.send_message(chat_id, t, kb)
            return

    if chat_id in ai_sessions:
        ai_message(chat_id, text)
        return

    handler = TEXT_SHORTCUTS.get(text.lower())
    if handler:
        handler(chat_id, user)
        return

    rows = [manager_row(), back_row()]
    if ai.is_available():
        rows.insert(0, [{"text": "🤖 Спросить ассистента", "callback_data": "oasis"}])
        reply = "Хотите задать вопрос виртуальному ассистенту Oasis Dance или написать менеджеру?"
    else:
        reply = "Чтобы задать вопрос, напишите нашему менеджеру или выберите раздел в меню."
    tg.send_message(chat_id, reply, {"inline_keyboard": rows})


def on_callback(cq):
    tg.answer_callback(cq["id"])
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    data = cq.get("data") or ""
    user = cq.get("from", {})
    if not chat_id:
        return
    if data == "order" or data.startswith("order_"):
        ai_sessions.pop(chat_id, None)
        t, kb = order_form.handle_callback(chat_id, data, user)
        tg.send_message(chat_id, t, kb)
        return
    CALLBACKS.get(data, show_start)(chat_id, user)


def run():
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("Не задан TELEGRAM_BOT_TOKEN в файле .env")
        sys.exit(1)

    me = tg.call("getMe")
    if not (me and me.get("ok")):
        logger.error("Не удалось подключиться к Telegram (проверьте токен и интернет на сервере)")
        sys.exit(1)
    logger.info("Бот @%s запущен. ИИ-ассистент: %s. Администраторы: %s",
                me["result"]["username"], "включён" if ai.is_available() else "выключен",
                config.ADMIN_CHAT_IDS or "не заданы")

    # Если где-то был настроен webhook, getUpdates не работает — снимаем его
    tg.call("deleteWebhook", {"drop_pending_updates": False})

    offset = None
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            resp = tg.call("getUpdates", params, timeout=40)
            if not resp or not resp.get("ok"):
                if resp and resp.get("error_code") == 409:
                    logger.error("Конфликт 409: бот запущен ещё где-то (например, в Replit). Остановите копию.")
                time.sleep(5)
                continue
            for upd in resp["result"]:
                offset = upd["update_id"] + 1
                try:
                    if "callback_query" in upd:
                        on_callback(upd["callback_query"])
                    elif "message" in upd:
                        on_message(upd["message"])
                except Exception:
                    logger.error("Ошибка обработки обновления:\n%s", traceback.format_exc())
        except KeyboardInterrupt:
            logger.info("Остановлено")
            break
        except Exception:
            logger.error("Ошибка цикла:\n%s", traceback.format_exc())
            time.sleep(5)


if __name__ == "__main__":
    run()
