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


# ---------- меню менеджера (только для администраторов) ----------

ABTN_ORDERS = "📥 Заявки"
ABTN_CAL = "📅 Календарь"
ABTN_POST = "📢 Пост в канал"
ABTN_SOURCES = "🔗 Соцсети → канал"
ABTN_STATS = "📊 Статистика"
ABTN_SETTINGS = "⚙️ Настройки"
ABTN_TO_CLIENT = "👀 Как видит клиент"
BTN_TO_MANAGER = "🧑‍💼 Вернуться в меню менеджера"

ADMIN_BUTTONS = {ABTN_ORDERS: "adm_menu", ABTN_CAL: "adm_cal", ABTN_POST: "ch_menu", ABTN_SOURCES: "src_menu",
                 ABTN_STATS: "adm_stats", ABTN_SETTINGS: "adm_settings"}

MANAGER_REPLY_KB = {
    "keyboard": [[{"text": ABTN_ORDERS}, {"text": ABTN_CAL}],
                 [{"text": ABTN_POST}, {"text": ABTN_SOURCES}],
                 [{"text": ABTN_STATS}, {"text": ABTN_SETTINGS}],
                 [{"text": ABTN_TO_CLIENT}]],
    "resize_keyboard": True,
    "is_persistent": True,
    "input_field_placeholder": "Меню менеджера",
}

def manager_kb(split: bool):
    """Меню менеджера; в отдельном боте менеджера кнопка «Как видит клиент» не нужна."""
    if not split:
        return MANAGER_REPLY_KB
    return {**MANAGER_REPLY_KB, "keyboard": MANAGER_REPLY_KB["keyboard"][:-1]}


# Клиентское меню, которое видит менеджер в режиме проверки, — с кнопкой возврата
CLIENT_PREVIEW_KB = {**MAIN_REPLY_KB,
                     "keyboard": MAIN_REPLY_KB["keyboard"] + [[{"text": BTN_TO_MANAGER}]]}


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
