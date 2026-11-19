"""
ИИ-ассистент на Google Gemini через REST API.
Сведения о коллективе берутся из knowledge.md.
"""
import logging
import os
import re
import time

import requests

import config

logger = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
API_LIST = "https://generativelanguage.googleapis.com/v1beta/models"
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]
KNOWLEDGE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "knowledge.md")


def _knowledge() -> str:
    try:
        with open(KNOWLEDGE_FILE, encoding="utf-8") as f:
            return "\n".join(l for l in f.read().splitlines() if not l.lstrip().startswith("#") or l.startswith("##"))
    except FileNotFoundError:
        return ""


def _system_prompt() -> str:
    return (
        "Ты — вежливый виртуальный ассистент шоу-балета Oasis Dance в Telegram-боте. "
        "Помогаешь потенциальным заказчикам: отвечаешь о коллективе, программах, мероприятиях и порядке заказа.\n\n"
        "Сведения о коллективе (используй только их):\n" + _knowledge() + "\n\n"
        "Правила:\n"
        "- Отвечай на русском, кратко: 2–5 предложений, обычным текстом без Markdown и без списков со звёздочками.\n"
        "- Не выдумывай цены, телефоны, даты, имена танцовщиц и другие факты, которых нет в сведениях. "
        "Если сведений нет — честно скажи и предложи оставить заявку или написать менеджеру.\n"
        "- Если человек хочет заказать выступление или узнать цену на свою дату — предложи нажать "
        "«Заказать выступление» или «Перезвоните мне» под этим сообщением.\n"
        "- Не обсуждай темы, не связанные с шоу-балетом и мероприятиями; мягко возвращай разговор к ним.\n"
        "- Ты ассистент, а не человек; не обещай ничего от имени менеджера."
    )


def _post_prompt() -> str:
    return (
        "Ты — копирайтер Telegram-канала шоу-балета Oasis Dance. Пишешь живые, тёплые посты для подписчиков "
        "и потенциальных заказчиков.\n\nСведения о коллективе (используй только их):\n" + _knowledge() + "\n\n"
        "Правила:\n"
        "- Русский язык, 400–900 знаков, короткие абзацы, 2–5 уместных эмодзи.\n"
        "- Обычный текст без Markdown, без хэштегов-простыней (не больше 3 хэштегов).\n"
        "- Не выдумывай цены, даты, места выступлений, имена и факты, которых нет в сведениях или в задании.\n"
        "- Заканчивай мягким призывом: оставить заявку через кнопку «Заказать выступление» под постом.\n"
        "- Выдай только текст поста, без пояснений."
    )


def rewrite_post(original: str, source_name: str) -> str:
    """Переписывает публикацию из соцсети в пост для Telegram-канала, сохраняя факты и ссылки."""
    system = (
        "Ты — редактор Telegram-канала шоу-балета Oasis Dance. Тебе дают публикацию руководителя коллектива "
        f"из {source_name}. Перепиши её в пост для Telegram-канала.\n\n"
        "Сведения о коллективе для контекста:\n" + _knowledge() + "\n\n"
        "Правила:\n"
        "- Сохрани смысл, все факты, даты, места, цены и имена ровно как в исходном тексте.\n"
        "- Все ссылки (URL) из исходного текста перенеси без изменений.\n"
        "- Ничего не добавляй от себя: никаких новых фактов, цен, дат и обещаний.\n"
        "- Русский язык, 200–800 знаков, короткие абзацы, 1–4 уместных эмодзи, без Markdown.\n"
        "- Не пиши, что это пересказ или репост; ссылку на оригинал добавят отдельно.\n"
        "- Убери служебную подпись с телефоном и мессенджерами для брони, а также отметки @аккаунтов "
        "(операторы, площадки, мастера): для заказа под постом есть кнопка.\n"
        "- Выдай только текст поста."
    )
    return _call(system, [{"role": "user", "parts": [{"text": original[:6000]}]}], temperature=0.6, max_tokens=3000,
                 read_timeout=90, total_limit=150)


def is_relevant(text: str) -> bool:
    """True, если публикация про шоу-балет (выступления, номера, костюмы, афиши), а не личная."""
    if not text.strip():
        return True
    system = ("Определи, относится ли публикация к работе шоу-балета: выступления, номера, костюмы, репетиции, "
              "мероприятия, афиши, набор артистов, поздравления от коллектива. Личные и семейные публикации "
              "(дети, отдых, здоровье, личная жизнь) — не относятся. Ответь одним словом: ДА или НЕТ.")
    try:
        ans = _call(system, [{"role": "user", "parts": [{"text": text[:3000]}]}], temperature=0, max_tokens=200)
        return not ans.strip().upper().startswith("НЕТ")
    except Exception:
        return True  # при сбое не теряем публикацию: решит администратор


def generate_post(topic: str) -> str:
    return _call(_post_prompt(), [{"role": "user", "parts": [{"text": "Тема поста: " + topic}]}],
                 temperature=0.8, max_tokens=3000, read_timeout=90, total_limit=150)


def is_available() -> bool:
    return bool(config.GEMINI_API_KEY or config.AI_FALLBACK_KEY)


def generate(message: str, history: list = None) -> str:
    """Возвращает ответ ассистента. history — список {'role': 'user'|'assistant', 'content': str}."""
    contents = []
    for m in (history or [])[-20:]:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
    contents.append({"role": "user", "parts": [{"text": message[:2000]}]})
    return _call(_system_prompt(), contents, temperature=0.5, max_tokens=2048)


_models_cache = {"time": 0, "list": []}
_SKIP = ("image", "tts", "audio", "live", "embedding", "aqa", "learnlm", "robotics", "computer")


def _available_models():
    """Список flash-моделей, доступных ключу, в порядке предпочтения. Обновляется раз в 6 часов."""
    if time.time() - _models_cache["time"] < 6 * 3600 and _models_cache["list"]:
        return _models_cache["list"]
    names = []
    try:
        r = requests.get(API_LIST, params={"key": config.GEMINI_API_KEY, "pageSize": 200}, timeout=(5, 15))
        for m in r.json().get("models", []):
            name = m.get("name", "").replace("models/", "")
            if ("generateContent" in m.get("supportedGenerationMethods", []) and "flash" in name
                    and not any(x in name for x in _SKIP)):
                names.append(name)
    except Exception as e:
        logger.error("Не удалось получить список моделей Gemini: %s", e)

    def rank(n):
        # Сначала стабильные модели с номером версии (они реже перегружены), затем псевдонимы -latest,
        # затем preview; облегчённые lite — в конце
        return ("lite" in n, "preview" in n or "exp" in n, n.endswith("-latest"),
                [-int(x) for x in re.findall(r"\d+", n)][:3])
    names.sort(key=rank)
    _models_cache.update(time=time.time(), list=names)
    return names


def _candidates():
    found = _available_models()
    preferred = [] if config.GEMINI_MODEL in ("", "auto") else [config.GEMINI_MODEL]
    order = preferred + found + FALLBACK_MODELS
    seen, result = set(), []
    for m in order:
        if m not in seen and (not found or m in found or m in preferred):
            seen.add(m)
            result.append(m)
    return result[:5]


# Запас токенов большой: новые модели Gemini тратят часть лимита на «размышления», длину задаёт промпт.
def _call(system: str, contents: list, temperature: float, max_tokens: int,
          read_timeout: int = 30, total_limit: int = 40) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }
    started = time.time()
    models = _candidates() if config.GEMINI_API_KEY else []
    # После круга по всем моделям — ещё одна попытка с первыми двумя через паузу
    attempts = models + (["_pause"] + models[:2] if len(models) > 1 else [])
    for model in attempts:
        if model == "_pause":
            if time.time() - started > total_limit - 15:
                break
            time.sleep(3)
            continue
        if time.time() - started > total_limit:
            break  # не держим человека дольше заданного времени в сумме
        try:
            r = requests.post(API.format(model=model), params={"key": config.GEMINI_API_KEY},
                              json=body, timeout=(5, read_timeout))
            if r.status_code != 200:
                # 503 — модель перегружена, 429 — лимит, 404 — модели нет: пробуем следующую
                logger.warning("Gemini %s: %s %s", model, r.status_code, r.text[:200].replace("\n", " "))
                continue
            parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                logger.info("ИИ ответил: Gemini %s за %.1f с", model, time.time() - started)
                return text.replace("**", "")
        except requests.Timeout:
            logger.error("Gemini %s не ответил за %s секунд", model, read_timeout)
            continue  # пробуем следующую модель, если общий предел ещё не исчерпан
        except Exception as e:
            logger.error("Ошибка Gemini %s: %s", model, e)
    if config.AI_FALLBACK_KEY:
        left = max(10, total_limit - (time.time() - started))
        try:
            return _call_fallback(system, contents, timeout=min(read_timeout, left))
        except Exception as e:
            logger.error("Запасной ИИ не ответил: %s", e)
    raise RuntimeError("ИИ не вернул ответ")


# ---------- запасной ИИ (протокол OpenAI: OpenAI, OpenRouter, DeepSeek и др.) ----------

_fallback_model = {"name": None}


def _fallback_model_name():
    if config.AI_FALLBACK_MODEL != "auto":
        return config.AI_FALLBACK_MODEL
    if "openrouter.ai" in config.AI_FALLBACK_URL:
        return "openrouter/free"  # бесплатный маршрутизатор OpenRouter сам выбирает свободную модель
    if _fallback_model["name"]:
        return _fallback_model["name"]
    name = "gpt-4o-mini"
    if "api.openai.com" in config.AI_FALLBACK_URL:
        try:
            r = requests.get(config.AI_FALLBACK_URL + "/models", timeout=(5, 15),
                             headers={"Authorization": f"Bearer {config.AI_FALLBACK_KEY}"})
            ids = [m["id"] for m in r.json().get("data", [])]
            minis = [i for i in ids if re.fullmatch(r"gpt-[\d.]+o?-mini", i)]
            if minis:
                minis.sort(key=lambda i: [float(x) for x in re.findall(r"\d+(?:\.\d+)?", i)], reverse=True)
                name = minis[0]
        except Exception as e:
            logger.warning("Не удалось получить список моделей запасного ИИ: %s", e)
    _fallback_model["name"] = name
    return name


def _call_fallback(system: str, contents: list, timeout: float) -> str:
    messages = [{"role": "system", "content": system}]
    for c in contents:
        role = "assistant" if c.get("role") == "model" else "user"
        messages.append({"role": role, "content": "".join(p.get("text", "") for p in c.get("parts", []))})
    model = _fallback_model_name()
    r = requests.post(config.AI_FALLBACK_URL + "/chat/completions", timeout=(5, timeout),
                      headers={"Authorization": f"Bearer {config.AI_FALLBACK_KEY}"},
                      json={"model": model, "messages": messages})
    if r.status_code != 200:
        raise RuntimeError(f"{model}: {r.status_code} {r.text[:200]}")
    text = (r.json()["choices"][0]["message"].get("content") or "").strip()
    if not text:
        raise RuntimeError(f"{model}: пустой ответ")
    logger.info("ИИ ответил: запасной ИИ %s", model)
    return text.replace("**", "")
