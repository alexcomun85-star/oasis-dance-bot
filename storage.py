"""
Хранилище бота на SQLite (входит в Python, отдельной установки не требует).
Файл базы: data/bot.db
"""
import json
import os
import sqlite3
import threading
import time

import config

DB_PATH = os.path.join(config.DATA_DIR, "bot.db")
_lock = threading.RLock()
_db = sqlite3.connect(DB_PATH, check_same_thread=False)
_db.row_factory = sqlite3.Row

_db.executescript("""
CREATE TABLE IF NOT EXISTS users (
    chat_id INTEGER PRIMARY KEY, username TEXT, first_name TEXT, last_name TEXT,
    first_seen REAL, last_seen REAL);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chat_id INTEGER, kind TEXT, status TEXT,
    user TEXT, data TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS order_msgs (
    order_id INTEGER, admin_id INTEGER, message_id INTEGER);
CREATE TABLE IF NOT EXISTS forms (
    chat_id INTEGER PRIMARY KEY, step INTEGER, data TEXT, user TEXT,
    updated REAL, abandoned_notified INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS ai_history (
    chat_id INTEGER PRIMARY KEY, history TEXT, updated REAL);
CREATE TABLE IF NOT EXISTS events (
    ts REAL, chat_id INTEGER, kind TEXT);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS seen (
    source TEXT, item_id TEXT, PRIMARY KEY (source, item_id));
CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, item_id TEXT, link TEXT, original TEXT,
    text TEXT, media_type TEXT, media TEXT, status TEXT, created REAL, channel_msg_id INTEGER);
""")
_db.commit()


def _q(sql, args=(), one=False, commit=False):
    with _lock:
        cur = _db.execute(sql, args)
        if commit:
            _db.commit()
            return cur.lastrowid
        rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows


# ---------- пользователи и события ----------

def touch_user(user: dict):
    if not user or not user.get("id"):
        return
    now = time.time()
    _q("""INSERT INTO users(chat_id, username, first_name, last_name, first_seen, last_seen)
          VALUES(?,?,?,?,?,?)
          ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username,
          first_name=excluded.first_name, last_name=excluded.last_name, last_seen=excluded.last_seen""",
       (user["id"], user.get("username"), user.get("first_name"), user.get("last_name"), now, now),
       commit=True)


def log_event(chat_id, kind: str):
    _q("INSERT INTO events(ts, chat_id, kind) VALUES(?,?,?)", (time.time(), chat_id, kind), commit=True)


def count_events(kind: str, since: float, chat_id=None) -> int:
    if chat_id is None:
        r = _q("SELECT COUNT(*) c FROM events WHERE kind=? AND ts>=?", (kind, since), one=True)
    else:
        r = _q("SELECT COUNT(*) c FROM events WHERE kind=? AND ts>=? AND chat_id=?",
               (kind, since, chat_id), one=True)
    return r["c"]


SUBMIT_LIMIT_PER_HOUR = 5


def too_many_submits(chat_id) -> bool:
    """Не больше 5 заявок и просьб перезвонить в час от одного человека — защита от засорения."""
    hour_ago = time.time() - 3600
    return count_events("order", hour_ago, chat_id) + count_events("callback", hour_ago, chat_id) >= SUBMIT_LIMIT_PER_HOUR


# ---------- заявки ----------

def _order(row):
    if not row:
        return None
    d = dict(row)
    d["user"] = json.loads(d["user"] or "{}")
    d["data"] = json.loads(d["data"] or "{}")
    return d


def add_order(chat_id, kind: str, user: dict, data: dict) -> int:
    now = time.time()
    return _q("INSERT INTO orders(chat_id, kind, status, user, data, created, updated) VALUES(?,?,?,?,?,?,?)",
              (chat_id, kind, "new", json.dumps(user, ensure_ascii=False),
               json.dumps(data, ensure_ascii=False), now, now), commit=True)


def get_order(order_id: int):
    return _order(_q("SELECT * FROM orders WHERE id=?", (order_id,), one=True))


def set_status(order_id: int, status: str):
    _q("UPDATE orders SET status=?, updated=? WHERE id=?", (status, time.time(), order_id), commit=True)


def list_orders(statuses, limit=10, offset=0):
    marks = ",".join("?" * len(statuses))
    rows = _q(f"SELECT * FROM orders WHERE status IN ({marks}) ORDER BY id DESC LIMIT ? OFFSET ?",
              (*statuses, limit, offset))
    return [_order(r) for r in rows]


def count_orders(statuses=None, since: float = 0, kind=None) -> int:
    sql, args = "SELECT COUNT(*) c FROM orders WHERE created>=?", [since]
    if statuses:
        sql += f" AND status IN ({','.join('?' * len(statuses))})"
        args += list(statuses)
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    return _q(sql, args, one=True)["c"]


def close_abandoned(chat_id):
    """Когда клиент дозаполнил заявку — убираем его «брошенную» запись из новых."""
    _q("UPDATE orders SET status='merged', updated=? WHERE chat_id=? AND kind='abandoned' AND status='new'",
       (time.time(), chat_id), commit=True)


def add_order_msg(order_id, admin_id, message_id):
    _q("INSERT INTO order_msgs(order_id, admin_id, message_id) VALUES(?,?,?)",
       (order_id, admin_id, message_id), commit=True)


def get_order_msgs(order_id):
    return [dict(r) for r in _q("SELECT * FROM order_msgs WHERE order_id=?", (order_id,))]


# ---------- незавершённые формы ----------

def get_form(chat_id):
    r = _q("SELECT * FROM forms WHERE chat_id=?", (chat_id,), one=True)
    if not r:
        return None
    return {"step": r["step"], "data": json.loads(r["data"]), "user": json.loads(r["user"]),
            "updated": r["updated"], "abandoned_notified": r["abandoned_notified"]}


def save_form(chat_id, form: dict):
    _q("""INSERT INTO forms(chat_id, step, data, user, updated, abandoned_notified) VALUES(?,?,?,?,?,0)
          ON CONFLICT(chat_id) DO UPDATE SET step=excluded.step, data=excluded.data,
          user=excluded.user, updated=excluded.updated""",
       (chat_id, form["step"], json.dumps(form["data"], ensure_ascii=False),
        json.dumps(form["user"], ensure_ascii=False), time.time()), commit=True)


def delete_form(chat_id):
    _q("DELETE FROM forms WHERE chat_id=?", (chat_id,), commit=True)


def stale_forms(older_than_sec: int):
    rows = _q("SELECT chat_id FROM forms WHERE abandoned_notified=0 AND updated<?",
              (time.time() - older_than_sec,))
    return [r["chat_id"] for r in rows]


def mark_form_notified(chat_id):
    _q("UPDATE forms SET abandoned_notified=1 WHERE chat_id=?", (chat_id,), commit=True)


# ---------- история ИИ ----------

def get_history(chat_id):
    r = _q("SELECT history FROM ai_history WHERE chat_id=?", (chat_id,), one=True)
    return json.loads(r["history"]) if r else []


def save_history(chat_id, history):
    _q("""INSERT INTO ai_history(chat_id, history, updated) VALUES(?,?,?)
          ON CONFLICT(chat_id) DO UPDATE SET history=excluded.history, updated=excluded.updated""",
       (chat_id, json.dumps(history[-20:], ensure_ascii=False), time.time()), commit=True)


def clear_history(chat_id):
    _q("DELETE FROM ai_history WHERE chat_id=?", (chat_id,), commit=True)


# ---------- удаление данных по запросу (152-ФЗ) ----------

def forget_user(chat_id):
    for sql in ("DELETE FROM forms WHERE chat_id=?", "DELETE FROM ai_history WHERE chat_id=?",
                "DELETE FROM users WHERE chat_id=?", "DELETE FROM events WHERE chat_id=?"):
        _q(sql, (chat_id,), commit=True)
    # Заявки обезличиваем, чтобы не сбить нумерацию и статистику
    _q("UPDATE orders SET user='{}', data='{\"удалено\": \"по запросу клиента\"}' WHERE chat_id=?",
       (chat_id,), commit=True)


# ---------- перенос данных из прошлой версии ----------

def migrate_jsonl(path):
    if not os.path.exists(path):
        return 0
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
                add_order(rec.get("chat_id"), "form", rec.get("user", {}), rec.get("data", {}))
                n += 1
            except Exception:
                continue
    os.rename(path, path + ".imported")
    return n


# ---------- источники постов (ВКонтакте, пересылка) ----------

def is_seen(source, item_id) -> bool:
    return _q("SELECT 1 FROM seen WHERE source=? AND item_id=?", (source, str(item_id)), one=True) is not None


def mark_seen(source, item_id):
    _q("INSERT OR IGNORE INTO seen(source, item_id) VALUES(?,?)", (source, str(item_id)), commit=True)


def source_known(source) -> bool:
    return _q("SELECT 1 FROM seen WHERE source=? LIMIT 1", (source,), one=True) is not None


def add_draft(source, item_id, link, original, text, media_type, media) -> int:
    return _q("""INSERT INTO drafts(source, item_id, link, original, text, media_type, media, status, created)
                 VALUES(?,?,?,?,?,?,?,?,?)""",
              (source, str(item_id), link, original, text, media_type, media, "pending", time.time()), commit=True)


def get_draft(draft_id):
    r = _q("SELECT * FROM drafts WHERE id=?", (draft_id,), one=True)
    return dict(r) if r else None


def update_draft(draft_id, **fields):
    keys = [k for k in fields if k in ("text", "status", "channel_msg_id")]
    if keys:
        _q(f"UPDATE drafts SET {', '.join(k + '=?' for k in keys)} WHERE id=?",
           (*[fields[k] for k in keys], draft_id), commit=True)


def has_draft(source, item_id) -> bool:
    return _q("SELECT 1 FROM drafts WHERE source=? AND item_id=?", (source, str(item_id)), one=True) is not None


def count_drafts(status) -> int:
    return _q("SELECT COUNT(*) c FROM drafts WHERE status=?", (status,), one=True)["c"]


# ---------- настройки, которые меняются кнопками ----------

def get_setting(key, default=None):
    r = _q("SELECT value FROM settings WHERE key=?", (key,), one=True)
    return r["value"] if r else default


def set_setting(key, value):
    _q("INSERT INTO settings(key, value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
       (key, str(value)), commit=True)
