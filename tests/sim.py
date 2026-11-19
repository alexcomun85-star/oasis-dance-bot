# Проверка диалогов бота без сети: python3 tests/sim.py (Telegram и Gemini заменены заглушками)
import os, sys, json, tempfile
os.environ["TELEGRAM_BOT_TOKEN"]="x"; os.environ["ADMIN_CHAT_IDS"]="111, @EbruBekker"
os.environ["DATA_DIR"]=tempfile.mkdtemp(); os.environ["GEMINI_API_KEY"]="k"
sys.path.insert(0, sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import tg
sent=[]
def fake(method, params=None, timeout=15):
    sent.append((method, params or {}))
    if method=="sendMessage":
        for row in (params.get("reply_markup") or {}).get("inline_keyboard", []):
            for b in row:
                if "callback_data" in b: assert len(b["callback_data"].encode())<=64, b
    return {"ok": True, "result": {}}
tg.call=fake
import bot, ai, config, order_form
ai.generate=lambda m,h=None: "Ответ <тест> & цена"
assert config.ADMIN_CHAT_IDS==[111], config.ADMIN_CHAT_IDS
U={"id":5,"first_name":"Аня","username":"anya"}
def msg(t): bot.on_message({"chat":{"id":5,"type":"private"},"from":U,"text":t})
def cb(d): bot.on_callback({"id":"1","from":U,"data":d,"message":{"chat":{"id":5}}})
def last(): return [p for m,p in sent if m=="sendMessage"][-1]
msg("/start"); assert "Аня" in last()["text"]
cb("order"); cb("order_opt_1")
msg("31.12.2099"); msg("25:00"); assert "Не удалось" in last()["text"]
msg("19:00"); msg("Москва, Арбат 1"); msg("сто"); msg("120")
cb("order_opt_0"); msg("Ира <b>"); msg("+79990000000"); msg("нет")
assert "подтвердите" in last()["text"] and "&lt;b&gt;" in last()["text"]
cb("order_confirm")
admin=[p for m,p in sent if m=="sendMessage" and p["chat_id"]==111]
assert admin and "Корпоратив" in admin[0]["text"] and "Стандартная" in admin[0]["text"]
print(open(order_form.ORDERS_FILE).read())
cb("oasis"); msg("Сколько стоит?"); assert last()["text"]=="Ответ &lt;тест&gt; &amp; цена"
msg("выход"); msg("/order"); msg("/cancel"); assert "отменено" in last()["text"]
msg("/myid"); msg("абракадабра"); msg("/unknown")
bot.on_message({"chat":{"id":-100,"type":"supergroup"},"from":U,"text":"/start"})
for c in ["video","contact","instagram","vk","partnership","main_menu","garbage"]: cb(c)
print("OK, сообщений:", len(sent))
