#!/usr/bin/env bash
# Установка hermes-gadget-minitoo на чужой хост с Hermes (пользовательский уровень, без root, кроме apt).
#   ./install.sh [--address AA:BB:CC:DD:EE:FF] [--server ws://127.0.0.1:8765/gadget] [--sdk-dir DIR] [--no-services] [--with-system]
# SDK: клонируется в <repo>/.sdk на закреплённый коммит и собирается (cmake); --sdk-dir берёт готовый чекаут SDK.
# Повторный запуск безопасен: config.json, drop-in'ы и юниты не перетираются без бэкапа.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SDK_DIR="" SDK_SHA="81f05a1006c6df726bf8433d30fd91ea4993fd5c"
ADDR="" SERVER="ws://127.0.0.1:8765/gadget" SERVICES=1 SYSTEM=0 NAME="MiniToo Gadget"
while [ $# -gt 0 ]; do
  case "$1" in
    --address) ADDR="$2"; shift 2;;
    --server) SERVER="$2"; shift 2;;
    --name) NAME="$2"; shift 2;;
    --sdk-dir) SDK_DIR="$2"; shift 2;;
    --no-services) SERVICES=0; shift;;
    --with-system) SYSTEM=1; shift;;
    -h|--help) sed -n 2,4p "$0"; exit 0;;
    *) echo "неизвестный аргумент: $1" >&2; exit 2;;
  esac
done

say() { printf '\033[1m==> %s\033[0m\n' "$*"; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "нет команды: $1 ($2)" >&2; MISSING=1; }; }

say "Проверка зависимостей"
MISSING=0
need python3 "apt install python3 python3-venv"
need bluetoothctl "apt install bluez"
need wpctl "apt install wireplumber pipewire (нужен PipeWire/WirePlumber)"
need pw-record "apt install pipewire-bin"
need git "apt install git"
[ -n "$SDK_DIR" ] || need cmake "apt install cmake build-essential (сборка ядра SDK)"
ldconfig -p | grep -q liblzo2 || { echo "нет liblzo2 (apt install liblzo2-2)" >&2; MISSING=1; }
ldconfig -p | grep -q libportaudio || echo "предупреждение: нет libportaudio2 (apt install libportaudio2) — см. docs/PORTABILITY.md" >&2
[ "$MISSING" = 0 ] || { echo "Поставьте недостающее и повторите." >&2; exit 1; }

say "Hermes Gadget SDK (ядро устройства)"
# Пакет SDK из pip не содержит нативной библиотеки и исходников ядра: нужен чекаут, в нём собирается libhgsim.
[ -n "$SDK_DIR" ] || SDK_DIR="$REPO/.sdk"
if [ ! -d "$SDK_DIR/.git" ]; then
  git clone -q https://github.com/Adolanium/hermes-gadget-sdk.git "$SDK_DIR"
  git -C "$SDK_DIR" checkout -q "$SDK_SHA"
fi
HEAD_SHA="$(git -C "$SDK_DIR" rev-parse HEAD 2>/dev/null || true)"
[ "$HEAD_SHA" = "$SDK_SHA" ] || echo "предупреждение: SDK в $SDK_DIR на $HEAD_SHA, ожидался $SDK_SHA (v0.4.1)" >&2

say "Виртуальное окружение"
[ -d "$REPO/.venv" ] || python3 -m venv "$REPO/.venv"
"$REPO/.venv/bin/pip" install -q --upgrade pip
# SDK в режиме разработки (repo_root указывает на чекаут), затем наш пакет без повторной установки SDK из git
"$REPO/.venv/bin/pip" install -q -e "$SDK_DIR"
"$REPO/.venv/bin/pip" install -q "sounddevice>=0.4.6,<0.6" "pytest>=8,<10" "ruff>=0.6"
"$REPO/.venv/bin/pip" install -q -e "$REPO" --no-deps
if [ -z "${HGSIM_LIBRARY:-}" ] && ! ls "$SDK_DIR"/build/host/libhgsim.so >/dev/null 2>&1; then
  say "Сборка ядра SDK (cmake, на слабом ПК несколько минут)"
  "$REPO/.venv/bin/hermes-gadget" build-sim
fi

say "config.json"
if [ ! -f "$REPO/config.json" ]; then
  cp "$REPO/config.template.json" "$REPO/config.json"
  python3 - "$REPO/config.json" "$ADDR" "$SERVER" "$NAME" <<'PY'
import json, sys
p, addr, server, name = sys.argv[1:5]
c = json.load(open(p))
c["server"], c["name"] = server, name
if addr:
    c["minitoo"]["address"] = addr.upper()
json.dump(c, open(p, "w"), indent=2, ensure_ascii=False)
PY
  echo "создан $REPO/config.json"
else
  echo "config.json уже есть — не трогаю"
fi
grep -q REAL_MAC_HERE "$REPO/config.json" && echo "ВАЖНО: укажите MAC колонки в config.json (minitoo.address) или запустите minitoo-autoaddr" >&2

if [ "$SERVICES" = 1 ]; then
  say "Пользовательские сервисы systemd"
  U="$HOME/.config/systemd/user"; mkdir -p "$U"
  for f in hermes-minitoo minitoo-talk-key minitoo-autoaddr; do
    src="$REPO/scripts/systemd/$f.service"; dst="$U/$f.service"
    # юниты используют %h/hermes-minitoo-gadget: если репозиторий лежит в другом месте — подставляем путь
    tmp="$(mktemp)"; sed "s#%h/hermes-minitoo-gadget#$REPO#g" "$src" > "$tmp"
    if [ -f "$dst" ] && ! cmp -s "$tmp" "$dst"; then cp "$dst" "$dst.bak"; echo "бэкап: $dst.bak"; fi
    install -m 644 "$tmp" "$dst"; rm -f "$tmp"
  done
  # опциональные настройки поведения — отдельным drop-in'ом, который не перетирается обновлением юнита
  D="$U/minitoo-talk-key.service.d"; mkdir -p "$D"
  [ -f "$D/10-options.conf" ] || install -m 644 "$REPO/scripts/systemd/minitoo-talk-key.options.conf" "$D/10-options.conf"
  # talk-key ищет config.json и venv в MINITOO_REPO (по умолчанию ~/hermes-minitoo-gadget):
  # указываем фактический каталог чекаута, иначе при другом имени каталога сервис не найдёт конфиг
  printf '[Service]\nEnvironment="MINITOO_REPO=%s"\n' "$REPO" > "$D/00-repo.conf"
  systemctl --user daemon-reload
  loginctl enable-linger "$USER" 2>/dev/null || echo "не удалось включить linger: сервисы не стартуют без входа пользователя" >&2
  echo "Юниты установлены, но не запущены. Запуск: systemctl --user enable --now hermes-minitoo minitoo-talk-key minitoo-autoaddr"
fi

if [ "$SYSTEM" = 1 ]; then
  say "Системные настройки (sudo): autosuspend Bluetooth, udev"
  sudo install -m 644 "$REPO"/scripts/system/modprobe.d/*.conf /etc/modprobe.d/
  sudo install -m 644 "$REPO"/scripts/system/udev/*.rules /etc/udev/rules.d/
  sudo udevadm control --reload
  echo "Правила установлены. Адаптер Intel 8087:0a2a в правиле жёстко прописан — проверьте lsusb и docs/PORTABILITY.md."
fi

say "Проверка"
"$REPO/scripts/doctor.sh" || true
say "Готово. Дальше: docs/PORTABILITY.md (сопряжение колонки, серверная часть Hermes: hermes-side/)"
