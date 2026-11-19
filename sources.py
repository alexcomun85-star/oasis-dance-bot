"""
Наполнение канала из соцсетей: новые публикации ВКонтакте и пересланные боту посты
→ ИИ переписывает для Telegram (с сохранением ссылок) → черновик администратору или сразу в канал.
"""
import html
import json
import logging
import re
import threading
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

import ai
import channel
import config
import storage
import tg

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.199"
URL_RE = re.compile(r"https?://[^\s<>\"]+")
CAPTION_LIMIT = 1024
_check_lock = threading.Lock()
last_check = {"time": 0, "result": "ещё не проверялось"}


# ---------- ВКонтакте ----------

def _vk(method, **params):
    params.update(access_token=config.VK_SERVICE_TOKEN, v=VK_VERSION)
    r = requests.get(VK_API + method, params=params, timeout=20)
    data = r.json()
    if "error" in data:
        raise RuntimeError(f"VK {data['error'].get('error_code')}: {data['error'].get('error_msg')}")
    return data["response"]


def _owner_param(src: str) -> dict:
    s = src.strip().replace("https://vk.com/", "").replace("vk.com/", "").strip("/")
    if re.fullmatch(r"-?\d+", s):
        return {"owner_id": s}
    if re.fullmatch(r"id\d+", s):
        return {"owner_id": s[2:]}
    if re.fullmatch(r"(club|public)\d+", s):
        return {"owner_id": "-" + re.sub(r"\D", "", s)}
    return {"domain": s}


def vk_posts(src: str, count=10):
    items = _vk("wall.get", count=count, **_owner_param(src)).get("items", [])
    posts = []
    for it in items:
        if it.get("marked_as_ads") or (it.get("copy_history") and not it.get("text")):
            continue  # реклама и чужие репосты без своего текста
        text = it.get("text", "")
        photo = video = None
        extra_links = []
        for a in it.get("attachments", []):
            if a["type"] == "photo" and not photo:
                sizes = a["photo"].get("sizes", [])
                if sizes:
                    photo = max(sizes, key=lambda s: s.get("width", 0) * s.get("height", 0))["url"]
            elif a["type"] == "video":
                v = a["video"]
                extra_links.append(f"https://vk.com/video{v['owner_id']}_{v['id']}")
            elif a["type"] == "link":
                extra_links.append(a["link"].get("url", ""))
        posts.append({
            "id": f"{it['owner_id']}_{it['id']}",
            "date": it.get("date", 0),
            "text": text,
            "link": f"https://vk.com/wall{it['owner_id']}_{it['id']}",
            "photo": photo,
            "extra_links": [x for x in extra_links if x],
            "pinned": bool(it.get("is_pinned")),
        })
    posts.sort(key=lambda p: p["date"])
    return posts


# ---------- подготовка поста ----------

def _compose(original: str, source_name: str, link: str, extra_links=()) -> str:
    """Текст поста в HTML: переписанный ИИ текст + потерянные ссылки + ссылка на оригинал."""
    body = ""
    if original.strip():
        try:
            body = ai.rewrite_post(original, source_name) if ai.is_available() else original
        except Exception as e:
            logger.error("ИИ не переписал пост: %s", e)
            body = original
    body = body.replace("**", "").strip()
    # Ссылки из оригинала, которые ИИ мог потерять, — добавляем отдельно
    must = list(dict.fromkeys(URL_RE.findall(original) + list(extra_links)))
    lost = [u for u in must if u not in body]
    text = html.escape(body) if body else "💃 Свежая публикация шоу-балета Oasis Dance."
    if lost:
        text += "\n\n" + "\n".join(html.escape(u) for u in lost)
    is_insta = source_name == "Instagram" and config.INSTAGRAM_NOTE
    if link:
        label = html.escape(source_name) + ("*" if is_insta else "")
        text += f'\n\n📎 <a href="{html.escape(link)}">Оригинал: {label}</a>'
    if is_insta:
        text += f"\n<i>* {html.escape(config.INSTAGRAM_NOTE)}</i>"
    return text


def _send(chat_id, draft, reply_markup):
    """Отправляет пост (фото/видео с подписью или текст). Возвращает ответ Telegram."""
    text, mtype, media = draft["text"], draft["media_type"], draft["media"]
    if mtype in ("photo", "video") and media and len(text) <= CAPTION_LIMIT:
        method = "sendPhoto" if mtype == "photo" else "sendVideo"
        return tg.call(method, {"chat_id": chat_id, mtype: media, "caption": text,
                                "parse_mode": "HTML", "reply_markup": reply_markup}, timeout=60)
    # Длинный текст или нет медиа — обычное сообщение; превью ссылки покажет картинку оригинала
    params = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "reply_markup": reply_markup}
    if draft["link"]:
        params["link_preview_options"] = {"url": draft["link"], "prefer_large_media": True}
    else:
        params["link_preview_options"] = {"is_disabled": True}
    return tg.call("sendMessage", params)


def _draft_kb(did):
    return {"inline_keyboard": [
        [{"text": "✅ Опубликовать", "callback_data": f"dr_pub_{did}"}],
        [{"text": "🔁 Другой вариант", "callback_data": f"dr_regen_{did}"},
         {"text": "⏭ Пропустить", "callback_data": f"dr_skip_{did}"}],
    ]}


def _show_draft(did, header):
    d = storage.get_draft(did)
    for admin_id in config.ADMIN_CHAT_IDS:
        tg.send_message(admin_id, header)
        r = _send(admin_id, d, _draft_kb(did))
        if not (r and r.get("ok")):
            logger.warning("Черновик №%s не показан администратору %s: %s", did, admin_id, (r or {}).get("description"))


def _publish(did):
    d = storage.get_draft(did)
    r = _send(config.CHANNEL_ID, d, channel.order_button())
    if r and r.get("ok"):
        storage.update_draft(did, status="published", channel_msg_id=r["result"]["message_id"])
        storage.log_event(0, "post")
        return True, r["result"]["message_id"]
    return False, (r or {}).get("description", "нет ответа")


def new_draft(source, item_id, source_name, original, link, media_type=None, media=None,
              extra_links=(), auto=False):
    text = _compose(original, source_name, link, extra_links)
    did = storage.add_draft(source, item_id, link, original, text, media_type, media)
    if auto:
        ok, res = _publish(did)
        for admin_id in config.ADMIN_CHAT_IDS:
            if ok:
                tg.send_message(admin_id, f"📢 Опубликовано в канале из {html.escape(source_name)}: "
                                          f"<a href=\"{_post_url(res)}\">открыть</a>",
                                {"inline_keyboard": [[{"text": "🗑 Удалить из канала", "callback_data": f"dr_del_{did}"}]]})
            else:
                tg.send_message(admin_id, f"⚠️ Не удалось опубликовать пост из {html.escape(source_name)}: "
                                          f"{html.escape(str(res))}")
        if not ok:
            storage.update_draft(did, status="pending")
            _show_draft(did, "Черновик для ручной публикации:")
    else:
        _show_draft(did, f"📝 <b>Новый пост для канала</b> — из {html.escape(source_name)}. Публикуем?")
    return did


def _post_url(msg_id):
    return f"https://t.me/{config.CHANNEL_ID.lstrip('@')}/{msg_id}" if config.CHANNEL_ID.startswith("@") else "#"


# ---------- проверка источников ----------

def _process(key, source_name, title, posts, backfill, bf_callback):
    first_time = not storage.source_known(key)
    fresh = [p for p in posts if not storage.is_seen(key, p["id"])]
    if first_time and not backfill:
        # Первое подключение: старые публикации не переносим, чтобы не завалить канал
        for p in posts:
            storage.mark_seen(key, p["id"])
        storage.mark_seen(key, "_connected")
        for admin_id in config.ADMIN_CHAT_IDS:
            tg.send_message(admin_id, f"🔗 Подключён источник <b>{html.escape(title)}</b>: найдено публикаций — "
                                      f"{len(posts)}. Новые буду готовить для канала автоматически.",
                            {"inline_keyboard": [[{"text": "📥 Подготовить 3 последние", "callback_data": bf_callback}]]})
        return 0
    if backfill:
        fresh = [p for p in posts if not p.get("pinned")][-backfill:]
    n = 0
    for p in fresh:
        storage.mark_seen(key, p["id"])
        if (not p["text"].strip() and not p.get("photo")) or storage.has_draft(key, p["id"]):
            continue
        if ai.is_available() and not ai.is_relevant(p["text"]):
            logger.info("Пропущена личная публикация %s %s", key, p["id"])
            continue
        new_draft(key, p["id"], source_name, p["text"], p["link"],
                  "photo" if p.get("photo") else None, p.get("photo"), p.get("extra_links", ()),
                  auto=config.AUTO_PUBLISH and not backfill and source_name != "Instagram")
        n += 1
    return n


def check_vk(src, backfill=0):
    idx = config.VK_SOURCES.index(src) if src in config.VK_SOURCES else 0
    return _process("vk:" + src, "ВКонтакте", f"ВКонтакте: {src}", vk_posts(src, count=max(10, backfill)),
                    backfill, f"src_bf_{idx}")


# ---------- ленты RSS/Atom/JSON (без ключей) ----------

def _strip_html(s: str):
    img = re.search(r'<img[^>]+src="([^"]+)"', s or "")
    text = re.sub(r"<br\s*/?>|</p>", "\n", s or "", flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return re.sub(r"\n{3,}", "\n\n", text).strip(), (img.group(1) if img else None)


def _name_for(url: str, fallback: str) -> str:
    host = urlparse(url or "").netloc
    if "instagram" in (url or "").lower():
        return "Instagram"
    if "vk.com" in host or "vk.ru" in host:
        return "ВКонтакте"
    if "t.me" in host:
        return "Telegram"
    return fallback or host or "источник"


def feed_posts(url: str):
    r = requests.get(url, timeout=20, headers={"User-Agent": "OasisDanceBot/1.0"})
    r.raise_for_status()
    body = r.text.strip()
    if body.startswith("{"):
        data = json.loads(body)
        name = data.get("source", "")
        items = [{"id": str(it["id"]), "date": it.get("date", 0), "text": it.get("text", ""),
                  "link": it.get("link", ""), "photo": it.get("photo")} for it in data.get("items", [])]
        items.sort(key=lambda p: p["date"])
        return name or _name_for(items[0]["link"] if items else "", ""), items
    root = ET.fromstring(r.content)
    ns = {"a": "http://www.w3.org/2005/Atom", "media": "http://search.yahoo.com/mrss/"}
    feed_title = root.findtext("channel/title") or root.findtext("a:title", namespaces=ns) or ""
    posts = []
    for it in root.findall("channel/item"):
        text, img = _strip_html(it.findtext("description") or it.findtext("title") or "")
        enc = it.find("enclosure")
        media = it.find("media:content", ns)
        if media is None:
            media = it.find("media:thumbnail", ns)
        photo = None
        if enc is not None and "image" in (enc.get("type") or "image"):
            photo = enc.get("url")
        if not photo and media is not None:
            photo = media.get("url")
        photo = photo or img
        link = it.findtext("link") or ""
        posts.append({"id": it.findtext("guid") or link, "text": text, "link": link, "photo": photo})
    for e in root.findall("a:entry", ns):
        link_el = e.find("a:link", ns)
        link = link_el.get("href") if link_el is not None else ""
        text, img = _strip_html(e.findtext("a:content", namespaces=ns) or e.findtext("a:summary", namespaces=ns)
                                or e.findtext("a:title", namespaces=ns) or "")
        posts.append({"id": e.findtext("a:id", namespaces=ns) or link, "text": text, "link": link, "photo": img})
    posts.reverse()  # в лентах новые сверху — обрабатываем от старых к новым
    return _name_for(posts[-1]["link"] if posts else "", feed_title), posts


def check_feed(url, backfill=0):
    idx = config.FEED_SOURCES.index(url) if url in config.FEED_SOURCES else 0
    name, posts = feed_posts(url)
    return _process("feed:" + url, name, f"{name} (лента)", posts, backfill, f"src_bff_{idx}")


def check_all():
    if not _check_lock.acquire(blocking=False):
        return
    try:
        total, errors = 0, []
        for src in (config.VK_SOURCES if config.VK_SERVICE_TOKEN else []):
            try:
                total += check_vk(src)
            except Exception as e:
                errors.append(f"{src}: {e}")
                logger.error("Ошибка проверки ВК %s: %s", src, e)
        for url in config.FEED_SOURCES:
            try:
                total += check_feed(url)
            except Exception as e:
                errors.append(f"лента {urlparse(url).netloc}: {e}")
                logger.error("Ошибка проверки ленты %s: %s", url, e)
        if not config.VK_SERVICE_TOKEN:
            errors.append("ВКонтакте не проверяется — нет ключа VK_SERVICE_TOKEN")
        last_check.update(time=time.time(),
                          result=f"новых публикаций: {total}" + (f"; ошибки: {'; '.join(errors)}" if errors else ""))
    finally:
        _check_lock.release()


# ---------- пересланные боту посты ----------

def from_forward(admin_id, msg) -> bool:
    """Администратор переслал боту пост (например, из Telegram) — делаем черновик для канала."""
    origin = msg.get("forward_origin")
    if not origin:
        return False
    original = msg.get("text") or msg.get("caption") or ""
    media_type = media = None
    if msg.get("photo"):
        media_type, media = "photo", msg["photo"][-1]["file_id"]
    elif msg.get("video"):
        media_type, media = "video", msg["video"]["file_id"]
    if not original and not media:
        tg.send_message(admin_id, "В пересланном сообщении нет текста, фото или видео.")
        return True
    link = ""
    if origin.get("type") == "channel" and (origin.get("chat") or {}).get("username"):
        link = f"https://t.me/{origin['chat']['username']}/{origin.get('message_id')}"
    tg.typing(admin_id)
    new_draft("forward", f"{admin_id}_{msg['message_id']}", "Telegram", original, link, media_type, media)
    return True


# ---------- кнопки администратора ----------

def handle_callback(admin_id, message_id, data):
    """Кнопки dr_* и src_*. Возвращает короткий текст для всплывающего уведомления."""
    if data.startswith("src_bf_"):
        try:
            src = config.VK_SOURCES[int(data[7:])]
        except (ValueError, IndexError):
            return "Источник не найден"
        n = check_vk(src, backfill=3)
        return f"Готовлю черновики: {n}"
    if data.startswith("src_bff_"):
        try:
            url = config.FEED_SOURCES[int(data[8:])]
        except (ValueError, IndexError):
            return "Источник не найден"
        n = check_feed(url, backfill=3)
        return f"Готовлю черновики: {n}"
    if data == "src_menu":
        tg.edit_message(admin_id, message_id, *status_screen())
        return None
    if data == "src_check":
        check_all()
        tg.send_message(admin_id, "🔄 Проверка завершена: " + html.escape(last_check["result"]))
        return None

    m = re.fullmatch(r"dr_(pub|regen|skip|del)_(\d+)", data)
    if not m:
        return None
    action, did = m.group(1), int(m.group(2))
    d = storage.get_draft(did)
    if not d:
        return "Черновик не найден"
    done_kb = lambda label: {"inline_keyboard": [[{"text": label, "callback_data": "dr_noop"}]]}  # noqa: E731

    if action == "del":
        if d["status"] == "published" and d["channel_msg_id"]:
            tg.call("deleteMessage", {"chat_id": config.CHANNEL_ID, "message_id": d["channel_msg_id"]})
            storage.update_draft(did, status="deleted")
            tg.call("editMessageReplyMarkup", {"chat_id": admin_id, "message_id": message_id,
                                               "reply_markup": done_kb("🗑 Удалено из канала")})
            return "Удалено"
        return "Пост уже удалён"
    if d["status"] != "pending":
        tg.call("editMessageReplyMarkup", {"chat_id": admin_id, "message_id": message_id,
                                           "reply_markup": done_kb("Уже обработан")})
        return "Этот черновик уже обработан"
    if action == "skip":
        storage.update_draft(did, status="skipped")
        tg.call("editMessageReplyMarkup", {"chat_id": admin_id, "message_id": message_id,
                                           "reply_markup": done_kb("⏭ Пропущено")})
        return "Пропущено"
    if action == "regen":
        tg.typing(admin_id)
        name = ("ВКонтакте" if d["source"].startswith("vk:") else
                _name_for(d["link"], "Telegram") if d["source"].startswith("feed:") else "Telegram")
        storage.update_draft(did, text=_compose(d["original"], name, d["link"]))
        tg.call("editMessageReplyMarkup", {"chat_id": admin_id, "message_id": message_id,
                                           "reply_markup": done_kb("🔁 Заменён новым вариантом")})
        r = _send(admin_id, storage.get_draft(did), _draft_kb(did))
        return None if r and r.get("ok") else "Не удалось показать новый вариант"
    if action == "pub":
        ok, res = _publish(did)
        if ok:
            tg.call("editMessageReplyMarkup", {"chat_id": admin_id, "message_id": message_id, "reply_markup": {
                "inline_keyboard": [[{"text": "✅ Опубликовано — открыть", "url": _post_url(res)}],
                                    [{"text": "🗑 Удалить из канала", "callback_data": f"dr_del_{did}"}]]}})
            return "Опубликовано"
        tg.send_message(admin_id, f"⚠️ Не удалось опубликовать: {html.escape(str(res))}")
        return None
    return None


def status_screen():
    vk = "✅ подключён" if config.VK_SERVICE_TOKEN else "❌ нет ключа (VK_SERVICE_TOKEN в .env)"
    when = time.strftime("%d.%m %H:%M", time.localtime(last_check["time"])) if last_check["time"] else "—"
    text = ("🔗 <b>Источники постов для канала</b>\n\n"
            f"ВКонтакте: {vk}\n"
            f"Страницы: {html.escape(', '.join(config.VK_SOURCES) or '—')}\n"
            f"Проверка каждые {config.SOURCE_CHECK_MINUTES} мин., последняя: {when} — {html.escape(last_check['result'])}\n"
            f"Режим: {'публиковать сразу' if config.AUTO_PUBLISH else 'черновик на одобрение'}\n"
            f"Черновиков ждут решения: {storage.count_drafts('pending')}\n\n"
            f"Ленты (Instagram и др.): {len(config.FEED_SOURCES)}\n"
            "Личные публикации ИИ отсеивает; посты из Instagram всегда идут на одобрение.\n\n"
            "Telegram: перешлите боту любой пост — он подготовит его для канала.")
    rows = [[{"text": "🔄 Проверить сейчас", "callback_data": "src_check"}]]
    for i, src in enumerate(config.VK_SOURCES[:5]):
        rows.append([{"text": f"📥 3 последние из {src}"[:40], "callback_data": f"src_bf_{i}"}])
    for i, url in enumerate(config.FEED_SOURCES[:5]):
        rows.append([{"text": f"📥 3 последние из ленты {_name_for(url, urlparse(url).netloc)}"[:40],
                      "callback_data": f"src_bff_{i}"}])
    rows.append([{"text": "🗂 Кабинет", "callback_data": "adm_menu"}])
    return text, {"inline_keyboard": rows}
