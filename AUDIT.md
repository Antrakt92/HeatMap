# HeatMap Audit

Дата аудита: 2026-05-05
Первый проход: текущий `main` на `5b79179` (`Fix peek triggering on desktop hover (PID-based window detection)`)
Глубокий проход: текущий `main` на `9cf9dc1` (`Add project audit`)
Дополнительный глубокий проход: текущий `main` на `cc5fffe` (`Stabilize setup and runtime helpers`)
Второй стабилизационный пакет: рабочая сессия 2026-05-05 (`startup ordering`, `sensor error UI`, `single-instance matching`)
Дополнительный аудит после второго пакета: текущий `main` на `11e2c45` (`Stabilize startup and sensor error handling`)

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
- После стабилизационного пакета повторно проверены:
  - `python -m py_compile overlay.py setup.py` прошел без ошибок.
  - `python -m unittest discover -s tests` прошел: 6 тестов OK.
  - `python -c "import overlay; print('ok')"` прошел.
  - `setup.py` во временной папке успешно скачал и извлек `LibreHardwareMonitorLib.dll` и `HidSharp.dll`.
- Дополнительно проверены startup/shutdown ordering, single-instance логика, sensor-loop error path, setup architecture assumptions, README launch path и `run_as_admin.bat`.
- После второго стабилизационного пакета повторно проверены:
  - `python -m py_compile overlay.py setup.py` прошел без ошибок.
  - `python -m unittest discover -s tests` прошел: 10 тестов OK.
  - `python -c "import overlay; print('ok')"` прошел.
  - `git diff --check` прошел без whitespace errors.
- После второго пакета дополнительно проверены sensor failure boundaries, autostart task state/migration path, README Python version claim, tracked files и unused imports scan.
- Повторно проверено отсутствие project-level `AGENTS.md`; рабочее дерево было чистым перед аудитом.

## Краткое состояние проекта

HeatMap сейчас выглядит как маленькое, но уже довольно сложное Windows-only desktop-приложение: один большой tkinter/WinAPI/LHM файл отвечает одновременно за UI, чтение датчиков, desktop embedding, peek trigger, autostart, конфиг, алерты и управление процессами.

Сильные стороны:

- Есть защита от части прошлых классов багов: `py_compile` чистый, хэши прямых DLL закреплены, конфиг валидируется, sensor loop вынесен в поток, `after()` callback-и отменяются при выходе.
- Есть fallback на `psutil`, если LibreHardwareMonitor не поднялся.
- Для storage есть сниженная частота `Update()` (~30 секунд), что уменьшает лишнюю нагрузку на диски.
- Peek-режим уже учитывает desktop HWND и PID текущего процесса.

Главные риски:

- Автоматизированные тесты уже покрывают часть helper/startup/error-state логики, но большая часть sensor parsing, autostart UX и WinAPI/tkinter поведения все еще без страховки.
- Ошибки runtime в `pythonw.exe` почти не видны пользователю для autostart/LHM/config failures; sensor-loop error state уже стал видимым в UI.
- `read_sensors()` все еще all-or-nothing: ошибка одного hardware/sensor object сбрасывает весь sample в error state.
- Autostart state проверяет только наличие scheduled task name, а не то, что задача реально указывает на текущий `overlay.py`.
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

### P2 - Новый запуск может убить старый рабочий оверлей до проверки prerequisites

Статус: закрыто во втором стабилизационном пакете 2026-05-05.

Файл: `overlay.py`

Код:

- `main()` сразу вызывает `kill_previous_instances()` (`overlay.py`, строка `1527`).
- Только после этого проверяется наличие `lib/LibreHardwareMonitorLib.dll` (`overlay.py`, строки `1529-1535`).
- Если DLL отсутствует, новый процесс показывает `MessageBoxW` и выходит через `sys.exit(1)`.

Почему это не ложноположительное:

- Порядок выполнения подтвержден в коде: старый instance завершается до проверки обязательной DLL.
- Старый процесс может продолжать работать с уже загруженной DLL даже если файл на диске был удален, перемещен или checkout оказался неполным.
- В таком состоянии новый запуск не заменит старый оверлей, а сначала погасит рабочий процесс и затем завершится с ошибкой.

Что сделать:

- Сделано: добавлен `REQUIRED_DLLS` и `_missing_required_dlls()`.
- Сделано: `main()` проверяет `LibreHardwareMonitorLib.dll` и `HidSharp.dll` до `kill_previous_instances()`.
- Сделано: MessageBox перечисляет отсутствующие DLL и оставляет подсказку `Run: python setup.py`.
- Сделано: добавлен regression test, который проверяет, что при missing DLL `kill_previous_instances()` не вызывается.

### P2 - Sensor-loop errors оставляют на экране устаревшие значения

Статус: закрыто во втором стабилизационном пакете 2026-05-05.

Файл: `overlay.py`

Код:

- `sensor_loop()` при исключении записывает `self.sensor_data = {"error": str(e)}` (`overlay.py`, строки `1262-1267`).
- `update_ui()` при `"error" in data` просто планирует следующий callback и возвращается (`overlay.py`, строки `1291-1293`).
- Старые labels при этом не очищаются и не переводятся в error/degraded state.

Почему это не ложноположительное:

- Это прямой текущий control flow: данные заменяются на error-dict, а UI ничего не меняет.
- Если перед ошибкой на экране были безопасные температуры/нагрузки, пользователь продолжит видеть эти старые значения без признака, что датчики перестали обновляться.
- Риск усиливается тем, что основной запуск идет через `pythonw.exe`, где `log.error(...)` обычно не виден.

Что сделать:

- Сделано: `update_ui()` при `{"error": ...}` вызывает `_show_sensor_error()`.
- Сделано: `_show_sensor_error()` очищает dynamic disk rows, сбрасывает `_last_disk_names`/`disk_labels` и ставит оставшимся metric labels `ERR` красным цветом.
- Сделано: после error state сохраняется обычный polling через `root.after(2000, self.update_ui)`.
- Сделано: добавлен `unittest` на error update path через fake root/labels без запуска настоящего tkinter.

### P2 - Тестовый слой все еще покрывает не всю рискованную логику

Файл: `overlay.py`

Код/структура:

- `OverlayApp` занимает примерно `949` строк.
- `read_sensors()` занимает примерно `186` строк.
- `update_ui()` занимает примерно `167` строк.
- Windows API, `winreg`, `winsound`, `ctypes.windll.user32` и tkinter инициализируются на верхнем уровне файла.

Почему это не ложноположительное:

- В репозитории уже есть stdlib `unittest`-набор для helper/startup/error-state логики.
- При этом sensor parsing, autostart command/result handling, no-LHM fallback, peek state transitions и большая часть tkinter/WinAPI поведения все еще не покрыты.
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

### P3 - `setup.py` жестко зашит под win-x64

Файл: `setup.py`

Код:

- `LibreHardwareMonitorLib` извлекается только из `runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll` (`setup.py`, строка `19`).
- Проверки архитектуры Python/Windows в `setup.py` нет.
- README требует Windows, но не фиксирует 64-bit Python как обязательное условие.

Почему это не ложноположительное:

- Путь `win-x64` прямо зашит в `PACKAGES`.
- На текущей машине проверено `AMD64`/64-bit, поэтому smoke test проходит именно для текущего окружения.
- На 32-bit Python или ARM64 Windows скрипт все равно установит x64 DLL, после чего CLR/pythonnet загрузка может упасть уже в runtime.

Что сделать:

- Определять architecture через `platform.machine()` и `struct.calcsize("P")`.
- Для x86/ARM64 выбирать соответствующий `runtimes/win-x86/...` или `runtimes/win-arm64/...` entry и закрепить SHA256 для каждого поддержанного варианта.
- Если поддерживается только x64, явно проверять это в `setup.py` и README, падая до скачивания с понятной ошибкой.

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

### P2 - Один сбойный hardware/sensor object обнуляет весь sensor sample

Файл: `overlay.py`

Код:

- `read_sensors()` проходит по `for hw in computer.Hardware` (`overlay.py`, строка `307`).
- `hw.Update()` и `sub.Update()` вызываются без локальной изоляции ошибок (`overlay.py`, строки `312-315`).
- Дальше чтение `hw.Sensors`, `sensor.Name`, `sensor.Value` и `sub.Sensors` тоже не изолировано по одному устройству/сенсору (`overlay.py`, строки `319-445`).
- `sensor_loop()` ловит исключение только вокруг всего `read_sensors()` и заменяет весь sample на `{"error": str(e)}` (`overlay.py`, строки `1266-1274`).

Почему это не ложноположительное:

- Control flow подтвержден в коде: любое исключение внутри одного `hw.Update()`, `sub.Update()` или sensor access выходит из `read_sensors()` целиком.
- После этого UI показывает общий `ERR`, хотя CPU/RAM fallback через `psutil` и другие уже прочитанные hardware blocks могли быть доступны.
- Это особенно вероятно на границе hardware/driver/.NET interop: проект уже обрабатывает LHM init failure и sensor-loop exceptions как реальные runtime-события.

Что сделать:

- Изолировать ошибки внутри `read_sensors()` на уровне одного hardware block: логировать failing hardware name/type, пропускать его и продолжать остальные.
- Для CPU load и RAM всегда сохранять `psutil` fallback даже если LHM block упал.
- Добавить fake hardware tests: один fake device бросает в `Update()`, второй возвращает нормальные sensors, итоговый sample не должен становиться глобальным error.

### P3 - Autostart может показывать stale ON и терять legacy fallback при failed migration

Файл: `overlay.py`

Код:

- `is_autostart_enabled()` проверяет только `schtasks /Query /TN HWMonitorOverlay` и возвращает `True` при `returncode == 0` (`overlay.py`, строки `154-161`).
- Команда/путь задачи не сверяется с текущими `get_pythonw_path()` и `SCRIPT_PATH`.
- `enable_autostart()` сначала удаляет legacy registry entry (`overlay.py`, строки `171-176`), и только потом создает scheduled task (`overlay.py`, строки `178-188`).
- Если `schtasks /Create` падает, функция возвращает `False`, но legacy entry уже удален (`overlay.py`, строки `189-195`).

Почему это не ложноположительное:

- Если проект был перемещен, склонирован в другую папку или старый task остался от другой копии, `is_autostart_enabled()` все равно покажет `ON`, потому проверяется только имя задачи.
- Если пользователь мигрирует со старого registry autostart и создание scheduled task не удалось, код уже удалил registry fallback до подтверждения успешной замены.
- Это не только UX-проблема: состояние автозапуска может реально стать неправильным или исчезнуть после неуспешного enable.

Что сделать:

- Проверять scheduled task target command через `schtasks /Query /TN ... /XML` или `/V /FO LIST` и сравнивать с текущим `SCRIPT_PATH`.
- Удалять legacy registry entry только после успешного создания scheduled task.
- Возвращать `(ok, message)` из enable/disable и показывать пользователю точную ошибку.
- Добавить tests для stale task command и failed migration order через mocked `subprocess.run`/`winreg`.

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

### P3 - Single-instance detection пропускает часть documented/manual launch cases

Статус: закрыто во втором стабилизационном пакете 2026-05-05.

Файл: `overlay.py`, `README.md`, `run_as_admin.bat`

Код:

- `kill_previous_instances()` сравнивает `SCRIPT_PATH.lower()` с каждым raw argv argument (`overlay.py`, строки `1499-1514`).
- `run_as_admin.bat` передает абсолютный путь `"%~dp0overlay.py"`, и этот путь покрывается текущей логикой.
- README также предлагает ручной запуск `python overlay.py` (`README.md`, строки `62-65`).

Почему это не ложноположительное:

- При ручном запуске из README в argv обычно лежит `overlay.py`, а не абсолютный `C:\...\overlay.py`.
- Текущее сравнение не normalizes relative paths через cwd, не делает `abspath`, не учитывает short paths/symlinks и slash variants.
- Значит, два ручных запуска из папки проекта могут не увидеть друг друга как один и тот же script path. Это открывает путь к двум overlay instances и гонкам вокруг общего `overlay_config.json`/temporary config file.

Что сделать:

- Сделано: добавлен helper `is_same_script_invocation(script_path, arg, cwd=None)`.
- Сделано: matching нормализует quotes, absolute path, `abspath`, `normcase` и relative `overlay.py` через `proc.cwd()`, если cwd доступен.
- Сделано: процессы с другим basename не считаются тем же script invocation.
- Сделано: добавлены тесты на absolute path, quoted absolute path, relative path с правильным/неправильным cwd и unrelated script.

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

### P3 - README заявляет Python 3.7+, но текущий test code требует более новый Python

Файлы: `README.md`, `tests/test_overlay_helpers.py`

Факты:

- README указывает `Python 3.7+` как требование (`README.md`, строка `35`).
- Тест `test_main_does_not_kill_previous_instance_when_required_dlls_are_missing()` использует parenthesized multi-context `with (...)` (`tests/test_overlay_helpers.py`, строки `77-81`).
- Такой синтаксис не поддерживается Python 3.7 и появился только в более новых версиях Python.

Почему это не ложноположительное:

- Приложение может оставаться совместимым с Python 3.7, но текущий официальный test suite уже не запускается на Python 3.7.
- Это ломает воспроизводимость для пользователя/CI, если он следует README буквально и пытается проверять проект на минимально заявленной версии.

Что сделать:

- Либо поднять documented minimum Python до версии, на которой реально запускаются тесты.
- Либо переписать tests на Python 3.7-compatible syntax и добавить matrix/заметку о реально проверенной версии.

## Проверенные подозрения, которые сейчас не считаю багами

- `setup.py` zip extraction: zip-slip не подтверждается, потому скрипт читает выбранный entry из архива и пишет только `os.path.basename(...)` в `LIB_DIR`.
- Хэши прямых `LibreHardwareMonitorLib.dll` и `HidSharp.dll`: совпадают с текущими файлами.
- `py_compile`: синтаксических ошибок в `overlay.py` и `setup.py` нет.
- `import overlay`: проходит успешно без создания tkinter root и без запуска sensor loop.
- `overlay_config.json`: файл локальный и игнорируется `.gitignore`, в репозиторий не должен попасть.
- Unused imports в `overlay.py` и `setup.py`: AST scan не показал неиспользуемых top-level imports.
- Broad `except Exception`: мест много, но часть оправдана на границах Windows API/GUI teardown/hardware access. Проблема не в самом факте broad except, а в том, что failures часто не видны пользователю и не пишутся в файл.

## Рекомендованный порядок следующих работ

1. Добавить file logging и user-visible error path для autostart/LHM/config failures.
2. Сделать `read_sensors()` устойчивым к сбою одного hardware/sensor block без потери всего sample.
3. Исправить autostart state/migration:
   - сверять scheduled task command с текущим `SCRIPT_PATH`;
   - удалять legacy registry entry только после успешного создания task;
   - показывать точную ошибку enable/disable.
4. Расширить тестовый слой:
   - sensor parsing на fake LHM objects;
   - autostart command/result handling;
   - больше config edge cases.
5. Добавить DLL manifest и verification command для всего `lib/`.
6. Определиться с architecture policy в `setup.py`: поддержать x86/ARM64 или явно ограничить x64.
7. Синхронизировать README Python minimum с реально поддерживаемой/tested версией.
8. Улучшить position clamp с учетом размера виджета и multi-monitor changes.
9. Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
10. Добавить debug dump известных hardware/sensor names и fallback matching для распространенных sensor names.
11. Добавить `LICENSE`.
12. Зафиксировать/задокументировать tested Python dependency versions.

## Сводка для следующей сессии

Осталось сделать:

- Добавить нормальный лог-файл и понятные пользователю сообщения об ошибках autostart, LHM и config save.
- Сделать `read_sensors()` частично отказоустойчивым: сбой одного hardware/sensor block не должен валить весь sample.
- Исправить autostart state/migration: проверять target command scheduled task и удалять legacy registry только после успешного создания task.
- Расширить `unittest`-набор: fake LHM sensors, autostart command/result handling, дополнительные config edge cases.
- Описать и проверять все DLL из `lib/` через manifest с SHA256.
- Решить architecture policy в `setup.py`: поддержать `win-x86`/`win-arm64` или явно ограничить установку 64-bit Windows/Python.
- Синхронизировать README Python minimum с реально поддерживаемой/tested версией или переписать tests под Python 3.7.
- Добавить clamp позиции виджета по фактическому размеру окна.
- Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
- Добавить debug dump sensor names и fallback matching для распространенных имен LHM-сенсоров.
- Добавить MIT `LICENSE`.
- Зафиксировать tested versions зависимостей Python или завести lock/documented environment.
