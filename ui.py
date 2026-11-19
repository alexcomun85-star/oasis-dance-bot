"""
Общие элементы интерфейса: нижнее меню, тексты кнопок, каталог номеров.
"""
import html
import json
import os

import config

# ---------- нижнее меню (постоянная клавиатура) ----------

BTN_ORDER = "🎭 Заказать шоу"
BTN_DATE = "📅 Свободна ли дата?"
BTN_NUMBERS = "💃 Номера и видео"
BTN_ASK = "💬 Задать вопрос"
BTN_CALLME = "📞 Перезвоните мне"
BTN_MORE = "☰ Ещё"

MENU_BUTTONS = {BTN_ORDER: "order", BTN_DATE: "date", BTN_NUMBERS: "numbers",
                BTN_ASK: "ask", BTN_CALLME: "callme", BTN_MORE: "more"}

MAIN_REPLY_KB = {
    "keyboard": [[{"text": BTN_ORDER}, {"text": BTN_DATE}],
                 [{"text": BTN_NUMBERS}, {"text": BTN_ASK}],
                 [{"text": BTN_CALLME}, {"text": BTN_MORE}]],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Напишите вопрос или выберите кнопку",
}


def home_row():
    return [{"text": "🏠 В начало", "callback_data": "main_menu"}]


# ---------- каталог номеров ----------

NUMBERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "numbers.json")


def numbers():
    try:
        with open(NUMBERS_FILE, encoding="utf-8") as f:
            return json.load(f).get("numbers", [])
    except Exception:
        return []


def number_by_id(nid):
    return next((n for n in numbers() if n["id"] == nid), None)


def numbers_screen():
    items = numbers()
    if not items:
        return ("💃 <b>Номера и видео</b>\n\nКаталог скоро появится. Видео выступлений — в наших соцсетях.",
                {"inline_keyboard": [[{"text": "📢 Telegram-канал", "url": config.CHANNEL_URL}],
                                     [{"text": "👥 ВКонтакте", "url": config.VK_URL}], home_row()]})
    rows, row = [], []
    for n in items:
        row.append({"text": n["title"], "callback_data": f"num_{n['id']}"[:64]})
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([{"text": "👥 Все видео ВКонтакте", "url": config.VK_URL}])
    rows.append(home_row())
    return ("💃 <b>Номера и видео</b>\n\n"
            "Выберите номер — покажу описание и видео. Понравился? Нажмите «Хочу этот номер», "
            "и он попадёт в заявку.", {"inline_keyboard": rows})


def number_card(nid):
    n = number_by_id(nid)
    if not n:
        return numbers_screen()
    text = f"<b>{html.escape(n['title'])}</b>\n\n{html.escape(n['text'])}"
    rows = []
    if n.get("video"):
        label = "▶️ Смотреть видео"
        if "instagram.com" in n["video"] and config.INSTAGRAM_NOTE:
            label += " (Instagram*)"
            text += f"\n\n<i>* {html.escape(config.INSTAGRAM_NOTE)}</i>"
        rows.append([{"text": label, "url": n["video"]}])
    rows.append([{"text": "❤️ Хочу этот номер", "callback_data": f"want_{nid}"[:64]}])
    rows.append([{"text": "⬅️ Все номера", "callback_data": "numbers"}, *home_row()])
    return text, {"inline_keyboard": rows}
