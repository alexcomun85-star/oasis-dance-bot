"""Минимальный клиент Telegram Bot API на requests."""
import logging
import time

import requests

import config

logger = logging.getLogger(__name__)

API_URL = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/"
MAX_LEN = 4096

_session = requests.Session()


def call(method: str, params: dict = None, timeout: int = 15):
    """Вызывает метод Bot API. Возвращает JSON-ответ или None при ошибке."""
    try:
        r = _session.post(API_URL + method, json=params or {}, timeout=timeout)
        data = r.json()
        if not data.get("ok"):
            logger.error("Telegram %s: %s", method, data.get("description"))
        return data
    except Exception as e:  # сеть, таймаут, неверный JSON
        logger.error("Ошибка запроса Telegram %s: %s", method, e)
        return None


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
        # Если HTML-разметка не разобралась — отправляем как обычный текст
        if result and not result.get("ok") and parse_mode and "parse" in str(result.get("description", "")).lower():
            params.pop("parse_mode", None)
            result = call("sendMessage", params)
        time.sleep(0.05)
    return result


def answer_callback(callback_id: str):
    return call("answerCallbackQuery", {"callback_query_id": callback_id})


def typing(chat_id):
    return call("sendChatAction", {"chat_id": chat_id, "action": "typing"})
