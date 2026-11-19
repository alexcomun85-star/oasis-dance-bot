"""Минимальный клиент Telegram Bot API на requests."""
import logging
import threading

import requests

import config

logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/"
MAX_LEN = 4096

_local = threading.local()


def _session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
    return _local.s


def call(method: str, params: dict = None, timeout: int = 15):
    """Вызывает метод Bot API. Возвращает JSON-ответ или None при сетевой ошибке."""
    try:
        r = _session().post(API_URL + method, json=params or {}, timeout=timeout)
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


def answer_callback(callback_id: str, text: str = None):
    params = {"callback_query_id": callback_id}
    if text:
        params["text"] = text
    return call("answerCallbackQuery", params)


def typing(chat_id):
    return call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
