"""
Публикация постов в Telegram-канал из бота (только для администраторов).
Пост: свой (текст, фото, видео, документ) или черновик от ИИ → превью → публикация с кнопкой «Заказать».
"""
import html
import logging
import threading

import ai
import config
import storage
import tg

logger = logging.getLogger(__name__)

# Состояние подготовки поста по администратору:
# {"stage": "content"|"topic"|"preview", "src": message_id превью, "topic": str, "button": bool}
_posts = {}
_lock = threading.Lock()

CANCEL_ROW = [{"text": "❌ Отмена", "callback_data": "ch_cancel"}]


def bot_link(payload="ch_order"):
    return f"https://t.me/{config.BOT_USERNAME}?start={payload}" if config.BOT_USERNAME else config.MANAGER_URL


def order_button():
    return {"inline_keyboard": [[{"text": "🎭 Заказать выступление", "url": bot_link()}]]}


def _get(admin_id):
    with _lock:
        return _posts.get(admin_id)


def _set(admin_id, state):
    with _lock:
        if state is None:
            _posts.pop(admin_id, None)
        else:
            _posts[admin_id] = state


def active(admin_id) -> bool:
    st = _get(admin_id)
    return bool(st) and st["stage"] in ("content", "topic")


def cancel(admin_id):
    _set(admin_id, None)


# ---------- экраны ----------

def status_text() -> str:
    """Проверяет, может ли бот публиковать в канал."""
    me = tg.call("getMe") or {}
    bot_id = (me.get("result") or {}).get("id")
    r = tg.call("getChatMember", {"chat_id": config.CHANNEL_ID, "user_id": bot_id}) if bot_id else None
    res = (r or {}).get("result") or {}
    if not (r and r.get("ok")):
        return (f"⚠️ Не удаётся проверить канал {html.escape(config.CHANNEL_ID)}: "
                f"{html.escape(str((r or {}).get('description', 'нет ответа')))}")
    can_post = res.get("status") == "creator" or res.get("can_post_messages")
    can_pin = res.get("status") == "creator" or res.get("can_pin_messages") or res.get("can_edit_messages")
    return (f"Канал: {html.escape(config.CHANNEL_ID)}\n"
            f"Публикация постов: {'✅ можно' if can_post else '❌ нет права «Публикация сообщений»'}\n"
            f"Закрепление: {'✅ можно' if can_pin else '❌ нет права'}")


def menu_screen():
    text = ("📢 <b>Пост в канал</b>\n\n" + status_text() + "\n\n"
            "Пришлите пост одним сообщением — текст, фото или видео с подписью. "
            "Или нажмите «Написать с ИИ» и укажите тему.\n\n"
            "Перед публикацией покажу, как пост будет выглядеть.")
    return text, {"inline_keyboard": [[{"text": "✨ Написать с ИИ", "callback_data": "ch_ai"}],
                                      [{"text": "🗂 Кабинет", "callback_data": "adm_menu"}, *CANCEL_ROW]]}


def start(admin_id):
    _set(admin_id, {"stage": "content", "button": True})
    return menu_screen()


def _confirm_kb(st):
    btn = "🔘 Кнопка «Заказать»: есть" if st.get("button", True) else "⚪ Кнопка «Заказать»: нет"
    rows = [[{"text": "✅ Опубликовать", "callback_data": "ch_pub"}],
            [{"text": btn, "callback_data": "ch_btn"}]]
    if st.get("topic"):
        rows.append([{"text": "🔁 Другой вариант", "callback_data": "ch_regen"}])
    rows.append([{"text": "✏️ Прислать другой пост", "callback_data": "ch_new"}, *CANCEL_ROW])
    return {"inline_keyboard": rows}


def _preview(admin_id, st, src_chat, src_msg):
    """Показывает пост так, как он будет выглядеть в канале, и спрашивает подтверждение."""
    params = {"chat_id": admin_id, "from_chat_id": src_chat, "message_id": src_msg}
    if st.get("button", True):
        params["reply_markup"] = order_button()
    r = tg.call("copyMessage", params)
    if not (r and r.get("ok")):
        tg.send_message(admin_id, "⚠️ Не удалось подготовить превью. Пришлите пост ещё раз.",
                        {"inline_keyboard": [CANCEL_ROW]})
        return
    st.update(stage="preview", src=r["result"]["message_id"])
    _set(admin_id, st)
    tg.send_message(admin_id, "👆 Так пост будет выглядеть в канале. Публикуем?", _confirm_kb(st))


def _draft(admin_id, st):
    tg.typing(admin_id)
    try:
        text = ai.generate_post(st["topic"])
    except Exception as e:
        logger.error("ИИ не написал пост: %s", e)
        tg.send_message(admin_id, "⚠️ ИИ сейчас не отвечает. Пришлите текст поста сами.",
                        {"inline_keyboard": [CANCEL_ROW]})
        st["stage"] = "content"
        _set(admin_id, st)
        return
    r = tg.send_message(admin_id, html.escape(text))
    if r and r.get("ok"):
        _preview(admin_id, st, admin_id, r["result"]["message_id"])


# ---------- входящие от администратора ----------

def on_message(admin_id, msg) -> bool:
    """Обрабатывает сообщение администратора, если он готовит пост. True — сообщение использовано."""
    st = _get(admin_id)
    if not st or st["stage"] not in ("content", "topic"):
        return False
    text = (msg.get("text") or "").strip()
    if text.lower() in ("/cancel", "отмена"):
        cancel(admin_id)
        tg.send_message(admin_id, "Публикация отменена.")
        return True
    if text.startswith("/"):
        return False  # другие команды работают как обычно
    if st["stage"] == "topic":
        if not text:
            tg.send_message(admin_id, "Напишите тему поста текстом.")
            return True
        st["topic"] = text[:500]
        _draft(admin_id, st)
        return True
    if msg.get("media_group_id"):
        tg.send_message(admin_id, "Альбомы из нескольких фото пока не поддерживаются — пришлите одно фото или видео с подписью.")
        return True
    st.pop("topic", None)
    _preview(admin_id, st, admin_id, msg["message_id"])
    return True


def handle_callback(admin_id, data):
    """Кнопки ch_*. Возвращает (текст, клавиатура) для редактирования сообщения или None."""
    st = _get(admin_id) or {"button": True}
    if data.startswith("ch_pin_"):
        return pin(admin_id, int(data[7:]))
    if data == "ch_menu":
        return start(admin_id)
    if data == "ch_cancel":
        cancel(admin_id)
        return "Публикация отменена.", None
    if data == "ch_new":
        st.update(stage="content")
        st.pop("topic", None)
        _set(admin_id, st)
        return "Пришлите новый пост одним сообщением.", {"inline_keyboard": [CANCEL_ROW]}
    if data == "ch_ai":
        if not ai.is_available():
            return "ИИ не подключён (нет ключа Gemini). Пришлите текст поста сами.", {"inline_keyboard": [CANCEL_ROW]}
        st.update(stage="topic")
        _set(admin_id, st)
        return ("✨ О чём пост? Напишите тему и детали, например:\n"
                "«Поздравление с Новым годом и приглашение бронировать корпоративы на декабрь»"), \
            {"inline_keyboard": [CANCEL_ROW]}
    if st.get("stage") != "preview":
        return "Пост не найден — начните заново: /post", None
    if data == "ch_regen" and st.get("topic"):
        _draft(admin_id, st)
        return "Готовлю другой вариант…", None
    if data == "ch_btn":
        st["button"] = not st.get("button", True)
        _set(admin_id, st)
        # Обновляем кнопку и под превью
        tg.call("editMessageReplyMarkup", {"chat_id": admin_id, "message_id": st["src"],
                                           "reply_markup": order_button() if st["button"] else {"inline_keyboard": []}})
        return "👆 Так пост будет выглядеть в канале. Публикуем?", _confirm_kb(st)
    if data == "ch_pub":
        params = {"chat_id": config.CHANNEL_ID, "from_chat_id": admin_id, "message_id": st["src"]}
        if st.get("button", True):
            params["reply_markup"] = order_button()
        r = tg.call("copyMessage", params)
        if not (r and r.get("ok")):
            return ("⚠️ Не удалось опубликовать: "
                    f"{html.escape(str((r or {}).get('description', 'нет ответа')))}\n\n"
                    "Проверьте, что бот — администратор канала с правом «Публикация сообщений».",
                    _confirm_kb(st))
        post_id = r["result"]["message_id"]
        cancel(admin_id)
        storage.log_event(admin_id, "post")
        link = f"https://t.me/{config.CHANNEL_ID.lstrip('@')}/{post_id}" if config.CHANNEL_ID.startswith("@") else ""
        text = "✅ Пост опубликован." + (f" <a href=\"{link}\">Открыть</a>" if link else "")
        return text, {"inline_keyboard": [[{"text": "📌 Закрепить в канале", "callback_data": f"ch_pin_{post_id}"}],
                                          [{"text": "📢 Ещё пост", "callback_data": "ch_menu"},
                                           {"text": "🗂 Кабинет", "callback_data": "adm_menu"}]]}
    return None


def pin(admin_id, post_id: int):
    r = tg.call("pinChatMessage", {"chat_id": config.CHANNEL_ID, "message_id": post_id,
                                   "disable_notification": True})
    if r and r.get("ok"):
        return "📌 Пост закреплён в канале.", {"inline_keyboard": [[{"text": "🗂 Кабинет", "callback_data": "adm_menu"}]]}
    return (f"⚠️ Не удалось закрепить: {html.escape(str((r or {}).get('description', 'нет ответа')))}",
            {"inline_keyboard": [[{"text": "🗂 Кабинет", "callback_data": "adm_menu"}]]})
