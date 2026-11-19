"""
Настройки бота шоу-балета Oasis Dance.
Все секреты и изменяемые данные берутся из файла .env (см. .env.example).
"""
import os


def _load_env_file(path: str = None) -> None:
    """Загружает переменные из файла .env рядом с ботом (без сторонних библиотек)."""
    path = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)


_load_env_file()

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Числовые ID администраторов через запятую (узнать свой ID: написать боту @userinfobot)
ADMIN_CHAT_IDS = [
    int(x) for x in os.environ.get("ADMIN_CHAT_IDS", "").replace(" ", "").split(",")
    if x.lstrip("-").isdigit()
]

# --- ИИ-ассистент (Google Gemini) ---
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# Псевдоним «последняя Flash-модель»: не устаревает при выводе конкретных версий
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-latest")

# --- Контакты коллектива ---
MANAGER_USERNAME = os.environ.get("MANAGER_USERNAME", "EbruBekker").lstrip("@")
MANAGER_PHONE = os.environ.get("MANAGER_PHONE", "")
MANAGER_EMAIL = os.environ.get("MANAGER_EMAIL", "")
WORK_HOURS = os.environ.get("WORK_HOURS", "ежедневно с 10:00 до 20:00")

INSTAGRAM_URL = os.environ.get("INSTAGRAM_URL", "https://www.instagram.com/ebru.dance")
VK_URL = os.environ.get("VK_URL", "https://vk.com/oasis_dance_show")
CHANNEL_URL = os.environ.get("CHANNEL_URL", "https://t.me/oasis_dance_show")
# Канал, куда бот публикует посты (бот должен быть его администратором)
CHANNEL_ID = os.environ.get("CHANNEL_ID", "@oasis_dance_show")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "oasisdance_show_bot")  # уточняется при запуске

MANAGER_URL = f"https://t.me/{MANAGER_USERNAME}"

# --- Ограничения ИИ (защита ключа Gemini от перерасхода) ---
AI_LIMIT_PER_USER_HOUR = int(os.environ.get("AI_LIMIT_PER_USER_HOUR", "20"))
AI_LIMIT_PER_DAY = int(os.environ.get("AI_LIMIT_PER_DAY", "500"))

# Через сколько минут бездействия незавершённая заявка считается брошенной
ABANDON_MINUTES = int(os.environ.get("ABANDON_MINUTES", "30"))

# Название оператора персональных данных для текста согласия
OPERATOR_NAME = os.environ.get("OPERATOR_NAME", "шоу-балет Oasis Dance")

# --- Источники постов для канала ---
# Сервисный ключ приложения ВКонтакте (dev.vk.com → ваше приложение → «Сервисный ключ доступа»)
VK_SERVICE_TOKEN = os.environ.get("VK_SERVICE_TOKEN", "")
# Страницы ВК через запятую: короткое имя (oasis_dance_show), id человека (id123) или группы (-123)
VK_SOURCES = [x.strip() for x in os.environ.get("VK_SOURCES", "oasis_dance_show").split(",") if x.strip()]
# Ленты публикаций без ключей: RSS/Atom (rss.app, vkrss.com и т.п.) или JSON (feeds/instagram.json в репозитории)
FEED_SOURCES = [x.strip() for x in os.environ.get(
    "FEED_SOURCES",
    "https://raw.githubusercontent.com/alexcomun85-star/oasis-dance-bot/main/feeds/instagram.json").split(",") if x.strip()]
# Пометка к постам из Instagram (пусто — без пометки)
INSTAGRAM_NOTE = os.environ.get("INSTAGRAM_NOTE", "Instagram принадлежит компании Meta, признанной экстремистской и запрещённой в РФ.")
SOURCE_CHECK_MINUTES = int(os.environ.get("SOURCE_CHECK_MINUTES", "20"))
# 1 — публиковать в канал сразу, 0 — сначала присылать черновик администратору на одобрение
AUTO_PUBLISH = os.environ.get("AUTO_PUBLISH", "0") == "1"

# --- Файлы данных ---
DATA_DIR = os.environ.get(
    "DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
)
os.makedirs(DATA_DIR, exist_ok=True)
