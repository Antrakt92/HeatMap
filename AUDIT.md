# HeatMap Audit

Дата аудита: 2026-05-05
Первый проход: текущий `main` на `5b79179` (`Fix peek triggering on desktop hover (PID-based window detection)`)
Глубокий проход: текущий `main` на `9cf9dc1` (`Add project audit`)

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
- Дополнительно проверена clean-install ветка `setup.py` во временной папке: скрипт падает на hash mismatch до установки DLL.
- Сверен NuGet package layout для `LibreHardwareMonitorLib/0.9.5`: пакет содержит `runtimes/.../net10.0`, `net472`, `net8.0`, `net9.0`, `netstandard2.0`, но не содержит `lib/net4/...`.
- Сверены entry hashes для `HidSharp/2.1.0` и `HidSharp/2.6.4`: ожидаемый в `setup.py` хэш относится к `HidSharp/2.6.4`, а URL указывает `2.1.0`.
- Проверен импорт модуля без запуска приложения: `import overlay` проходит успешно.
- Проверены Authenticode signatures для `lib/*.dll`: Microsoft/System DLL и `HidSharp.dll` подписаны, `LibreHardwareMonitorLib.dll`, `DiskInfoToolkit.dll`, `BlackSharp.Core.dll`, `RAMSPDToolkit-NDD.dll` не подписаны.

## Краткое состояние проекта

HeatMap сейчас выглядит как маленькое, но уже довольно сложное Windows-only desktop-приложение: один большой tkinter/WinAPI/LHM файл отвечает одновременно за UI, чтение датчиков, desktop embedding, peek trigger, autostart, конфиг, алерты и управление процессами.

Сильные стороны:

- Есть защита от части прошлых классов багов: `py_compile` чистый, хэши прямых DLL закреплены, конфиг валидируется, sensor loop вынесен в поток, `after()` callback-и отменяются при выходе.
- Есть fallback на `psutil`, если LibreHardwareMonitor не поднялся.
- Для storage есть сниженная частота `Update()` (~30 секунд), что уменьшает лишнюю нагрузку на диски.
- Peek-режим уже учитывает desktop HWND и PID текущего процесса.

Главные риски:

- Автоматизированные тесты появились только для первого набора pure helpers; большая часть логики все еще прибита к Windows API на верхнем уровне импорта.
- Ошибки runtime в `pythonw.exe` почти не видны пользователю.
- Цветовые пороги и критические пороги алертов все еще живут отдельно, хотя disk temp mismatch уже закрыт.
- Бинарные зависимости частично закреплены хэшами, но полный manifest/provenance для всего `lib/` отсутствует.
- `overlay.py` стал монолитом, где новые изменения легко зацепят UI, WinAPI, сенсоры и конфиг одновременно.

## Проверенные находки

### P1 - `setup.py` не работает на чистой установке

Статус: закрыто в стабилизационном пакете 2026-05-05.

Файл: `setup.py`

Код:

- `LibreHardwareMonitorLib` скачивается из `https://www.nuget.org/api/v2/package/LibreHardwareMonitorLib/0.9.5` (`setup.py`, строка `17`).
- Скрипт сначала ищет `lib/net4/LibreHardwareMonitorLib.dll` (`setup.py`, строка `19`).
- В реальном NuGet-пакете `0.9.5` такого пути нет; есть `runtimes/win-x64/lib/net10.0/...`, `runtimes/win-x64/lib/net472/...`, `net8.0`, `net9.0`, `netstandard2.0` и аналоги для `win-x86`/`win-arm64`.
- Fallback по basename берет первый подходящий DLL из архива. В проверке временного запуска это оказался `runtimes/win-x64/lib/net10.0/LibreHardwareMonitorLib.dll`.
- Ожидаемый SHA256 в `setup.py` (`21673a...`) соответствует `runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll`, а не первому найденному `net10.0` DLL (`64f9fe...`).
- `HidSharp` дополнительно сломан: URL указывает `HidSharp/2.1.0` (`setup.py`, строка `26`), а ожидаемый SHA256 `d86690...` соответствует `HidSharp/2.6.4 lib/net35/HidSharp.dll`, не `2.1.0`.

Воспроизведение:

- `setup.py` был скопирован во временную папку и запущен оттуда, чтобы не трогать текущий `lib/`.
- Результат: `ERROR: LibreHardwareMonitorLib.dll hash mismatch! Expected: 21673a... Got: 64f9fe...`

Почему это не ложноположительное:

- Ошибка воспроизводится реальным запуском `setup.py` вне репозитория.
- Хэши entry-by-entry подтверждают, что ожидаемый hash относится к другому target framework entry.
- Если пользователь следует README и запускает `python setup.py` после clone без `lib/`, установка не завершится.

Что сделать:

- Сделано: указан точный `runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll`.
- Сделано: `HidSharp` переведен на `2.6.4` и `lib/net35/HidSharp.dll`.
- Сделано: basename fallback удален; missing exact path теперь печатает matching DLL candidates.
- Сделано: добавлен metadata-тест и выполнен smoke test `setup.py` во временной папке.

### P1 - Критический алерт диска может визуально выглядеть некритичным

Статус: закрыто в стабилизационном пакете 2026-05-05.

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

- Сделано: добавлен отдельный `disk_temp_color()`, где `55C+` красный.
- Сделано: строка диска в UI использует `disk_temp_color()`, а не общий `temp_color()`.

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
- NuGet metadata для `LibreHardwareMonitorLib/0.9.5` объявляет зависимости вроде `DiskInfoToolkit 1.1.1`, `HidSharp 2.6.4`, `RAMSPDToolkit-NDD 1.4.2`, `System.Management 10.0.1`, `System.Memory 4.6.3`, `System.Threading.AccessControl 10.0.1` и других в зависимости от target framework; текущий `setup.py` не восстанавливает эту dependency graph.
- `LibreHardwareMonitorLib.dll`, `DiskInfoToolkit.dll`, `BlackSharp.Core.dll` и `RAMSPDToolkit-NDD.dll` не имеют Authenticode signature, поэтому для них особенно важны pinned hashes/provenance.

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

### P2 - Shutdown может закрыть LibreHardwareMonitor, пока sensor thread еще жив

Статус: закрыто в стабилизационном пакете 2026-05-05.

Файл: `overlay.py`

Код:

- `quit()` ставит `self._stop_event`, затем делает `self.sensor_thread.join(timeout=5)` (`1460-1464`).
- После timeout код не проверяет `self.sensor_thread.is_alive()`.
- Далее он закрывает `self.computer.Close()` (`1467-1472`) с комментарием, что sensor thread уже остановлен.
- Но если `read_sensors()` завис или выполняется дольше 5 секунд, sensor thread после timeout все еще может читать тот же `Computer` object.

Почему это не ложноположительное:

- `join(timeout=5)` по контракту не гарантирует остановку потока; он только перестает ждать через 5 секунд.
- Код действительно закрывает `self.computer` после timeout без проверки `is_alive()`.
- Hardware/sensor I/O и storage update уже признаны потенциально медленными в проекте: storage обновляется раз в 30 секунд именно для снижения I/O.

Что сделать:

- Сделано: после `join(timeout=5)` проверяется `self.sensor_thread.is_alive()`.
- Сделано: если поток еще жив, `self.computer.Close()` не вызывается; пишется warning.

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

Статус: закрыто в стабилизационном пакете 2026-05-05.

Файл: `overlay.py`

Код:

- `save_config()` пишет напрямую в `overlay_config.json` (`519-524`).

Почему это не ложноположительное:

- При падении процесса, выключении питания или ошибке записи во время `json.dump()` файл может остаться частично записанным.
- `load_config()` при любой ошибке возвращает defaults (`515-516`), то есть пользователь молча теряет позицию, настройки peek/alerts и fan calibration.

Что сделать:

- Сделано: `save_config()` пишет во временный файл рядом с конфигом.
- Сделано: перед заменой выполняются `flush()` и `os.fsync()`.
- Сделано: финальная замена выполняется через `os.replace()`.

### P3 - Bool в числовых полях конфига проходит как валидное число

Статус: закрыто в стабилизационном пакете 2026-05-05.

Файл: `overlay.py`

Код:

- `load_config()` проверяет `x/y` через `isinstance(cfg.get(key), (int, float))` (`501-505`).
- `gpu_fan_max_rpm/cpu_fan_max_rpm` проверяются аналогично (`509-513`).
- В Python `bool` является subclass `int`, поэтому JSON `true/false` проходит числовую проверку.

Воспроизведение:

- Временный конфиг `{"x": true, "y": false, "gpu_fan_max_rpm": true, "cpu_fan_max_rpm": false}` был прочитан как:
  - `x = 1`
  - `y = 0`
  - `gpu_fan_max_rpm = 1`
  - `cpu_fan_max_rpm = 1800` (`false` отфильтровался из-за `<= 0`)

Почему это не ложноположительное:

- Поведение воспроизведено через `overlay.load_config()` с временным `CONFIG_PATH`.
- `gpu_fan_max_rpm = 1` приведет к заведомо неверной оценке fan percentage до следующей автокалибровки.

Что сделать:

- Сделано: числовые поля явно отклоняют `bool` до проверки `int/float`.
- Сделано: добавлен `unittest` на `true/false` в numeric config fields.

### P3 - `read_sensors()` падает на бесконечных sensor values

Статус: закрыто в стабилизационном пакете 2026-05-05.

Файл: `overlay.py`

Код:

- `_safe_round()` фильтрует `None` и `NaN`, но не фильтрует `inf/-inf` (`262-269`).
- `round(float("inf"))` и `round(float("-inf"))` выбрасывают `OverflowError`.
- `_safe_round()` вызывается почти для всех LHM sensor values (`325`, `333`, `338`, `353`, `359`, `363`, `367`, `372`, `378`, `382`, `394`, `397`, `417`, `422`, `444`).

Воспроизведение:

- `overlay._safe_round(float("nan"))` возвращает `None`.
- `overlay._safe_round(float("inf"))` выбрасывает `OverflowError: cannot convert float infinity to integer`.
- `overlay._safe_round(float("-inf"))` выбрасывает такую же ошибку.

Почему это не ложноположительное:

- Ошибка воспроизводится напрямую в helper-е.
- Если LHM или .NET interop вернет infinity для одного sensor value, весь `read_sensors()` упадет, sensor loop заменит данные на `{"error": ...}`, а UI просто перестанет обновлять значения без видимого сообщения.

Что сделать:

- Сделано: `_safe_round()` использует `math.isfinite(v)`.
- Сделано: добавлены тесты для `None`, `NaN`, `inf`, `-inf` и обычных чисел.

### P3 - Есть мертвые константы

Статус: закрыто в стабилизационном пакете 2026-05-05.

Файл: `overlay.py`

Код:

- `HWND_TOPMOST = -1` (`94`) и `HWND_NOTOPMOST = -2` (`95`) больше нигде не используются.

Почему это не ложноположительное:

- AST-проверка показала ссылки только на строки объявления.
- Реальный topmost режим переключается через `root.wm_attributes("-topmost", ...)`, а fallback uses `HWND_BOTTOM`.

Что сделать:

- Сделано: `HWND_TOPMOST` и `HWND_NOTOPMOST` удалены.

### P3 - Есть мертвое поле `_hwnd`

Статус: закрыто в стабилизационном пакете 2026-05-05.

Файл: `overlay.py`

Код:

- `_embed_into_desktop()` сохраняет `self._hwnd = hwnd` (`741-746`).
- AST-проверка показала, что `_hwnd` только записывается и нигде не читается.

Почему это не ложноположительное:

- По коду все последующие операции заново вызывают `_get_hwnd()` вместо использования `self._hwnd`.

Что сделать:

- Сделано: запись `self._hwnd` удалена.

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
- `import overlay`: проходит успешно без создания tkinter root и без запуска sensor loop.
- `overlay_config.json`: файл локальный и игнорируется `.gitignore`, в репозиторий не должен попасть.
- Broad `except Exception`: мест много, но часть оправдана на границах Windows API/GUI teardown/hardware access. Проблема не в самом факте broad except, а в том, что failures часто не видны пользователю и не пишутся в файл.

## Рекомендованный порядок следующих работ

1. Добавить file logging и user-visible error path для autostart/LHM/config/sensor failures.
2. Расширить тестовый слой:
   - sensor parsing на fake LHM objects;
   - autostart command/result handling;
   - больше config edge cases.
3. Добавить DLL manifest и verification command для всего `lib/`.
4. Улучшить position clamp с учетом размера виджета и multi-monitor changes.
5. Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
6. Добавить debug dump известных hardware/sensor names и fallback matching для распространенных sensor names.
7. Добавить `LICENSE`.
8. Зафиксировать/задокументировать tested Python dependency versions.

## Сводка для следующей сессии

Осталось сделать:

- Добавить нормальный лог-файл и понятные пользователю сообщения об ошибках autostart, LHM, config save и sensor loop.
- Расширить `unittest`-набор: fake LHM sensors, autostart command/result handling, дополнительные config edge cases.
- Описать и проверять все DLL из `lib/` через manifest с SHA256.
- Добавить clamp позиции виджета по фактическому размеру окна.
- Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
- Добавить debug dump sensor names и fallback matching для распространенных имен LHM-сенсоров.
- Добавить MIT `LICENSE`.
- Зафиксировать tested versions зависимостей Python или завести lock/documented environment.
