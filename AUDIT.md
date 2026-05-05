# HeatMap Audit

Дата аудита: 2026-05-05
Аудировал: текущий `main` на `5b79179` (`Fix peek triggering on desktop hover (PID-based window detection)`)

## Что проверено

- Прочитаны `README.md`, `overlay.py`, `setup.py`, `requirements.txt`, `run_as_admin.bat`, `.gitignore`.
- Проверена карта проекта: основная логика находится в одном файле `overlay.py` (~1528 строк), вспомогательная загрузка DLL в `setup.py`.
- Проверены синтаксис и базовая компиляция:
  - `python -m py_compile overlay.py setup.py` прошел без ошибок.
- Проверены хэши уже закрепленных прямых DLL из `setup.py`:
  - `LibreHardwareMonitorLib.dll` совпадает с SHA256 из `setup.py`.
  - `HidSharp.dll` совпадает с SHA256 из `setup.py`.
- Проверены места с Windows API, `schtasks`, `pythonnet`/LibreHardwareMonitor, потоками, `after()` callback-ами, конфигом, алертами, sensor loop, peek-mode, autostart.
- Проверено наличие проектных инструкций/памяти: проектного `AGENTS.md`, `MEMORY.md`, `CLAUDE.md` и существующего audit-файла в репозитории не найдено. `.claude/settings.local.json` существует как legacy локальная настройка и не является проектной памятью Codex.

## Краткое состояние проекта

HeatMap сейчас выглядит как маленькое, но уже довольно сложное Windows-only desktop-приложение: один большой tkinter/WinAPI/LHM файл отвечает одновременно за UI, чтение датчиков, desktop embedding, peek trigger, autostart, конфиг, алерты и управление процессами.

Сильные стороны:

- Есть защита от части прошлых классов багов: `py_compile` чистый, хэши прямых DLL закреплены, конфиг валидируется, sensor loop вынесен в поток, `after()` callback-и отменяются при выходе.
- Есть fallback на `psutil`, если LibreHardwareMonitor не поднялся.
- Для storage есть сниженная частота `Update()` (~30 секунд), что уменьшает лишнюю нагрузку на диски.
- Peek-режим уже учитывает desktop HWND и PID текущего процесса.

Главные риски:

- Нет автоматизированных тестов, а большая часть логики прибита к Windows API на верхнем уровне импорта.
- Ошибки runtime в `pythonw.exe` почти не видны пользователю.
- Цветовые пороги и критические пороги алертов живут отдельно и уже расходятся.
- Бинарные зависимости частично закреплены хэшами, но полный manifest/provenance для всего `lib/` отсутствует.
- `overlay.py` стал монолитом, где новые изменения легко зацепят UI, WinAPI, сенсоры и конфиг одновременно.

## Проверенные находки

### P1 - Критический алерт диска может визуально выглядеть некритичным

Файл: `overlay.py`

Код:

- `OverlayApp._CRITICAL["disk_temp"] = 55` на строках `554-559`.
- `_check_alerts()` включает алерт при `dtemp >= 55` на строках `1177-1180`.
- Дисковая температура в UI окрашивается через общий `temp_color(dtemp)` на строке `1416`.
- `temp_color()` красит красным только `temp >= 75` на строках `461-468`.

Почему это не ложноположительное:

- При `disk temp = 55` приложение уже пищит как по критическому событию, но строка диска будет желтой, потому что общий `temp_color()` возвращает желтый для диапазона `55..74`.
- README описывает цвета как `Зеленый - норма`, `Желтый - повышенные значения`, `Красный - критические значения`, а код для диска нарушает эту модель.

Что сделать:

- Развести цветовые пороги по типам метрик: CPU temp, GPU temp, disk temp, load, disk usage.
- Либо привязать красный цвет к тем же `_CRITICAL` значениям, которые используют алерты.

### P2 - Ошибки и отказы почти невидимы в обычном запуске через `pythonw.exe`

Файлы: `overlay.py`, `run_as_admin.bat`

Код:

- `run_as_admin.bat` запускает `pythonw.exe`, то есть без консоли.
- `logging.basicConfig(...)` пишет в стандартный logging output, но файл лога не настраивается (`overlay.py`, строки `23-28`).
- Важные сбои пишутся только в лог: autostart (`163-195`, `213-214`), LHM init (`257-258`), config save (`523-524`), sensor loop (`1244-1258`), no-admin warning (`1517-1518`).
- `toggle_autostart()` не показывает пользователю причину неудачи, а просто перечитывает статус задачи (`1132-1139`).

Почему это не ложноположительное:

- Основной documented path запускает GUI без консоли.
- При отказе `schtasks`, блокировке LHM-драйвера, проблеме записи конфига или серии ошибок sensor loop пользователь получает максимум молчаливое отсутствие данных/переключения. Диагностика остается только в невидимом stderr/logging output.

Что сделать:

- Добавить файл логов рядом с конфигом или в `%LOCALAPPDATA%\HeatMap\HeatMap.log`.
- Для user-action failures показывать `MessageBoxW` или маленький статус/tooltip: autostart failed, sensors unavailable, config save failed.
- В UI явно показывать degraded mode: `LHM unavailable, psutil fallback`, `sensor read error`, `not admin`.

### P2 - Нет тестового слоя для самой рискованной логики

Файл: `overlay.py`

Код/структура:

- `OverlayApp` занимает примерно `949` строк.
- `read_sensors()` занимает примерно `186` строк.
- `update_ui()` занимает примерно `167` строк.
- Windows API, `winreg`, `winsound`, `ctypes.windll.user32` и tkinter инициализируются на верхнем уровне файла.

Почему это не ложноположительное:

- В репозитории нет тестов, тестового runner-а или CI-конфига.
- `python -m py_compile` проверяет только синтаксис, но не проверяет sensor parsing, пороги, config validation, autostart command building, duplicate disk handling, no-LHM fallback, peek state transitions.
- Из-за top-level WinAPI импорт файла сложно безопасно тестировать вне Windows и сложно мокать отдельные куски.

Что сделать:

- Вынести pure-logic слой: thresholds/color policy, config load/normalize, sensor data normalization, command construction for autostart.
- Добавить `pytest` тесты на эти pure functions.
- Для LHM создать fake hardware/sensor objects и покрыть минимум CPU/GPU/RAM/storage parsing.
- Для UI оставить smoke/manual checklist, потому tkinter/desktop embedding лучше проверять отдельно.

### P2 - Полный manifest/provenance для bundled `lib/*.dll` отсутствует

Файлы: `setup.py`, `lib/`

Код/факты:

- `setup.py` скачивает и проверяет хэш только для `LibreHardwareMonitorLib.dll` и `HidSharp.dll`.
- В `lib/` сейчас лежит больше 20 DLL, включая `System.Text.Json.dll`, `System.Memory.dll`, `RAMSPDToolkit-NDD.dll`, `DiskInfoToolkit.dll`, `BlackSharp.Core.dll` и другие.
- README говорит, что `setup.py` скачивает `LibreHardwareMonitorLib.dll` и `HidSharp.dll`, но не объясняет происхождение остальных DLL.

Почему это не ложноположительное:

- Два прямых DLL проверены хэшем и совпали.
- Остальные DLL реально присутствуют в репозитории, но их версии, источники и хэши нигде не закреплены в машинно-проверяемом виде.
- Если `lib/` удалить и выполнить `python setup.py`, скрипт восстановит только две DLL, а не весь текущий набор `lib/`.

Что сделать:

- Добавить `lib/MANIFEST.md` или `lib_manifest.json` с именем, версией/пакетом, источником и SHA256 для каждой DLL.
- Либо расширить `setup.py`, чтобы он скачивал/проверял все transitive dependencies.
- Добавить verification-команду, которая сверяет все DLL с manifest.

### P2 - Runtime failures autostart остаются без понятного UX

Файл: `overlay.py`

Код:

- `enable_autostart()` возвращает `False`, если `schtasks /Create` падает (`190-192`).
- `disable_autostart()` возвращает `False`, если удаление задачи падает (`202-214`).
- `toggle_autostart()` игнорирует return value и только обновляет label по `is_autostart_enabled()` (`1132-1139`).

Почему это не ложноположительное:

- Если `schtasks` вернет ошибку прав, политики, локализации, недоступности Task Scheduler или некорректного `/TR`, пользователь не узнает причину.
- Из-за `pythonw.exe` сообщение в `log.warning` обычно не видно.

Что сделать:

- Возвращать из enable/disable `(ok, message)`.
- При ошибке показывать messagebox с stderr/stdout.
- В меню временно показывать `Autostart: ERROR` или status notification.

### P3 - Позиция окна валидируется только по левому верхнему углу

Файл: `overlay.py`

Код:

- В `__init__` проверяется только `cx/cy` против virtual screen bounds (`535-542`).

Почему это не ложноположительное:

- Если сохраненное `x` находится на последнем пикселе экрана или `y` почти внизу, top-left формально валиден, но виджет может оказаться почти полностью за пределами видимой области.
- Особенно вероятно после смены масштаба, разрешения, отключения монитора или изменения набора дисков, когда высота виджета меняется.

Что сделать:

- После построения UI вызвать `update_idletasks()`, измерить `winfo_width()/height()` и clamp-ить позицию так, чтобы хотя бы основная часть виджета оставалась видимой.
- Повторять clamp при изменении virtual screen geometry.

### P3 - Config save не атомарный

Файл: `overlay.py`

Код:

- `save_config()` пишет напрямую в `overlay_config.json` (`519-524`).

Почему это не ложноположительное:

- При падении процесса, выключении питания или ошибке записи во время `json.dump()` файл может остаться частично записанным.
- `load_config()` при любой ошибке возвращает defaults (`515-516`), то есть пользователь молча теряет позицию, настройки peek/alerts и fan calibration.

Что сделать:

- Писать во временный файл рядом с конфигом.
- Делать `flush/fsync`.
- Завершать через `os.replace(tmp, CONFIG_PATH)`.
- При поврежденном конфиге сохранять `.bak` для диагностики.

### P3 - Есть мертвые константы

Файл: `overlay.py`

Код:

- `HWND_TOPMOST = -1` (`94`) и `HWND_NOTOPMOST = -2` (`95`) больше нигде не используются.

Почему это не ложноположительное:

- AST-проверка показала ссылки только на строки объявления.
- Реальный topmost режим переключается через `root.wm_attributes("-topmost", ...)`, а fallback uses `HWND_BOTTOM`.

Что сделать:

- Удалить константы или вернуть их только если будет реальный `SetWindowPos(HWND_TOPMOST/HWND_NOTOPMOST)` путь.

### P3 - Дублирование UI-строк, цветов и sensor keys усложняет безопасные изменения

Файл: `overlay.py`

Факты:

- Повторяющиеся строки: `#1a1a2e` встречается 26 раз, `#888888` 23 раза, `Segoe UI` 19 раз.
- Sensor keys (`cpu_temp`, `gpu_temp`, `ram_pct`, `gpu_fan`, `cpu_fan` и т.д.) повторяются вручную в `read_sensors()`, `update_ui()`, `_check_alerts()`, config/fan calibration.

Почему это не ложноположительное:

- Цветовой баг с disk temp уже является примером того, как разделенные вручную пороги/ключи расходятся.
- Любое переименование key или изменение semantic threshold нужно делать в нескольких местах без тестовой страховки.

Что сделать:

- Вынести constants/theme: colors, fonts, row labels.
- Вынести sensor key constants или dataclass/typed dict.
- Свести color policy и alert policy к одной таблице thresholds.

### P3 - Sensor parsing сильно зависит от конкретных английских имен LibreHardwareMonitor

Файл: `overlay.py`

Код:

- CPU total load ищется по `"total"` (`331-335`).
- GPU load ищется по точному `name == "gpu core"` (`356-361`).
- VRAM ищется по точным `"gpu memory used"` и `"gpu memory total"` (`375-386`).
- RAM ищется по точному `"memory"` (`440-446`).
- CPU fan/control matching зависит от `"cpu"`, `"optional"`, `"#1"` (`411-438`).

Почему это не ложноположительное:

- Это действительно текущая логика выбора сенсоров.
- Она может быть нормальной для текущей версии LHM и железа автора, но на другом железе/версии/локализации часть метрик будет пустой без явной ошибки.

Что сделать:

- Добавить debug dump известных hardware/sensor names в лог при первом запуске или по hotkey/menu item.
- Добавить fallback matching: несколько известных вариантов для GPU load/VRAM/RAM.
- Добавить тестовые fake sensors на распространенные варианты имен.

### P3 - README заявляет MIT, но LICENSE-файла нет

Файл: `README.md`

Факт:

- README содержит секцию `Лицензия: MIT`.
- В корне репозитория нет `LICENSE`/`LICENSE.md`.

Почему это не ложноположительное:

- `git ls-files` и просмотр корня не показывают license file.

Что сделать:

- Добавить стандартный `LICENSE` с MIT-текстом и актуальным copyright holder.

### P3 - Зависимости Python заданы только нижними границами

Файл: `requirements.txt`

Код:

- `pythonnet>=3.0.0`
- `psutil>=5.9.0`

Почему это не ложноположительное:

- Верхних ограничений или lock-файла нет.
- Для Windows + pythonnet + .NET interop это риск воспроизводимости: будущие major/minor изменения могут сломать загрузку CLR или поведение `psutil`.

Что сделать:

- Для приложения зафиксировать tested versions в `requirements.txt` или добавить lock-файл.
- Минимум: документировать Python version + tested package versions.

## Проверенные подозрения, которые сейчас не считаю багами

- `setup.py` zip extraction: zip-slip не подтверждается, потому скрипт читает выбранный entry из архива и пишет только `os.path.basename(...)` в `LIB_DIR`.
- Хэши прямых `LibreHardwareMonitorLib.dll` и `HidSharp.dll`: совпадают с текущими файлами.
- `py_compile`: синтаксических ошибок в `overlay.py` и `setup.py` нет.
- `overlay_config.json`: файл локальный и игнорируется `.gitignore`, в репозиторий не должен попасть.
- Broad `except Exception`: мест много, но часть оправдана на границах Windows API/GUI teardown/hardware access. Проблема не в самом факте broad except, а в том, что failures часто не видны пользователю и не пишутся в файл.

## Рекомендованный порядок следующих работ

1. Починить P1: синхронизировать color thresholds с alert thresholds, особенно disk temp.
2. Добавить file logging и user-visible error path для autostart/LHM/config/sensor failures.
3. Вынести pure logic из `overlay.py` и добавить первые `pytest` тесты:
   - thresholds/colors;
   - config normalization;
   - sensor parsing на fake sensors;
   - autostart command construction.
4. Сделать атомарную запись `overlay_config.json`.
5. Добавить DLL manifest и verification command для всего `lib/`.
6. Улучшить position clamp с учетом размера виджета и multi-monitor changes.
7. Удалить мертвые `HWND_TOPMOST/HWND_NOTOPMOST`.
8. Добавить `LICENSE`.
9. Зафиксировать/задокументировать tested Python dependency versions.

## Сводка для следующей сессии

Осталось сделать:

- Исправить рассинхрон критических алертов и цветов, начать с disk temp `55C`.
- Добавить нормальный лог-файл и понятные пользователю сообщения об ошибках autostart, LHM, config save и sensor loop.
- Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
- Добавить базовый `pytest` набор без реального железа через fake LHM objects.
- Перевести сохранение конфига на atomic write с backup поврежденного JSON.
- Описать и проверять все DLL из `lib/` через manifest с SHA256.
- Добавить clamp позиции виджета по фактическому размеру окна.
- Удалить мертвые topmost constants.
- Добавить MIT `LICENSE`.
- Зафиксировать tested versions зависимостей Python или завести lock/documented environment.
