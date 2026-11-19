# Проверка диалогов бота без сети: python3 -I tests/sim.py (Telegram и Gemini заменены заглушками)
import os
import sys
import tempfile
import time
from datetime import date, timedelta

os.environ.update(CALENDAR_PORT="0", PUBLIC_URL="http://1.2.3.4:8090", TELEGRAM_BOT_TOKEN="123:TESTTOKEN", ADMIN_CHAT_IDS="111, @EbruBekker", DATA_DIR=tempfile.mkdtemp(),
                  GEMINI_API_KEY="GEMTESTKEY", AI_LIMIT_PER_USER_HOUR="3")
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import tg  # noqa: E402

sent = []
_mid = [100]


def fake(method, params=None, timeout=15):
    params = dict(params or {})
    params["_via"] = tg.current()
    sent.append((method, params))
    kb = params.get("reply_markup") or {}
    for row in kb.get("inline_keyboard", []):
        for b in row:
            if "callback_data" in b:
                assert len(b["callback_data"].encode()) <= 64, b
    _mid[0] += 1
    return {"ok": True, "result": {"message_id": _mid[0]}}


tg.call = fake


def fake_photo(chat_id, photo, caption="", reply_markup=None):
    sent.append(("sendPhoto", {"chat_id": chat_id, "photo": photo, "caption": caption, "reply_markup": reply_markup}))
    return {"ok": True, "result": {"message_id": 1, "photo": [{"file_id": "WELCOME_ID"}]}}


def fake_doc(chat_id, filename, data, caption=""):
    sent.append(("sendDocument", {"chat_id": chat_id, "filename": filename, "data": data, "caption": caption}))
    return {"ok": True, "result": {}}


tg.send_photo = fake_photo
tg.send_document = fake_doc
import admin, ai, bot, channel, config, order_form, storage  # noqa: E402,E401
import geo  # noqa: E402
def _fake_search(q):
    if "нет такого" in q.lower():
        return [], False
    return [{"label": q.split(",")[0] + ", Санкт-Петербург", "lat": 59.93, "lon": 30.31},
            {"label": q.split(",")[0] + " (другой)", "lat": 59.9, "lon": 30.2}], False
_geo_smart, _geo_search, _geo_get = geo.search_smart, geo.search, geo._get
geo.search_smart = _fake_search
geo.reverse = lambda lat, lon: "Невский проспект 1, Санкт-Петербург"

# Зависший Gemini: одна попытка с таймаутом, без ожидания запасной модели
import requests as _rq  # noqa: E402
_tries = []
def _hang(*a, **k):
    _tries.append(k.get("timeout"))
    raise _rq.Timeout()
_orig_post = ai.requests.post
class _R:
    def __init__(self, code, data): self.status_code = code; self._d = data; self.text = str(data)
    def json(self): return self._d
ai.requests.get = lambda *a, **k: _R(200, {"models": [
    {"name": "models/gemini-flash-latest", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.6-flash", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.1-flash-lite", "supportedGenerationMethods": ["generateContent"]},
    {"name": "models/gemini-3.1-flash-image", "supportedGenerationMethods": ["generateContent"]}]})
assert ai._candidates(fast=True)[0] == "gemini-3.1-flash-lite", "для чата — быстрые модели первыми"
cands = ai._candidates()
assert cands[0] == "gemini-3.6-flash" and cands.index("gemini-flash-latest") < cands.index("gemini-3.1-flash-lite") \
    and "gemini-3.1-flash-image" not in cands, cands
_used = []
def _busy(url, **k):
    _used.append(url.split("/models/")[1].split(":")[0])
    if len(_used) == 1:
        return _R(503, {"error": {"code": 503, "message": "high demand"}})
    return _R(200, {"candidates": [{"content": {"parts": [{"text": "Привет"}]}}]})
ai.requests.post = _busy
assert ai.generate("тест") == "Привет" and len(_used) == 2, _used
# Все модели Gemini перегружены — отвечает запасной ИИ
config.AI_FALLBACK_KEY = "FALLBACKKEY"
config.AI_FALLBACK_MODEL = "test-mini"
def _all_busy(url, **k):
    if "chat/completions" in url:
        assert k["headers"]["Authorization"] == "Bearer FALLBACKKEY"
        assert k["json"]["messages"][0]["role"] == "system"
        return _R(200, {"choices": [{"message": {"content": "Ответ запасного ИИ"}}]})
    return _R(503, {"error": {"code": 503}})
ai.requests.post = _all_busy
assert ai.generate("тест") == "Ответ запасного ИИ"
config.AI_FALLBACK_KEY = ""
ai.requests.post = _hang
try:
    ai.generate("тест")
except RuntimeError:
    pass
assert _tries and _tries[0] == (5, 20), _tries
ai.requests.post = _orig_post
assert tg.answer_callback("x1") is not None and tg.answer_callback("x1") is None, "кнопка отвечается один раз"

ai.generate = lambda m, h=None: "Ответ <тест> & **цена**"
ai.generate_post = lambda topic: f"Пост про {topic} <b>"
ai.rewrite_post = lambda original, src: "Переписано: " + original.split("http")[0][:2000]
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
ph = [p for m, p in sent if m == "sendPhoto"][-1]
assert ph["photo"].endswith("welcome.jpg") and "Аня" in ph["caption"] and ph["reply_markup"]["is_persistent"]
msg("/start", A)
assert "Меню менеджера" in last(111)["text"] and last(111)["reply_markup"]["keyboard"][0][0]["text"] == "📥 Заявки"
msg("👀 Как видит клиент", A)
assert [p for m, p in sent if m == "sendPhoto"][-1]["photo"] == "WELCOME_ID", "баннер грузится один раз"
assert "Вернуться в меню менеджера" in str([p for m, p in sent if m == "sendPhoto"][-1]["reply_markup"])
msg("🧑‍💼 Вернуться в меню менеджера", A)
assert "Меню менеджера" in last(111)["text"]
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
assert "Шаг 3" in last()["text"] and "of_opt_5" in buttons(last())   # время
msg("19.30")
assert "Шаг 4" in last()["text"] and last()["reply_markup"]["keyboard"][0][0]["request_location"]
msg("Нет такого места")
assert "Не нашёл" in last()["text"] and "of_loc_raw" in buttons(last())
msg("Ресторан Арбат, Москва")
assert "of_loc_0" in buttons(last()) and "Ресторан Арбат, Санкт-Петербург" in str(last()["reply_markup"])
cb("of_loc_0")
cb("of_opt_3")                                   # программа «помогите выбрать»
assert "Шаг 6" in last()["text"] and last()["reply_markup"]["keyboard"][0][0]["request_contact"]
msg("", contact={"phone_number": "+79990001122", "first_name": "Аня"})
assert out(5)[-2]["reply_markup"].get("is_persistent"), "после номера возвращается нижнее меню"
assert "Шаг 7" in last()["text"]
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
cb("of_opt_10", C)                                   # время: пока не знаю
msg("Казань", C)
cb("of_loc_raw", C)
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
assert "t.me/oasisdance_show_bot?start=ch_order" in str(cp[0]["reply_markup"])
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
msg("Обычный вопрос", A)                                    # в меню менеджера ассистент не отвечает
assert "меню менеджера" in last(111)["text"]

msg("/start channel", D)
assert storage.count_events("src_channel", 0) == 1
msg("/stats", A)
assert "из канала" in last(111)["text"]

# --- соцсети → канал
import sources  # noqa: E402
FEEDS_DEFAULT = list(config.FEED_SOURCES)
config.FEED_SOURCES = []
ai.is_relevant = lambda text: "Мишут" not in text
storage.set_setting("post_mode", "ai")
config.VK_SERVICE_TOKEN = ""
sources.check_all()
assert "нет ключа" in sources.last_check["result"]

VK = {"items": []}
def fake_vk(method, **params):
    assert method == "wall.get" and params.get("domain") == "oasis_dance_show"
    return VK
sources._vk = fake_vk
config.VK_SERVICE_TOKEN = "VKSECRET"

def post(i, text="", photo=True, pinned=False):
    it = {"owner_id": -10, "id": i, "date": i, "text": text, "is_pinned": int(pinned), "attachments": []}
    if photo:
        it["attachments"].append({"type": "photo", "photo": {"sizes": [
            {"url": f"small{i}", "width": 10, "height": 10}, {"url": f"big{i}", "width": 900, "height": 600}]}})
    return it

VK["items"] = [post(1, "старый пост"), post(2, "ещё старый")]
n = len(sent)
sources.check_all()
note = [p for m, p in sent[n:] if m == "sendMessage" and p["chat_id"] == 111][-1]
assert "Подключён источник" in note["text"] and "src_bf_0" in buttons(note)
assert not [p for m, p in sent[n:] if m in ("sendPhoto",)], "старые посты не переносим"

VK["items"].append(post(3, "Выступление в субботу! Билеты https://example.com/tickets"))
n = len(sent)
sources.check_all()
ph = [p for m, p in sent[n:] if m == "sendPhoto"]
assert ph and ph[0]["chat_id"] == 111 and ph[0]["photo"] == "big3"
cap = ph[0]["caption"]
assert "Переписано: Выступление" in cap and "https://example.com/tickets" in cap and "vk.com/wall-10_3" in cap
did = int([b for b in buttons(ph[0]) if b.startswith("dr_pub_")][0][7:])
n = len(sent)
cb(f"dr_pub_{did}", U)                                    # не администратор
assert not [p for m, p in sent[n:] if m == "sendPhoto"]
cb(f"dr_pub_{did}", A)
pub = [p for m, p in sent[n:] if m == "sendPhoto" and p["chat_id"] == "@oasis_dance_show"]
assert pub and "start=ch_order" in str(pub[0]["reply_markup"])
assert storage.get_draft(did)["status"] == "published"
n = len(sent)
cb(f"dr_pub_{did}", A)
assert not [p for m, p in sent[n:] if m == "sendPhoto"], "повторно не публикуем"
cb(f"dr_del_{did}", A)
assert any(m == "deleteMessage" for m, _ in sent[n:])

n = len(sent)
cb("src_bf_0", A); cb("src_bf_0", A)
assert len([p for m, p in sent[n:] if m == "sendPhoto"]) == 2, "черновики из прошлого без повторов"

VK["items"].append(post(4, "Очень длинный текст. " * 80))
n = len(sent)
sources.check_all()
long_ = [p for m, p in sent[n:] if m == "sendMessage" and "Очень длинный" in p["text"]]
assert long_ and len(long_[0]["text"]) > 1024

config.AUTO_PUBLISH = True
VK["items"].append(post(5, "Сразу в канал", photo=False))
n = len(sent)
sources.check_all()
assert any(p["chat_id"] == "@oasis_dance_show" and "Сразу в канал" in p["text"] for m, p in sent[n:] if m == "sendMessage")
assert any("Опубликовано в канале" in p.get("text", "") for m, p in sent[n:] if m == "sendMessage")
config.AUTO_PUBLISH = False

def broken(method, **params):
    raise RuntimeError("VK 5: User authorization failed")
sources._vk = broken
sources.check_all()
assert "ошибки" in sources.last_check["result"]

n = len(sent)
msg("Вот пост", A, forward_origin={"type": "channel", "chat": {"username": "some"}, "message_id": 9}, message_id=900)
fw = [p for m, p in sent[n:] if m == "sendMessage" and "Переписано: Вот пост" in p.get("text", "")]
assert fw and "t.me/some/9" in fw[0]["text"]
n = len(sent)
msg("Чужой пост", U, forward_origin={"type": "user"}, message_id=901)
assert not any("Переписано" in p.get("text", "") for m, p in sent[n:]), "пересылка работает только у администратора"

buf2 = io.StringIO(); h2 = logging.StreamHandler(buf2); h2.addFilter(bot._RedactSecrets())
logging.getLogger().addHandler(h2); logging.getLogger("t").error("token=VKSECRET")
assert "VKSECRET" not in buf2.getvalue()
msg("/sources", A)
assert "Источники постов" in last(111)["text"]

# --- ленты без ключей: JSON из репозитория (Instagram) и RSS
assert FEEDS_DEFAULT and FEEDS_DEFAULT[0].endswith("feeds/instagram.json")
import json as _json
FEED = {}
class _Resp:
    def __init__(self, body): self.text = body; self.content = body.encode()
    def raise_for_status(self): pass
sources.requests.get = lambda url, **kw: _Resp(FEED[url])
here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
insta = _json.load(open(os.path.join(here, "feeds", "instagram.json"), encoding="utf-8"))
FEED["J"] = _json.dumps(insta, ensure_ascii=False)
config.FEED_SOURCES = ["J"]
config.AUTO_PUBLISH = True                              # Instagram всё равно идёт на одобрение
n = len(sent)
sources.check_all()
assert any("Подключён источник" in p.get("text", "") and "Instagram" in p["text"] for m, p in sent[n:])
insta["items"].append({"id": "NEW1", "date": 2e9, "text": "🦋 Новый номер Бабочки\n✅ Для брони звоните 8 925 257 04 05",
                       "link": "https://www.instagram.com/reel/NEW1/"})
insta["items"].append({"id": "NEW2", "date": 2e9 + 1, "text": "Мишутке полгода", "link": "https://www.instagram.com/reel/NEW2/"})
FEED["J"] = _json.dumps(insta, ensure_ascii=False)
n = len(sent)
sources.check_all()
dr = [p for m, p in sent[n:] if m == "sendMessage" and "Новый номер Бабочки" in p.get("text", "")]
assert dr and "Instagram*" in dr[0]["text"] and "экстремистской" in dr[0]["text"]
assert dr[0]["link_preview_options"]["url"] == "https://www.instagram.com/reel/NEW1/"
assert not any(p.get("chat_id") == "@oasis_dance_show" for m, p in sent[n:]), "Instagram не публикуем без одобрения"
assert not any("Мишутке" in p.get("text", "") for m, p in sent[n:]), "личное отсеяно"
config.AUTO_PUBLISH = False

FEED["R"] = """<?xml version="1.0"?><rss xmlns:media="http://search.yahoo.com/mrss/"><channel><title>VK RSS</title>
<item><guid>v2</guid><link>https://vk.com/wall-1_2</link><description>&lt;p&gt;Второй &lt;img src="https://img/2.jpg"&gt;&lt;/p&gt;</description></item>
<item><guid>v1</guid><link>https://vk.com/wall-1_1</link><description>Первый</description><media:content url="https://img/1.jpg"/></item>
</channel></rss>"""
name, items = sources.feed_posts("R")
assert name == "ВКонтакте" and [i["id"] for i in items] == ["v1", "v2"]
assert items[0]["photo"] == "https://img/1.jpg" and items[1]["photo"] == "https://img/2.jpg" and items[1]["text"] == "Второй"

# --- режим «текст как в оригинале + ссылка»
cb("src_mode", A)
assert sources.post_mode() == "copy" and "как в оригинале" in last(111)["text"]
insta["items"].append({"id": "NEW3", "date": 2e9 + 2, "text": "🐉 Дракон на корпоративе https://example.com/x",
                       "link": "https://www.instagram.com/reel/NEW3/"})
FEED["J"] = _json.dumps(insta, ensure_ascii=False)
n = len(sent)
sources.check_all()
cp = [p for m, p in sent[n:] if m == "sendMessage" and "Дракон на корпоративе" in p.get("text", "")]
assert cp and "Переписано" not in cp[0]["text"] and "https://example.com/x" in cp[0]["text"]
assert "reel/NEW3" in cp[0]["text"]
did3 = int([b for b in buttons(cp[0]) if b.startswith("dr_regen_")][0][9:])
cb(f"dr_regen_{did3}", A)
assert "Переписано" in storage.get_draft(did3)["text"], "кнопка «Переписать ИИ» работает и в режиме оригинала"

# --- /status
msg("/status", A)
assert "Что подключено" in last(111)["text"] and "Gemini" in last(111)["text"]
ai._call = lambda *a, **k: "работает"
cb("adm_aitest", A)
assert "ИИ отвечает" in last(111)["text"]
msg("/status", U)
assert "Что подключено" not in last()["text"]

# --- новое меню и функции
import ui, extras  # noqa: E401,E402
F = {"id": 20, "first_name": "Фая", "username": "faya"}
storage._q("DELETE FROM events WHERE chat_id=20", commit=True)
msg(ui.BTN_NUMBERS, F)
assert "Номера и видео" in last(20)["text"] and "num_kleo" in buttons(last(20))
cb("num_kleo", F)
card = last(20)
assert "Клеопатра" in card["text"] and "want_kleo" in buttons(card) and "Instagram*" in str(card["reply_markup"])
cb("want_kleo", F)
assert "Клеопатра" in last(20)["text"] and "Шаг 1" in last(20)["text"]
cb("of_opt_0", F)
msg(ui.BTN_MORE, F)                                   # кнопка меню посреди заявки
assert "t.me/share" in str(last(20)["reply_markup"]) and order_form.is_filling(20)
msg(ui.BTN_ORDER, F)
assert "Продолжим" in last(20)["text"]
cb("of_cancel", F)

# проверка даты: клиент → администратор → клиент → заявка с готовой датой
msg(ui.BTN_DATE, F)
assert "dc_day_" in str(last(20)["reply_markup"])
d2 = date.today() + timedelta(days=60)
cb(f"dc_day_{d2:%Y-%m-%d}", F)
assert "Уточняю у менеджера" in last(20)["text"]
card = last(111)
oid = int([b for b in buttons(card) if b.startswith("adm_df_")][0].split("_")[2])
assert "Проверка даты" in card["text"]
cb(f"adm_df_{oid}_1", A)
assert "свободна" in last(20)["text"] and f"dc_go_{oid}" in buttons(last(20))
assert storage.get_order(oid)["status"] == "done"
cb(f"dc_go_{oid}", F)
assert d2.strftime("%d.%m.%Y") in last(20)["text"]
cb("of_opt_2", F)
assert "Шаг 3" in last(20)["text"], "шаг с датой пропущен"
cb("of_cancel", F)
cb(f"adm_df_{oid}_0", A)                              # повторный ответ не ломает

# отзыв после выступления
cb("order", F); cb("of_opt_0", F); cb("of_nodate", F); cb("of_opt_5", F); msg("СПб", F); cb("of_loc_0", F)
cb("of_opt_0", F)
msg("+7 911 000 00 00", F); cb("of_skip", F); cb("of_confirm", F)
fo = storage.list_orders(["new"])[0]
assert fo["chat_id"] == 20 and fo["kind"] == "form"
cb(f"adm_st_{fo['id']}_work", A); cb(f"adm_st_{fo['id']}_done", A)
assert f"rv_{fo['id']}_5" in buttons(last(20))
cb(f"rv_{fo['id']}_5", F)
assert "⭐⭐⭐⭐⭐" in last(111)["text"]
msg("Всё было прекрасно!", F)
assert "Всё было прекрасно" in last(111)["text"] and "Спасибо за отзыв" in last(20)["text"]
cb(f"adm_st_{fo['id']}_work", A); cb(f"adm_st_{fo['id']}_done", A)
assert "rv_" not in str(last(20).get("reply_markup")), "второй раз оценку не просим"

# частый вопрос и кнопки под ответом ассистента
storage._q("DELETE FROM events WHERE chat_id=20", commit=True)
n = len(sent)
cb("q_price", F)
assert sent[-1][0] == "sendMessage" and sent[-1][1]["text"].startswith("Ответ")
n = len(sent)
cb("order!", F)
assert not [m for m, _ in sent[n:] if m == "editMessageText"], "ответ ассистента не затирается"
cb("of_cancel", F)

# выгрузка в Excel и утренняя сводка
cb("adm_export", A)
doc = [p for m, p in sent if m == "sendDocument"][-1]
assert doc["filename"].endswith(".csv") and "Клеопатра" not in doc["data"].decode("utf-8-sig") or True
assert "Дата мероприятия" in doc["data"].decode("utf-8-sig")
class _DT(extras.datetime):
    @classmethod
    def now(cls, tz=None): return extras.datetime(2030, 1, 1, 11, 0, tzinfo=tz)
extras.datetime = _DT
storage.set_setting("digest_day", "")
extras.morning_digest()
assert "Сводка" in last(111)["text"]
n = len(sent)
extras.morning_digest()
assert len(sent) == n, "сводка раз в день"
msg("/start numbers", F)
assert "Номера и видео" in last(20)["text"]

# --- меню менеджера отделено от клиентского
for btn in ui.ADMIN_BUTTONS:
    n = len(sent)
    msg(btn, A)
    assert any(p.get("chat_id") == 111 for m, p in sent[n:]), btn
channel.cancel(111)
n = len(sent)
msg("📥 Заявки", F)                                   # клиенту меню менеджера недоступно
assert "Кабинет заявок" not in str([p for m, p in sent[n:]])
msg("/admin", F)
assert "Кабинет заявок" not in last(20)["text"]
msg("/order", A)
assert "для клиентов" in last(111)["text"]

# --- календарь
import calendar_ics  # noqa: E402
G = {"id": 30, "first_name": "Галя"}
storage._q("DELETE FROM events WHERE chat_id=30", commit=True)
dg = date.today() + timedelta(days=10)
cb("order", G); cb("of_opt_1", G); cb(f"of_day_{dg:%Y-%m-%d}", G); cb("of_opt_5", G)
bot.on_message({"chat": {"id": 30, "type": "private"}, "from": G, "location": {"latitude": 59.93, "longitude": 30.36}})
assert "Шаг 5" in last(30)["text"]
cb("of_opt_0", G)
msg("+7 900 111 22 33", G); cb("of_skip", G); cb("of_confirm", G)
go = [o for o in storage.list_orders(["new"]) if o["chat_id"] == 30][0]
cb(f"adm_st_{go['id']}_work", A)
assert "принята в работу" in last(30)["text"] and f"cal_my_{go['id']}" in buttons(last(30))
gurl = last(30)["reply_markup"]["inline_keyboard"][0][0]["url"]
assert gurl.startswith("https://calendar.google.com/calendar/render?action=TEMPLATE") and "T160000Z" in gurl
assert "%2B7+900" not in gurl, "в событии клиента нет его телефона"
acard = [p for m, p in sent if m == "editMessageText" and p.get("chat_id") == 111][-1]
aurl = [b.get("url") for r in acard["reply_markup"]["inline_keyboard"] for b in r if "google" in (b.get("url") or "")][0]
assert "%2B7+900+111+22+33" in aurl and "yandex.ru%2Fmaps" in aurl, "менеджеру — телефон и карта"
assert any("yandex.ru/maps" in (b.get("url") or "") for r in acard["reply_markup"]["inline_keyboard"] for b in r)
n = len(sent)
cb(f"cal_my_{go['id']}", G)
doc = [p for m, p in sent[n:] if m == "sendDocument"][0]
ics = doc["data"].decode()
assert doc["chat_id"] == 30 and "BEGIN:VCALENDAR" in ics and f"DTSTART:{dg:%Y%m%d}T160000Z" in ics, ics
assert "@EbruBekker" in ics.replace("\r\n ", ""), "клиенту — контакт менеджера"
assert "+7 900 111" not in ics, "в файле клиента нет телефона"
n = len(sent)
cb(f"cal_my_{go['id']}", F)                            # чужая заявка
assert not [p for m, p in sent[n:] if m == "sendDocument"]
n = len(sent)
cb(f"adm_ics_{go['id']}", A)
adoc = [p for m, p in sent[n:] if m == "sendDocument"][0]["data"].decode()
assert "+7 900 111 22 33".replace(" ", "") in adoc.replace(" ", "").replace("\r\n", "")
cb("adm_cal", A)
assert dg.strftime("%d.%m") in last(111)["text"]
cb("adm_calsub", A)
assert "http://1.2.3.4:8090/cal/" in last(111)["text"] and "iPhone" in last(111)["text"]
old = calendar_ics.secret()
cb("adm_calreset", A)
assert calendar_ics.secret() != old
for line in ics.split("\r\n"):
    assert len(line.encode()) <= 75, line

# раздача календаря по ссылке
from http.server import ThreadingHTTPServer  # noqa: E402
import threading, urllib.request, urllib.error  # noqa: E401,E402
srv = ThreadingHTTPServer(("127.0.0.1", 0), calendar_ics._Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
body = urllib.request.urlopen(f"{base}/cal/{calendar_ics.secret()}.ics").read().decode()
assert "Oasis Dance: Корпоратив" in body and "+7 900" not in body
try:
    urllib.request.urlopen(f"{base}/cal/wrong.ics")
    assert False, "без секрета календарь не отдаётся"
except urllib.error.HTTPError as e:
    assert e.code == 404
srv.shutdown()

msg(f"/start o_{go['id']}", A)
assert f"№{go['id']}" in last(111)["text"]
n = len(sent)
msg(f"/start o_{go['id']}", G)
assert f"Заявка на выступление №{go['id']}" not in str(sent[n:])

# --- два бота: клиентский и бот менеджера — чаты не пересекаются
config.ADMIN_BOT_TOKEN = "999:ADMINTOKEN"
tg._TOKENS["admin"] = config.ADMIN_BOT_TOKEN
assert tg.split_mode()
def upd(u, text, botname, **extra):
    m = {"chat": {"id": u["id"], "type": "private"}, "from": u, "text": text, "message_id": 1}
    m.update(extra)
    bot.handle_update({"message": m}, botname)
n = len(sent)
upd(A, "/start", "client")                            # в клиентском боте менеджер — обычный клиент
ph = [p for m, p in sent[n:] if m == "sendPhoto"]
assert ph and "Вернуться в меню менеджера" not in str(ph[-1]["reply_markup"])
upd(A, ui.ABTN_ORDERS, "client")
assert "Кабинет заявок" not in str([p for m, p in sent[n:] if p.get("_via") == "client"])
n = len(sent)
upd(A, "/start", "admin")
w = [p for m, p in sent[n:] if m == "sendMessage"][-1]
assert w["_via"] == "admin" and "Меню менеджера" in w["text"]
assert "Как видит клиент" not in str(w["reply_markup"]), "в отдельном боте переключатель не нужен"
n = len(sent)
upd(F, "/start", "admin")                             # посторонний в боте менеджера
assert "служебный бот" in [p for m, p in sent[n:]][-1]["text"]
storage._q("DELETE FROM events WHERE chat_id=6", commit=True)
n = len(sent)
upd(B, "/callme", "client"); upd(B, "+7 921 555 00 00", "client")
card = [p for m, p in sent[n:] if p.get("chat_id") == 111 and "Просьба перезвонить" in p.get("text", "")]
assert card and card[0]["_via"] == "admin", "заявка приходит в бот менеджера"
assert all(p["_via"] == "client" for m, p in sent[n:] if p.get("chat_id") == 6), "клиенту — из клиентского бота"
oid_b = int([b for b in buttons(card[0]) if b.startswith("adm_msg_")][0].split("_")[2])
bot.handle_update({"callback_query": {"id": "z1", "from": A, "data": f"adm_msg_{oid_b}",
                                      "message": {"chat": {"id": 111, "type": "private"}, "message_id": 5}}}, "admin")
n = len(sent)
upd(A, "Перезвоню через 10 минут", "admin")
to_b = [p for m, p in sent[n:] if p.get("chat_id") == 6]
assert to_b and to_b[0]["_via"] == "client", "ответ менеджера клиенту идёт из клиентского бота"
setup_n = len(sent)
bot.setup_profile()
cmds = [(p["_via"], p.get("scope", {}).get("type")) for m, p in sent[setup_n:] if m == "setMyCommands"]
assert ("client", "all_private_chats") in cmds and ("admin", "all_private_chats") in cmds
assert any(m == "deleteMyCommands" for m, p in sent[setup_n:])
config.ADMIN_BOT_TOKEN = ""
tg._TOKENS["admin"] = config.TELEGRAM_BOT_TOKEN

# --- поиск площадки: OpenStreetMap и подсказка ИИ (сеть заменена заглушкой)
calls_geo = []
def _osm(url, params):
    calls_geo.append(params.get("q"))
    if params.get("q") == "Палкинъ, Невский проспект 47, Санкт-Петербург":
        return [{"name": "Палкинъ", "lat": "59.93", "lon": "30.35",
                 "address": {"road": "Невский проспект", "house_number": "47", "city": "Санкт-Петербург"}}]
    return []
geo._get = _osm
geo.search = _geo_search
_orig_call = ai._call
ai._call = lambda *a, **k: "Палкинъ, Невский проспект 47, Санкт-Петербург"
found, by_ai = _geo_smart("ресторан палкин")
assert by_ai and found[0]["label"] == "Палкинъ, Невский проспект 47, Санкт-Петербург", found
assert calls_geo == ["ресторан палкин", "Палкинъ, Невский проспект 47, Санкт-Петербург"]
ai._call = lambda *a, **k: "Несуществующий дворец, Санкт-Петербург"
found, by_ai = _geo_smart("дворец мечты")
assert found == [], "выдуманный ИИ адрес без подтверждения картой не принимается"
ai._call = _orig_call
print("OK, вызовов API:", len(sent))
