#!/usr/bin/env bash
# Установка бота Oasis Dance на сервер Ubuntu/Debian. Запускать от root из папки с файлами бота:
#   bash install.sh
set -e

APP=/opt/oasis-bot
SRC="$(cd "$(dirname "$0")" && pwd)"

echo "== 1/5 Устанавливаю Python =="
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip >/dev/null

echo "== 2/5 Создаю пользователя и папку =="
id oasisbot >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin oasisbot
mkdir -p "$APP/data"
for f in bot.py config.py tg.py ai.py order_form.py admin.py storage.py channel.py sources.py knowledge.md requirements.txt; do
  cp "$SRC/$f" "$APP/"
done

echo "== 3/5 Ставлю зависимости =="
[ -d "$APP/venv" ] || python3 -m venv "$APP/venv"
"$APP/venv/bin/pip" install -q --upgrade pip
"$APP/venv/bin/pip" install -q -r "$APP/requirements.txt"

echo "== 4/5 Настройки =="
if [ ! -f "$APP/.env" ]; then
  cp "$SRC/.env.example" "$APP/.env"
  read -r -p "Вставьте токен бота (TELEGRAM_BOT_TOKEN): " TOKEN
  sed -i "s|^TELEGRAM_BOT_TOKEN=.*|TELEGRAM_BOT_TOKEN=$TOKEN|" "$APP/.env"
  read -r -p "Ключ Gemini (Enter — пропустить): " GKEY
  [ -n "$GKEY" ] && sed -i "s|^GEMINI_API_KEY=.*|GEMINI_API_KEY=$GKEY|" "$APP/.env"
fi
chmod 600 "$APP/.env"
chown -R oasisbot:oasisbot "$APP"

echo "== 5/5 Запускаю службу =="
cp "$SRC/oasis-bot.service" /etc/systemd/system/oasis-bot.service
systemctl daemon-reload
systemctl enable --now oasis-bot
systemctl restart oasis-bot
sleep 5
systemctl --no-pager --lines=15 status oasis-bot || true

echo
echo "Готово. Полезные команды:"
echo "  journalctl -u oasis-bot -f        — смотреть журнал в реальном времени"
echo "  systemctl restart oasis-bot       — перезапустить"
echo "  nano $APP/.env                    — изменить настройки (потом перезапустить)"
