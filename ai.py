"""
ИИ-ассистент на Google Gemini через REST API.
Сведения о коллективе берутся из knowledge.md.
"""
import logging
import os

import requests

import config

logger = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
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
    return _call(system, [{"role": "user", "parts": [{"text": original[:6000]}]}], temperature=0.6, max_tokens=3000)


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
                 temperature=0.8, max_tokens=3000)


def is_available() -> bool:
    return bool(config.GEMINI_API_KEY)


def generate(message: str, history: list = None) -> str:
    """Возвращает ответ ассистента. history — список {'role': 'user'|'assistant', 'content': str}."""
    contents = []
    for m in (history or [])[-20:]:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
    contents.append({"role": "user", "parts": [{"text": message[:2000]}]})
    return _call(_system_prompt(), contents, temperature=0.5, max_tokens=2048)


# Запас токенов большой: новые модели Gemini тратят часть лимита на «размышления», длину задаёт промпт.
def _call(system: str, contents: list, temperature: float, max_tokens: int) -> str:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": contents,
        "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
    }

    models = [config.GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != config.GEMINI_MODEL]
    for model in models:
        try:
            r = requests.post(API.format(model=model), params={"key": config.GEMINI_API_KEY},
                              json=body, timeout=60)
            if r.status_code != 200:
                logger.error("Gemini %s: %s %s", model, r.status_code, r.text[:300])
                continue
            parts = r.json().get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text.replace("**", "")
        except Exception as e:
            logger.error("Ошибка Gemini %s: %s", model, e)
    raise RuntimeError("Gemini не вернул ответ")
