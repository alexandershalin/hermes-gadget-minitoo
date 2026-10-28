# Перенос на другую установку Hermes

## Выбранная схема

Проект ставится **на хост с Hermes через `install.sh` и пользовательские сервисы systemd**. Docker используется только для тестов кода (`Dockerfile.test`), не для работы с колонкой.

Почему не Docker для рантайма:

- колонка работает по Bluetooth Classic: RFCOMM для экрана, A2DP и HFP для звука. Всё это живёт в BlueZ хоста и в пользовательском PipeWire/WirePlumber;
- переключение профиля A2DP и HFP делается через `wpctl` и D-Bus пользовательской сессии;
- в контейнер это пришлось бы пробрасывать целиком (`--net=host`, сокеты D-Bus и PipeWire, `/dev/rfcomm*`, udev). Такая сборка хрупкая и без выигрыша: зависимости от хоста остаются.

Как у соседей: официальный Linux-клиент SDK (`hermes-gadget-sdk/linux`) тоже ставится через `install.sh` и systemd-сервис, не через Docker.

## Как это связано с Hermes

Клиент ставится **на том же компьютере, где работает Hermes**, по образцу `hermes-gadget linux` (те же модули `client`, `control`, `audio`, `display`, `cli`; от SDK берутся только ядро устройства, WebSocket-транспорт и сопряжение): это отдельный процесс, который подключается к плагину gadget по WebSocket (`"server": "ws://127.0.0.1:8765/gadget"` в `config.json`, при необходимости `token` и `wss://`). Колонка при этом должна быть в радиусе Bluetooth этого компьютера. Запуск на другой машине тоже возможен (поменять `server`), но не проверялся.

Команды: `hermes-gadget-minitoo run|status|messages|send|event|button|audio-devices|audio-check|capabilities`. Это отдельный исполняемый файл, а не подкоманда `hermes-gadget` (у SDK нет хука для чужих подкоманд, а подмена его скрипта ломалась бы при переустановке SDK).

## Что входит

| Часть | Где | Куда ставится |
|---|---|---|
| Бэкенд экрана и звука | `src/hermes_minitoo`, `.venv` | рядом с репозиторием |
| Кнопки, запись, VAD | `scripts/minitoo-talk-key.py` | user-юнит `minitoo-talk-key` |
| Автопоиск адреса колонки | `scripts/minitoo-autoaddr.py` | user-юнит `minitoo-autoaddr` |
| Настройки поведения | `scripts/systemd/minitoo-talk-key.options.conf` | drop-in `10-options.conf` |
| Системные настройки Bluetooth | `scripts/system/` | `/etc/modprobe.d`, `/etc/udev/rules.d` (нужен sudo) |
| Голос для Hermes (STT, TTS) | `hermes-side/` | `~/.local/bin`, юнит `piper-tts` |
| Самодиагностика | `scripts/doctor.sh` | запускается вручную |

## Установка

```bash
git clone https://github.com/alexandershalin/hermes-gadget-minitoo.git
cd hermes-gadget-minitoo
git checkout claude/inspiring-shannon-xl7a6h   # пока ветка не влита в main
sudo apt install python3-venv git liblzo2-2 libportaudio2 bluez pipewire wireplumber pipewire-bin ffmpeg
./install.sh --address AA:BB:CC:DD:EE:FF       # MAC колонки; без него позже сработает autoaddr
./hermes-side/install.sh                       # STT-клиент и Piper TTS (если нужен голос)
systemctl --user enable --now hermes-minitoo minitoo-talk-key minitoo-autoaddr
./scripts/doctor.sh                            # сводка: что в порядке, чего не хватает
```

Опции `install.sh`: `--server ws://хост:порт/gadget`, `--name`, `--no-services`, `--with-system` (ставит правила autosuspend, нужен sudo).

Сопряжение колонки выполняется один раз вручную: `bluetoothctl`, затем `scan on`, `pair MAC`, `trust MAC`, `connect MAC`.

## Настройка Hermes

В `config.yaml` Hermes (применять через `hermes config set`):

```yaml
platforms:
  gadget:
    enabled: true
stt:
  provider: openrouter_stt
  language: auto
  providers:
    openrouter_stt:
      type: command
      command: python3 ~/.local/bin/openrouter-stt-client.py {input_path} {output_path} {language} {model}
      timeout: 60
tts:
  provider: piper_daemon
  providers:
    piper_daemon:
      type: command
      voice: dmitri
      format: wav
      command: python3 ~/.local/bin/piper-tts-client.py {input_path} {output_path} {voice}
```

Одобрить устройство: `hermes gadget approve КОД`.

## Двуязычный режим (русский и английский)

- **STT.** При `stt.language: auto` клиент не передаёт язык, и Whisper определяет его сам.
- **TTS.** `piper-tts-client.py` выбирает голос по тексту ответа: если кириллицы меньше 30%, читает английским голосом `ryan`, иначе выбранным русским.
- **Язык ответа модели.** Если промпт Hermes в основном русский, бесплатные модели отвечают по-русски на любую речь, даже при правиле в `SOUL.md` и в памяти. Поэтому STT-клиент добавляет к нерусской реплике строку `[Language: the user spoke English. Reply in English only.]`. Это единственный способ, который сработал надёжно: правка промпта не перевешивает русский контекст.

## Что привязано к конкретной машине

- **Адаптер Bluetooth.** Правило udev в `scripts/system/udev/91-bt-intel-0a2a-no-autosuspend.rules` написано под Intel `8087:0a2a`. Для другого адаптера нужны свои `idVendor` и `idProduct` из `lsusb`.
- **Прокси STT.** Хост OpenRouter из РФ может быть недоступен напрямую. Прокси задаётся в `~/.config/hermes-minitoo/stt.env` (`OR_STT_PROXY=...`); по умолчанию соединение прямое.
- **libportaudio2.** На целевой машине без неё юнит `hermes-minitoo` не запустится. Системная установка (`apt install libportaudio2`) решает вопрос. Строка `LD_LIBRARY_PATH=%h/.local/lib/portaudio-extract/...` в юните нужна только на хосте без прав на apt.
- **Качество связи.** Сообщения `corrupted SCO packet` и зависания `hci0` зависят от адаптера и обсуждаются в `docs/KNOWN_ISSUES.md`.

## Тесты без железа

```bash
docker build -f Dockerfile.test -t minitoo-test . && docker run --rm minitoo-test
```

Или локально: `.venv/bin/python -m pytest -q tests`.

## Что не проверено на чужой установке

Скрипты `install.sh` и `hermes-side/install.sh` проверены только на синтаксис и на этой машине. Чистая установка на другом хосте пока не прогонялась. Первый, кто это сделает, пусть допишет сюда найденные расхождения.

## Структура кода и другие платформы

Общий слой не зависит от ОС: `codec.py`, `protocol.py`, `display.py`, `client.py`, `control.py`, `config.py`. Всё платформенное лежит в `src/hermes_minitoo/platforms/<ОС>/`. Сейчас есть только `linux/` (RFCOMM через BlueZ, таблица HCI-соединений, блокировка каталога состояния). Для macOS или Android нужен свой каталог с тем же набором: транспорт RFCOMM, источник событий кнопок, звук. Скрипты `scripts/` (кнопки, VAD, автопоиск адреса) пока тоже только для Linux.
