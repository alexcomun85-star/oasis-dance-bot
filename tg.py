"""
Минимальный клиент Telegram Bot API на requests.
Поддерживает два бота: клиентский и (необязательно) отдельный бот менеджера.
Какой бот отправляет сообщение, определяется контекстом: tg.via("admin") / tg.via("client").
"""
import contextlib
import contextvars
import logging
import threading

import requests

import config

logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/"  # клиентский бот
_TOKENS = {"client": config.TELEGRAM_BOT_TOKEN, "admin": config.ADMIN_BOT_TOKEN or config.TELEGRAM_BOT_TOKEN}
_current = contextvars.ContextVar("tg_bot", default="client")
MAX_LEN = 4096


def split_mode() -> bool:
    """True, если у менеджера свой отдельный бот (свой чат)."""
    return bool(config.ADMIN_BOT_TOKEN) and config.ADMIN_BOT_TOKEN != config.TELEGRAM_BOT_TOKEN


def current() -> str:
    return _current.get()


@contextlib.contextmanager
def via(bot: str):
    """Отправлять от имени бота: "admin" — бот менеджера, "client" — клиентский бот."""
    token = _current.set(bot)
    try:
        yield
    finally:
        _current.reset(token)


def _url(method: str) -> str:
    return f"https://api.telegram.org/bot{_TOKENS.get(_current.get(), config.TELEGRAM_BOT_TOKEN)}/{method}"

_local = threading.local()


def _session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def call(method: str, params: dict = None, timeout: int = 15):
    """Вызывает метод Bot API. Возвращает JSON-ответ или None при сетевой ошибке."""
    try:
        r = _session().post(_url(method), json=params or {}, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            desc = data.get("description", "")
            if "message is not modified" not in desc:
                logger.warning("Telegram %s: %s", method, desc)
        return data
    except Exception as e:
        logger.error("Ошибка запроса Telegram %s: %s", method, e)
        return None


def _is_parse_error(result) -> bool:
    return bool(result) and not result.get("ok") and "parse" in str(result.get("description", "")).lower()


def send_message(chat_id, text: str, reply_markup: dict = None, parse_mode: str = "HTML"):
    """Отправляет сообщение; длинный текст делит на части по 4096 символов."""
    parts = [text[i:i + MAX_LEN] for i in range(0, len(text), MAX_LEN)] or [""]
    result = None
    for i, part in enumerate(parts):
        params = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
        if parse_mode:
            params["parse_mode"] = parse_mode
        if reply_markup and i == len(parts) - 1:
            params["reply_markup"] = reply_markup
        result = call("sendMessage", params)
        if _is_parse_error(result):
            params.pop("parse_mode", None)
            result = call("sendMessage", params)
    return result


def edit_message(chat_id, message_id, text: str, reply_markup: dict = None):
    """Меняет текст уже отправленного сообщения. Если не получилось — отправляет новое."""
    if message_id and len(text) <= MAX_LEN:
        params = {"chat_id": chat_id, "message_id": message_id, "text": text,
                  "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup:
            params["reply_markup"] = reply_markup
        r = call("editMessageText", params)
        if r and (r.get("ok") or "not modified" in str(r.get("description", ""))):
            return r
    return send_message(chat_id, text, reply_markup)


_answered = set()
_answered_lock = threading.Lock()


def answer_callback(callback_id: str, text: str = None):
    """Убирает «часики» на кнопке. Повторный ответ на ту же кнопку не отправляется."""
    with _answered_lock:
        if callback_id in _answered:
            return None
        _answered.add(callback_id)
        if len(_answered) > 5000:
            _answered.clear()
    params = {"callback_query_id": callback_id}
    if text:
        params["text"] = text
    return call("answerCallbackQuery", params)


def typing(chat_id):
    return call("sendChatAction", {"chat_id": chat_id, "action": "typing"})


def send_document(chat_id, filename: str, data: bytes, caption: str = ""):
    """Отправляет файл (например, выгрузку заявок)."""
    try:
        r = _session().post(_url("sendDocument"), data={"chat_id": chat_id, "caption": caption},
                            files={"document": (filename, data)}, timeout=60)
        return r.json()
    except Exception as e:
        logger.error("Ошибка отправки файла: %s", e)
        return None


def send_photo(chat_id, photo, caption: str = "", reply_markup: dict = None):
    """photo — file_id/URL (строка) или путь к файлу на сервере (начинается с /)."""
    params = {"chat_id": chat_id, "caption": caption, "parse_mode": "HTML"}
    if reply_markup:
        params["reply_markup"] = reply_markup
    if isinstance(photo, str) and not photo.startswith("/"):
        params["photo"] = photo
        return call("sendPhoto", params, timeout=60)
    try:
        import json as _json
        if reply_markup:
            params["reply_markup"] = _json.dumps(reply_markup)
        with open(photo, "rb") as f:
            r = _session().post(_url("sendPhoto"), data=params, files={"photo": f}, timeout=60)
        return r.json()
    except Exception as e:
        logger.error("Ошибка отправки фото: %s", e)
        return None
