"""
Поиск площадки мероприятия по названию или адресу (OpenStreetMap / Nominatim, без ключей).
Если поиск ничего не нашёл, ИИ переформулирует запрос, а результат снова проверяется по карте —
поэтому выдуманные адреса в заявку не попадают.
"""
import logging
import threading
import time

import requests

import ai

logger = logging.getLogger(__name__)

SEARCH_URL = "https://nominatim.openstreetmap.org/search"
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "OasisDanceBot/1.0 (Telegram bot of a dance show; low volume)",
           "Accept-Language": "ru"}
SPB_VIEWBOX = "29.4,60.3,31.0,59.6"  # приоритет — Санкт-Петербург и окрестности
_lock = threading.Lock()
_last = [0.0]


def _get(url, params):
    # Правила Nominatim: не чаще 1 запроса в секунду
    with _lock:
        wait = 1.1 - (time.time() - _last[0])
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
    r = requests.get(url, params=params, headers=HEADERS, timeout=(5, 10))
    r.raise_for_status()
    return r.json()


def _label(item) -> str:
    a = item.get("address") or {}
    name = item.get("name") or ""
    street = a.get("road") or a.get("pedestrian") or a.get("square") or ""
    house = a.get("house_number") or ""
    city = a.get("city") or a.get("town") or a.get("village") or a.get("state") or ""
    parts = []
    if name and name not in street:
        parts.append(name)
    if street:
        parts.append(f"{street} {house}".strip())
    if city:
        parts.append(city)
    label = ", ".join(parts) or item.get("display_name", "")
    return label[:120]


def search(query: str, limit=5):
    """Список мест: [{"label", "lat", "lon"}]."""
    try:
        items = _get(SEARCH_URL, {"q": query, "format": "jsonv2", "addressdetails": 1, "limit": limit,
                                  "countrycodes": "ru,by,kz", "viewbox": SPB_VIEWBOX, "bounded": 0})
    except Exception as e:
        logger.warning("Поиск адреса не удался: %s", e)
        return []
    seen, out = set(), []
    for it in items:
        label = _label(it)
        if label and label not in seen:
            seen.add(label)
            out.append({"label": label, "lat": float(it["lat"]), "lon": float(it["lon"])})
    return out


def search_smart(query: str):
    """Обычный поиск; если пусто — ИИ подсказывает, как искать, и результат снова проверяется по карте."""
    found = search(query)
    if found or not ai.is_available():
        return found, False
    try:
        hint = ai._call(
            "Ты помогаешь найти место проведения мероприятия на карте OpenStreetMap. "
            "По описанию пользователя составь поисковый запрос: название заведения или улица с домом и город "
            "(если город не указан — Санкт-Петербург). Не выдумывай адрес, если не уверен — оставь название и "
            "город. Ответь только запросом одной строкой.",
            [{"role": "user", "parts": [{"text": query[:300]}]}], temperature=0, max_tokens=300)
        hint = hint.strip().strip('"«»').splitlines()[0][:150]
    except Exception as e:
        logger.warning("ИИ не помог с адресом: %s", e)
        return [], False
    if not hint or hint.lower() == query.lower():
        return [], False
    return search(hint), True


def reverse(lat: float, lon: float):
    try:
        it = _get(REVERSE_URL, {"lat": lat, "lon": lon, "format": "jsonv2", "addressdetails": 1, "zoom": 18})
        return _label(it) or f"{lat:.5f}, {lon:.5f}"
    except Exception as e:
        logger.warning("Не удалось определить адрес по точке: %s", e)
        return f"{lat:.5f}, {lon:.5f}"


def map_url(lat, lon) -> str:
    return f"https://yandex.ru/maps/?pt={lon},{lat}&z=16&l=map"
