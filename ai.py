"""
ИИ-ассистент на Google Gemini через REST API (без устаревшей библиотеки google-generativeai).
"""
import logging

import requests

import config

logger = logging.getLogger(__name__)

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
FALLBACK_MODELS = ["gemini-flash-latest", "gemini-flash-lite-latest"]


def _system_prompt() -> str:
    contacts = [f"- Менеджер в Telegram: @{config.MANAGER_USERNAME}"]
    if config.MANAGER_PHONE:
        contacts.append(f"- Телефон: {config.MANAGER_PHONE}")
    if config.MANAGER_EMAIL:
        contacts.append(f"- Email: {config.MANAGER_EMAIL}")
    contacts.append(f"- Instagram: {config.INSTAGRAM_URL}")
    contacts.append(f"- ВКонтакте: {config.VK_URL}")
    contacts.append(f"- Telegram-канал: {config.CHANNEL_URL}")
    return (
        "Ты — виртуальный ассистент шоу-балета Oasis Dance (ранее Ebru Dance). "
        "Помогаешь клиентам: отвечаешь на вопросы о коллективе, выступлениях, репертуаре и заказе шоу.\n\n"
        "Сведения о шоу-балете:\n"
        "- Профессиональный танцевальный коллектив.\n"
        "- В репертуаре восточные, латиноамериканские, современные, народные танцы и шоу-номера.\n"
        "- Выступает на корпоративах, свадьбах, днях рождения, частных праздниках.\n"
        "- Возможны индивидуальные номера под тематику мероприятия.\n"
        "- Стоимость зависит от числа танцовщиц и номеров; точную цену называет менеджер.\n\n"
        "Контакты:\n" + "\n".join(contacts) + "\n\n"
        "Отвечай вежливо, кратко (до 5–6 предложений), на русском языке, обычным текстом без разметки Markdown. "
        "Не выдумывай факты, цены, даты и состав — если сведений нет, предложи связаться с менеджером. "
        "Для заказа выступления предлагай кнопку «Заказать выступление» или менеджера."
    )


def is_available() -> bool:
    return bool(config.GEMINI_API_KEY)


def generate(message: str, history: list = None) -> str:
    """Возвращает ответ ассистента. history — список {'role': 'user'|'assistant', 'content': str}."""
    contents = []
    for m in (history or [])[-20:]:
        role = "model" if m.get("role") == "assistant" else "user"
        contents.append({"role": role, "parts": [{"text": m.get("content", "")}]})
    contents.append({"role": "user", "parts": [{"text": message}]})

    body = {
        "systemInstruction": {"parts": [{"text": _system_prompt()}]},
        "contents": contents,
        "generationConfig": {"temperature": 0.6, "maxOutputTokens": 800},
    }

    models = [config.GEMINI_MODEL] + [m for m in FALLBACK_MODELS if m != config.GEMINI_MODEL]
    for model in models:
        try:
            r = requests.post(
                API.format(model=model),
                params={"key": config.GEMINI_API_KEY},
                json=body,
                timeout=60,
            )
            if r.status_code != 200:
                logger.error("Gemini %s: %s %s", model, r.status_code, r.text[:300])
                continue
            data = r.json()
            parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in parts).strip()
            if text:
                return text
        except Exception as e:
            logger.error("Ошибка Gemini %s: %s", model, e)
    raise RuntimeError("Gemini не вернул ответ")
