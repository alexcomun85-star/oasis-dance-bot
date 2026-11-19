#!/usr/bin/env python3
"""
Telegram-бот шоу-балета Oasis Dance (@oasisdance_show_bot).
Запуск: python3 bot.py  (настройки — в файле .env)
"""
import html
import logging
import os
import sys
import threading
import time
import traceback
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
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


class _RedactSecrets(logging.Filter):
    """Убирает токен бота и ключ Gemini из любых строк журнала (сетевые ошибки содержат адрес запроса)."""
    def filter(self, record):
        secrets = [s for s in (config.TELEGRAM_BOT_TOKEN, config.GEMINI_API_KEY, config.VK_SERVICE_TOKEN,
                               config.AI_FALLBACK_KEY) if s]
        if secrets:
            msg = record.getMessage()
            for secret in secrets:
                msg = msg.replace(secret, "***")
            record.msg, record.args = msg, None
        return True


for _h in logging.getLogger().handlers:
    _h.addFilter(_RedactSecrets())

import admin  # noqa: E402
import channel  # noqa: E402
import sources  # noqa: E402
import ai  # noqa: E402
import order_form  # noqa: E402
import storage  # noqa: E402
import tg  # noqa: E402

REMOVE_KB = {"remove_keyboard": True}

# Клиенты, которые нажали «Перезвоните мне» и ещё не прислали номер
_callback_wait = set()
_state_lock = threading.Lock()


# ---------- отправка набора сообщений ----------

def render(chat_id, msgs, edit_id=None):
    """msgs — список (текст, клавиатура). Первое сообщение по возможности редактирует edit_id."""
    for i, (text, kb) in enumerate(msgs or []):
        inline_or_none = kb is None or "inline_keyboard" in kb
        if i == 0 and edit_id and inline_or_none:
            tg.edit_message(chat_id, edit_id, text, kb)
        else:
            tg.send_message(chat_id, text, kb)


# ---------- главное меню и разделы ----------

def main_keyboard(chat_id=None):
    rows = [
        [{"text": "🎭 Заказать выступление", "callback_data": "order"}],
        [{"text": "📞 Перезвоните мне", "callback_data": "callme"}],
    ]
    if ai.is_available():
        rows.append([{"text": "💬 Задать вопрос", "callback_data": "ask"}])
    rows += [
        [{"text": "🎬 Видео и фото", "callback_data": "video"},
         {"text": "📱 Контакты", "callback_data": "contact"}],
        [{"text": "👩‍💼 Сотрудничество", "callback_data": "partnership"}],
    ]
    if chat_id and admin.is_admin(chat_id):
        rows.append([{"text": "🗂 Кабинет заявок", "callback_data": "adm_menu"}])
    return {"inline_keyboard": rows}


def back_kb(*extra_rows):
    return {"inline_keyboard": [*extra_rows, [{"text": "🏠 Главное меню", "callback_data": "main_menu"}]]}


def manager_row(text="✉️ Написать менеджеру"):
    return [{"text": text, "url": config.MANAGER_URL}]


def start_screen(chat_id, user):
    name = html.escape(user.get("first_name") or "")
    text = (f"👋 Здравствуйте{', ' + name if name else ''}!\n\n"
            "Это бот шоу-балета <b>Oasis Dance</b> — шоу-балета премиум-класса.\n\n"
            "🎭 Оставьте заявку на выступление — это займёт минуту\n"
            "📞 Или оставьте номер, и менеджер перезвонит\n")
    if ai.is_available():
        text += "💬 А на вопросы ответит ассистент — просто напишите сообщение\n"
    text += "\nВыберите, что вас интересует:"
    return [(text, main_keyboard(chat_id))]


def help_screen(chat_id):
    text = ("📌 <b>Что умеет бот</b>\n\n"
            "/order — заказать выступление\n"
            "/callme — попросить перезвонить\n"
            "/video — видео и фото выступлений\n"
            "/contact — контакты\n"
            "/privacy — обработка персональных данных\n"
            "/cancel — отменить текущее действие\n")
    if ai.is_available():
        text += "\nЛюбой вопрос можно просто написать сообщением — ответит ассистент."
    if admin.is_admin(chat_id):
        text += ("\n\n<b>Для администратора:</b>\n/admin — кабинет заявок\n/orders — новые заявки\n"
                 "/stats — статистика\n/post — пост в канал\n/sources — соцсети → канал\n"
                 "Перешлите боту любой пост — он подготовит его для канала.")
    return [(text, back_kb())]


def video_screen():
    return [("🎬 <b>Видео и фото выступлений</b>\n\n"
             "Смотрите наши номера в соцсетях:",
             back_kb([{"text": "👥 ВКонтакте", "url": config.VK_URL}],
                     [{"text": "📢 Telegram-канал", "url": config.CHANNEL_URL}],
                     [{"text": "📸 Instagram", "url": config.INSTAGRAM_URL}]))]


def contact_screen():
    lines = ["📱 <b>Контакты</b>\n",
             "Менеджер ответит на вопросы о программах, свободных датах и стоимости.\n",
             f"✉️ Telegram: @{config.MANAGER_USERNAME}"]
    if config.MANAGER_PHONE:
        lines.append(f"☎️ Телефон: {config.MANAGER_PHONE}")
    if config.MANAGER_EMAIL:
        lines.append(f"📧 Email: {config.MANAGER_EMAIL}")
    if config.WORK_HOURS:
        lines.append(f"\n🕙 {config.WORK_HOURS}")
    return [("\n".join(lines), back_kb(manager_row(), [{"text": "📞 Перезвоните мне", "callback_data": "callme"}]))]


def partnership_screen():
    return [("👩‍💼 <b>Сотрудничество</b>\n\n"
             "Мы открыты для сотрудничества с ивент-агентствами, площадками, ресторанами и организаторами. "
             "Напишите менеджеру — обсудим условия.", back_kb(manager_row()))]


def ask_screen():
    if not ai.is_available():
        return [("Напишите ваш вопрос менеджеру — он ответит.", back_kb(manager_row()))]
    return [("💬 Напишите ваш вопрос обычным сообщением — ассистент ответит.\n\n"
             "Например: «Какие программы есть на свадьбу?» или «Выступаете ли вы в другом городе?»", back_kb())]


def privacy_screen():
    op = html.escape(config.OPERATOR_NAME)
    text = ("🔒 <b>Обработка персональных данных</b>\n\n"
            f"Оператор: {op}.\n\n"
            "<b>Что мы получаем:</b> имя и имя пользователя Telegram, номер телефона (если вы его отправили), "
            "сведения о мероприятии из заявки, вопросы ассистенту.\n\n"
            "<b>Зачем:</b> чтобы связаться с вами по заявке и подготовить предложение. "
            "Данные не передаются третьим лицам для рекламы.\n\n"
            "<b>Ассистент:</b> вопросы, которые вы пишете ассистенту, обрабатываются сервисом Google Gemini. "
            "Не указывайте в них лишние личные данные.\n\n"
            "<b>Хранение:</b> заявки хранятся, пока нужны для работы с вами; технический журнал бота "
            "не содержит текстов ваших сообщений и перезаписывается.\n\n"
            "<b>Удаление:</b> команда /forget удалит ваши данные из бота. Уже полученные менеджером "
            "уведомления о заявке удалите, попросив его об этом.")
    return [(text, back_kb())]


# ---------- «Перезвоните мне» ----------

def callme_start(chat_id):
    if storage.too_many_submits(chat_id):
        return order_form.LIMIT_MSG
    with _state_lock:
        _callback_wait.add(chat_id)
    return [("📞 <b>Перезвоним вам</b>\n\nНажмите кнопку «Отправить номер» внизу экрана "
             "или напишите номер сообщением." + order_form.consent_line(),
             {"keyboard": [[{"text": order_form.BTN_SEND_PHONE, "request_contact": True}],
                           [{"text": order_form.BTN_CANCEL}]],
              "resize_keyboard": True, "one_time_keyboard": True})]


def callme_finish(chat_id, user, phone, name=None):
    with _state_lock:
        _callback_wait.discard(chat_id)
    u = {k: user.get(k) for k in ("id", "username", "first_name", "last_name")}
    data = {"contact_phone": phone,
            "contact_name": name or " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x)}
    oid = storage.add_order(chat_id, "callback", u, data)
    storage.log_event(chat_id, "callback")
    sent = admin.notify_new(oid)
    text = "✅ Спасибо! Менеджер перезвонит вам в ближайшее время."
    if not sent:
        text += f"\nЕсли срочно — напишите @{config.MANAGER_USERNAME}."
    return [(text, REMOVE_KB), ("Пока ждёте звонка — посмотрите наши выступления 👇", main_keyboard(chat_id))]


def waiting_callback(chat_id) -> bool:
    with _state_lock:
        return chat_id in _callback_wait


# ---------- ИИ-ассистент ----------

AI_KB = {"inline_keyboard": [
    [{"text": "🎭 Заказать выступление", "callback_data": "order"},
     {"text": "📞 Перезвоните мне", "callback_data": "callme"}],
    [{"text": "🏠 Меню", "callback_data": "main_menu"}],
]}


def ai_answer(chat_id, text):
    if not ai.is_available():
        return [("Чтобы задать вопрос, напишите менеджеру или выберите раздел в меню.",
                 back_kb(manager_row()))]
    hour_ago, day_ago = time.time() - 3600, time.time() - 86400
    if (storage.count_events("ai", hour_ago, chat_id) >= config.AI_LIMIT_PER_USER_HOUR
            or storage.count_events("ai", day_ago) >= config.AI_LIMIT_PER_DAY):
        return [("Ассистент сейчас отдыхает 🙂 Напишите, пожалуйста, менеджеру — он ответит.",
                 back_kb(manager_row()))]
    history = storage.get_history(chat_id)
    stop = threading.Event()

    def keep_typing():
        while not stop.is_set():
            tg.typing(chat_id)
            stop.wait(4)
    threading.Thread(target=keep_typing, daemon=True).start()
    try:
        answer = ai.generate(text, history)
    except Exception as e:
        logger.error("Ассистент не ответил: %s", e)
        return [("Извините, ассистент сейчас не может ответить. Напишите менеджеру — он поможет.",
                 back_kb(manager_row()))]
    finally:
        stop.set()
    storage.log_event(chat_id, "ai")
    storage.save_history(chat_id, history + [{"role": "user", "content": text},
                                             {"role": "assistant", "content": answer}])
    return [(html.escape(answer.replace("**", "")), AI_KB)]


# ---------- обработка входящих ----------

def cancel_all(chat_id):
    with _state_lock:
        _callback_wait.discard(chat_id)
    admin.cancel_reply(chat_id)
    admin.client_cancel(chat_id)
    if order_form.is_filling(chat_id):
        return order_form.cancel(chat_id)
    return [("Отменено.", REMOVE_KB), ("Чем ещё помочь?", main_keyboard(chat_id))]


def on_command(chat_id, user, cmd):
    if cmd == "/start":
        admin.cancel_reply(chat_id)
        channel.cancel(chat_id)
        return start_screen(chat_id, user)
    if cmd == "/cancel":
        channel.cancel(chat_id)
        return cancel_all(chat_id)
    if cmd == "/help":
        return help_screen(chat_id)
    if cmd == "/order":
        return order_form.start(chat_id, user)
    if cmd == "/callme":
        return callme_start(chat_id)
    if cmd == "/video":
        return video_screen()
    if cmd == "/contact":
        return contact_screen()
    if cmd == "/partnership":
        return partnership_screen()
    if cmd in ("/ask", "/oasis"):
        return ask_screen()
    if cmd == "/privacy":
        return privacy_screen()
    if cmd == "/forget":
        return [("Удалить ваши данные из бота (незаконченную заявку, историю вопросов ассистенту, "
                 "контактные данные в заявках)? Это нельзя отменить.",
                 {"inline_keyboard": [[{"text": "🗑 Да, удалить", "callback_data": "forget_yes"},
                                       {"text": "Нет", "callback_data": "main_menu"}]]})]
    if cmd == "/myid":
        return [(f"Ваш Telegram ID: <code>{chat_id}</code>", None)]
    if admin.is_admin(chat_id):
        if cmd == "/admin":
            return [admin.menu_screen()]
        if cmd == "/orders":
            return [admin.list_screen("new", 0)]
        if cmd == "/stats":
            return [admin.stats_screen()]
        if cmd == "/post":
            return [channel.start(chat_id)]
        if cmd == "/status":
            return [admin.status_screen()]
        if cmd == "/sources":
            return [sources.status_screen()]
    return help_screen(chat_id)


TEXT_SHORTCUTS = {"меню": "main_menu", "главное меню": "main_menu", "start": "main_menu",
                  "заказать": "order", "заказ": "order", "заказать шоу": "order",
                  "контакты": "contact", "видео": "video"}


def on_message(msg):
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return
    chat_id = chat["id"]
    user = msg.get("from", {})
    storage.touch_user(user)

    # Администратор готовит пост в канал (принимаем и фото, и видео)
    if admin.is_admin(chat_id) and channel.active(chat_id) and channel.on_message(chat_id, msg):
        return
    # Администратор переслал пост — готовим черновик для канала
    if (admin.is_admin(chat_id) and msg.get("forward_origin") and not admin.awaiting_reply(chat_id)
            and sources.from_forward(chat_id, msg)):
        return

    # Номер, отправленный кнопкой
    if msg.get("contact"):
        contact = dict(msg["contact"])
        if contact.get("user_id") != user.get("id"):
            contact["phone_number"] = f'{contact.get("phone_number", "")} (номер другого человека)'
        res = order_form.process_contact(chat_id, contact)
        if res is None and waiting_callback(chat_id):
            res = callme_finish(chat_id, user, contact["phone_number"], contact.get("first_name"))
        if res is None:
            res = [("Спасибо! Чтобы менеджер перезвонил, нажмите «Перезвоните мне».", main_keyboard(chat_id))]
        render(chat_id, res)
        return

    text = (msg.get("text") or "").strip()
    if not text:
        render(chat_id, [("Я понимаю текстовые сообщения 🙂 Выберите раздел:", main_keyboard(chat_id))])
        return
    logger.info("Сообщение от %s (%s симв.)", chat_id, len(text))

    if text.startswith("/"):
        cmd = text.split()[0].split("@")[0].lower()
        if cmd == "/start" and len(text.split()) > 1:
            storage.log_event(chat_id, "src_" + text.split()[1][:20])
        render(chat_id, on_command(chat_id, user, cmd))
        return

    # Администратор пишет ответ клиенту
    if admin.is_admin(chat_id) and admin.awaiting_reply(chat_id):
        admin.send_to_client(chat_id, text)
        return
    # Клиент отвечает менеджеру
    if admin.client_awaiting(chat_id):
        if admin.forward_client_reply(chat_id, text):
            render(chat_id, [("✅ Передали менеджеру. Он ответит здесь же.", back_kb())])
        else:
            render(chat_id, [("⚠️ Не удалось передать сообщение. Напишите менеджеру напрямую.",
                              back_kb(manager_row()))])
        return
    # Заполнение заявки
    if order_form.is_filling(chat_id):
        res = order_form.process_text(chat_id, text)
        if res:
            render(chat_id, res)
            return
    # Ждём номер для обратного звонка
    if waiting_callback(chat_id):
        if text == order_form.BTN_CANCEL or text.lower() in ("отмена", "отменить"):
            render(chat_id, cancel_all(chat_id))
            return
        phone = order_form._parse_phone(text)
        if phone:
            render(chat_id, callme_finish(chat_id, user, phone))
        else:
            render(chat_id, [("⚠️ Не похоже на номер. Нажмите «Отправить номер» или напишите номер, "
                              "например +7 900 123-45-67. Отмена — /cancel", None)])
        return

    key = TEXT_SHORTCUTS.get(text.lower())
    if key:
        render(chat_id, on_callback_data(chat_id, user, key))
        return
    render(chat_id, ai_answer(chat_id, text))


def on_callback_data(chat_id, user, data):
    """Кнопки клиента. Возвращает список сообщений."""
    if data == "main_menu":
        return start_screen(chat_id, user)
    if data == "order":
        return order_form.start(chat_id, user)
    if data == "callme":
        return callme_start(chat_id)
    if data == "ask":
        return ask_screen()
    if data == "video":
        return video_screen()
    if data == "contact":
        return contact_screen()
    if data == "partnership":
        return partnership_screen()
    if data == "forget_yes":
        storage.forget_user(chat_id)
        return [("🗑 Ваши данные удалены. Если захотите вернуться — нажмите /start.", None)]
    return start_screen(chat_id, user)


def on_callback(cq):
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")
    data = cq.get("data") or ""
    user = cq.get("from", {})
    if not chat_id or (msg.get("chat") or {}).get("type") != "private" or chat_id != user.get("id"):
        tg.answer_callback(cq["id"])
        return
    storage.touch_user(user)

    if data.startswith("adm_"):
        if not admin.is_admin(chat_id):
            tg.answer_callback(cq["id"], "Недоступно")
            return
        tg.answer_callback(cq["id"])
        res = admin.handle_callback(chat_id, message_id, data)
        if res:
            tg.edit_message(chat_id, message_id, *res)
        return

    if data.startswith("ch_"):
        if not admin.is_admin(chat_id):
            tg.answer_callback(cq["id"], "Недоступно")
            return
        tg.answer_callback(cq["id"])
        res = channel.handle_callback(chat_id, data)
        if res:
            tg.edit_message(chat_id, message_id, *res)
        return

    if data.startswith(("dr_", "src_")):
        if not admin.is_admin(chat_id):
            tg.answer_callback(cq["id"], "Недоступно")
            return
        if data == "dr_noop":
            tg.answer_callback(cq["id"])
            return
        tg.answer_callback(cq["id"], "Готовлю…" if data.startswith(("dr_regen", "src_bf", "src_check")) else None)
        note = sources.handle_callback(chat_id, message_id, data)
        if note:
            tg.send_message(chat_id, html.escape(note))
        return

    if data.startswith("cli_rep_"):
        tg.answer_callback(cq["id"])
        try:
            ok = admin.client_start_reply(chat_id, int(data[8:]))
        except ValueError:
            ok = False
        tg.send_message(chat_id, "✍️ Напишите ответ менеджеру одним сообщением. Отмена — /cancel"
                        if ok else "Эта переписка уже недоступна. Напишите менеджеру напрямую.")
        return

    tg.answer_callback(cq["id"])
    if data.startswith("of_"):
        render(chat_id, order_form.handle_callback(chat_id, data, user), message_id)
        return
    if data == "order" and order_form.is_filling(chat_id):
        render(chat_id, order_form.start(chat_id, user), message_id)
        return
    # Разделы меню открываем на месте текущего сообщения, чтобы не засорять чат
    edit = message_id if data not in ("callme",) else None
    render(chat_id, on_callback_data(chat_id, user, data), edit)


# ---------- профиль бота в Telegram ----------

USER_COMMANDS = [
    ("start", "Главное меню"), ("order", "Заказать выступление"), ("callme", "Перезвоните мне"),
    ("video", "Видео и фото"), ("contact", "Контакты"), ("privacy", "Персональные данные"),
    ("help", "Помощь"),
]
ADMIN_COMMANDS = USER_COMMANDS + [("admin", "Кабинет заявок"), ("orders", "Новые заявки"),
                                  ("stats", "Статистика"), ("post", "Пост в канал"),
                                  ("sources", "Соцсети → канал"), ("status", "Что подключено")]
DESCRIPTION = ("Шоу-балет Oasis Dance — шоу-балет премиум-класса для свадеб, корпоративов и праздников.\n\n"
               "Здесь можно оставить заявку на выступление за минуту, попросить перезвонить "
               "или задать вопрос ассистенту.")
SHORT_DESCRIPTION = "Шоу-балет Oasis Dance: заявка на выступление за минуту 💃"


def setup_profile():
    def cmds(lst):
        return [{"command": c, "description": d} for c, d in lst]
    tg.call("setMyCommands", {"commands": cmds(USER_COMMANDS), "scope": {"type": "all_private_chats"}})
    for admin_id in config.ADMIN_CHAT_IDS:
        tg.call("setMyCommands", {"commands": cmds(ADMIN_COMMANDS), "scope": {"type": "chat", "chat_id": admin_id}})
    cur = tg.call("getMyDescription") or {}
    if (cur.get("result") or {}).get("description") != DESCRIPTION:
        tg.call("setMyDescription", {"description": DESCRIPTION})
    cur = tg.call("getMyShortDescription") or {}
    if (cur.get("result") or {}).get("short_description") != SHORT_DESCRIPTION:
        tg.call("setMyShortDescription", {"short_description": SHORT_DESCRIPTION})


# ---------- запуск ----------

_chat_locks = defaultdict(threading.Lock)


def _chat_of(upd):
    if "callback_query" in upd:
        return ((upd["callback_query"].get("message") or {}).get("chat") or {}).get("id")
    return (upd.get("message") or {}).get("chat", {}).get("id")


def handle_update(upd):
    # Сообщения одного чата обрабатываем по очереди, разных чатов — параллельно
    with _chat_locks[_chat_of(upd)]:
        try:
            if "callback_query" in upd:
                on_callback(upd["callback_query"])
            elif "message" in upd:
                on_message(upd["message"])
        except Exception:
            logger.error("Ошибка обработки обновления:\n%s", traceback.format_exc())


_last_poll = [time.time()]
WATCHDOG_SEC = 300


def watchdog():
    while True:
        time.sleep(30)
        if time.time() - _last_poll[0] > WATCHDOG_SEC:
            logger.error("Приём сообщений не отвечает %s сек — перезапуск", WATCHDOG_SEC)
            os._exit(1)  # systemd (Restart=always) поднимет бота заново


def background():
    next_sources = time.time() + 60  # первая проверка соцсетей через минуту после запуска
    while True:
        try:
            admin.check_abandoned()
        except Exception:
            logger.error("Ошибка фоновой проверки:\n%s", traceback.format_exc())
        if time.time() >= next_sources:
            next_sources = time.time() + config.SOURCE_CHECK_MINUTES * 60
            try:
                sources.check_all()
            except Exception:
                logger.error("Ошибка проверки соцсетей:\n%s", traceback.format_exc())
        time.sleep(60)


def run():
    if not config.TELEGRAM_BOT_TOKEN:
        logger.error("Не задан TELEGRAM_BOT_TOKEN в файле .env")
        sys.exit(1)
    me = tg.call("getMe")
    if not (me and me.get("ok")):
        logger.error("Не удалось подключиться к Telegram (проверьте токен и интернет на сервере)")
        sys.exit(1)

    imported = storage.migrate_jsonl(os.path.join(config.DATA_DIR, "orders.jsonl"))
    if imported:
        logger.info("Перенесено заявок из прошлой версии: %s", imported)
    config.BOT_USERNAME = me["result"]["username"]
    logger.info("Бот @%s запущен. ИИ-ассистент: %s. Администраторы: %s",
                me["result"]["username"], "включён" if ai.is_available() else "выключен",
                config.ADMIN_CHAT_IDS or "не заданы")
    tg.call("deleteWebhook", {"drop_pending_updates": False})
    try:
        setup_profile()
    except Exception:
        logger.error("Не удалось обновить профиль бота:\n%s", traceback.format_exc())
    threading.Thread(target=background, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()

    pool = ThreadPoolExecutor(max_workers=16)
    ack_pool = ThreadPoolExecutor(max_workers=4)  # мгновенно убирает «часики» на кнопках
    offset = None
    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message", "callback_query"]}
            if offset:
                params["offset"] = offset
            _last_poll[0] = time.time()
            resp = tg.call("getUpdates", params, timeout=40)
            if not resp or not resp.get("ok"):
                if resp and resp.get("error_code") == 409:
                    logger.error("Конфликт 409: бот запущен ещё где-то (например, в Replit). Остановите копию.")
                time.sleep(5)
                continue
            for upd in resp["result"]:
                offset = upd["update_id"] + 1
                if "callback_query" in upd:
                    ack_pool.submit(tg.answer_callback, upd["callback_query"]["id"])
                pool.submit(handle_update, upd)
        except KeyboardInterrupt:
            logger.info("Остановлено")
            break
        except Exception:
            logger.error("Ошибка цикла:\n%s", traceback.format_exc())
            time.sleep(5)


if __name__ == "__main__":
    run()
