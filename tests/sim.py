# Проверка диалогов бота без сети: python3 -I tests/sim.py (Telegram и Gemini заменены заглушками)
import os
import sys
import tempfile
import time
from datetime import date, timedelta

os.environ.update(TELEGRAM_BOT_TOKEN="123:TESTTOKEN", ADMIN_CHAT_IDS="111, @EbruBekker", DATA_DIR=tempfile.mkdtemp(),
                  GEMINI_API_KEY="GEMTESTKEY", AI_LIMIT_PER_USER_HOUR="3")
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tg  # noqa: E402

sent = []
_mid = [100]


def fake(method, params=None, timeout=15):
    params = params or {}
    sent.append((method, params))
    kb = params.get("reply_markup") or {}
    for row in kb.get("inline_keyboard", []):
        for b in row:
            if "callback_data" in b:
                assert len(b["callback_data"].encode()) <= 64, b
    _mid[0] += 1
    return {"ok": True, "result": {"message_id": _mid[0]}}


tg.call = fake
import admin, ai, bot, config, order_form, storage  # noqa: E402,E401

ai.generate = lambda m, h=None: "Ответ <тест> & **цена**"
ai.generate_post = lambda topic: f"Пост про {topic} <b>"
assert config.ADMIN_CHAT_IDS == [111]

U = {"id": 5, "first_name": "Аня", "username": "anya"}
A = {"id": 111, "first_name": "Админ"}


def msg(t, u=U, **extra):
    m = {"chat": {"id": u["id"], "type": "private"}, "from": u, "text": t}
    m.update(extra)
    bot.on_message(m)


def cb(d, u=U, mid=50):
    bot.on_callback({"id": "1", "from": u, "data": d, "message": {"chat": {"id": u["id"], "type": "private"}, "message_id": mid}})


def out(chat=None):
    return [p for m, p in sent if m in ("sendMessage", "editMessageText") and (chat is None or p["chat_id"] == chat)]


def last(chat=5):
    return out(chat)[-1]


def buttons(p):
    return [b.get("callback_data") for row in (p.get("reply_markup") or {}).get("inline_keyboard", []) for b in row]


# --- старт и меню
msg("/start")
assert "Аня" in last()["text"] and "adm_menu" not in buttons(last())
msg("/start", A)
assert "adm_menu" in buttons(last(111))
cb("video")
assert sent[-1][0] == "editMessageText", "меню должно редактироваться на месте"

# --- заявка
cb("order")
assert "Шаг 1" in last()["text"]
cb("of_opt_1")                                   # Корпоратив
assert "Шаг 2" in last()["text"]
nxt = date.today().replace(day=1) + timedelta(days=40)
cb(f"of_cal_{nxt.year}-{nxt.month:02d}")         # листаем календарь
d = nxt.replace(day=15)
cb(f"of_day_{d:%Y-%m-%d}")
assert "Шаг 3" in last()["text"]
msg("Москва, ресторан «Арбат»")
cb("of_opt_3")                                   # программа «помогите выбрать»
assert "Шаг 5" in last()["text"] and last()["reply_markup"]["keyboard"][0][0]["request_contact"]
msg("", contact={"phone_number": "+79990001122", "first_name": "Аня"})
assert out(5)[-2]["reply_markup"] == {"remove_keyboard": True}
assert "Шаг 6" in last()["text"]
cb("of_skip")
assert "Проверьте заявку" in last()["text"] and "персональных" in last()["text"]
cb("of_confirm")
assert "№1" in last()["text"]
card = last(111)
assert "Корпоратив" in card["text"] and "+79990001122" in card["text"] and "adm_st_1_work" in buttons(card)
assert storage.get_order(1)["status"] == "new"

# --- кабинет: статус и переписка
cb("adm_st_1_work", A, mid=card.get("message_id", 0))
assert storage.get_order(1)["status"] == "work"
cb("adm_msg_1", A)
msg("Здравствуйте! Свободны на эту дату <b>", A)
to_client = last(5)
assert "Сообщение от менеджера" in to_client["text"] and "&lt;b&gt;" in to_client["text"]
cb("cli_rep_1")
msg("Отлично, ждём предложение")
assert "Ответ клиента" in last(111)["text"]
cb("adm_st_1_done", U)                           # клиент не может менять статус
assert storage.get_order(1)["status"] == "work"
msg("/admin", A)
assert "Кабинет заявок" in last(111)["text"]
cb("adm_list_work_0", A)
assert "adm_open_1_work" in buttons(last(111))
cb("adm_open_1_work", A)
cb("adm_st_1_done", A)
assert storage.get_order(1)["status"] == "done"
msg("/stats", A)
assert "Статистика" in last(111)["text"]

# --- перезвоните мне
B = {"id": 6, "first_name": "Боря"}
cb("callme", B)
msg("123", B)
assert "Не похоже" in last(6)["text"]
msg("8 (900) 555-44-33", B)
assert "перезвонит" in out(6)[-2]["text"]
assert "Просьба перезвонить" in last(111)["text"]

# --- брошенная заявка
C = {"id": 7, "first_name": "Вера"}
cb("order", C)
cb("of_opt_0", C)
storage._q("UPDATE forms SET updated=? WHERE chat_id=7", (time.time() - 3600,), commit=True)
admin.check_abandoned()
assert "Незавершённая" in last(111)["text"]
ab = storage.list_orders(["new"])[0]
assert ab["kind"] == "abandoned"
cb("order", C)
cb("of_continue", C)
assert "Шаг 2" in last(7)["text"]
cb("of_nodate", C)
msg("Казань", C)
cb("of_opt_0", C)
msg("✉️ Лучше написать в Telegram", C)
msg("Нужна программа на 20 минут", C)
cb("of_confirm", C)
assert storage.get_order(ab["id"])["status"] == "merged"

# --- отмена формы и назад
cb("order", C)
cb("of_opt_0", C)
cb("of_back", C)
assert "Шаг 1" in last(7)["text"]
msg("/cancel", C)
assert not order_form.is_filling(7)

# --- ассистент и лимит
for i in range(3):
    msg(f"Вопрос {i}")
    assert last()["text"] == "Ответ &lt;тест&gt; &amp; цена"
msg("Ещё вопрос")
assert "отдыхает" in last()["text"]

# --- прочее
msg("/privacy")
assert "Google Gemini" in last()["text"]
msg("/forget")
cb("forget_yes")
assert storage.get_history(5) == []
bot.on_message({"chat": {"id": -100, "type": "supergroup"}, "from": U, "text": "/start"})
n = len(sent)
cb("adm_menu")                                    # не администратор
assert not any(m == "editMessageText" for m, _ in sent[n:])
bot.setup_profile()
assert any(m == "setMyCommands" for m, _ in sent)

# --- безопасность
import io, logging  # noqa: E401
buf = io.StringIO()
h = logging.StreamHandler(buf)
h.addFilter(bot._RedactSecrets())
logging.getLogger().addHandler(h)
logging.getLogger("t").error("url /bot123:TESTTOKEN/getMe key=GEMTESTKEY")
assert "/bot***/getMe" in buf.getvalue() and "key=***" in buf.getvalue(), buf.getvalue()

D = {"id": 8, "first_name": "Гена"}
n_orders = storage.count_orders()
msg("", D, contact={"phone_number": "+70000000000", "user_id": 999})       # номер без запроса
assert storage.count_orders() == n_orders and "Перезвоните мне" in last(8)["text"]
cb("callme", D)
msg("", D, contact={"phone_number": "+70000000001", "user_id": 999})       # чужой номер
assert "номер другого человека" in last(111)["text"]
for _ in range(5):
    cb("callme", D)
    msg("+7 900 000 00 00", D)
assert any("несколько заявок" in p["text"] for p in out(8))

n = len(sent)
bot.on_callback({"id": "1", "from": A, "data": "adm_menu",
                 "message": {"chat": {"id": -5, "type": "group"}, "message_id": 1}})
assert not any(m == "editMessageText" for m, _ in sent[n:]), "кнопки из групп не обрабатываются"

E = {"id": 9, "first_name": "Ева"}
cb("order", E); cb("of_opt_0", E); cb("of_nodate", E)
msg("Москва\nСтатус: ✅ Выполнена", E)
assert "Москва Статус" in order_form.summary(storage.get_form(9)["data"])

# --- пост в канал
def calls(method, since):
    return [p for m, p in sent[since:] if m == method]

msg("/post", U)                                            # не администратор
assert "Пост в канал" not in last()["text"]
n = len(sent)
cb("ch_menu", U)
assert not calls("editMessageText", n)

msg("/post", A)
assert "Пост в канал" in last(111)["text"]
n = len(sent)
msg("", A, photo=[{"file_id": "p"}], caption="Фото с выступления", message_id=777)
cp = calls("copyMessage", n)
assert cp and cp[0]["chat_id"] == 111 and cp[0]["from_chat_id"] == 111 and cp[0]["message_id"] == 777
assert "t.me/oasisdance_show_bot?start=channel" in str(cp[0]["reply_markup"])
assert "Публикуем" in last(111)["text"]
cb("ch_btn", A)                                            # убрать кнопку
n = len(sent)
cb("ch_pub", A)
pub = calls("copyMessage", n)
assert pub and pub[0]["chat_id"] == "@oasis_dance_show" and "reply_markup" not in pub[0]
assert "опубликован" in last(111)["text"]
n = len(sent)
cb(f"ch_pin_{_mid[0]}", A)
assert calls("pinChatMessage", n)

msg("/post", A)
cb("ch_ai", A)
msg("Новогодние корпоративы", A)
assert any("Пост про Новогодние корпоративы &lt;b&gt;" in p.get("text", "") for p in out(111))
cb("ch_regen", A)
n = len(sent)
cb("ch_pub", A)
assert calls("copyMessage", n)[0]["reply_markup"]["inline_keyboard"][0][0]["text"].startswith("🎭")

msg("/post", A)
msg("/cancel", A)
msg("Обычный вопрос", A)                                    # после отмены — снова ассистент
assert last(111)["text"].startswith("Ответ")

msg("/start channel", D)
assert storage.count_events("src_channel", 0) == 1
msg("/stats", A)
assert "из канала" in last(111)["text"]
print("OK, вызовов API:", len(sent))
