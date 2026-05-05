# HeatMap Audit

Этот файл смотрит только вперед: здесь хранятся открытые подтвержденные задачи, риски и улучшения. Закрытые пункты и история прошлых сессий не дублируются; при необходимости их можно посмотреть через `git log` / `git show`.

Актуально для текущего `main`.

## Краткое состояние

HeatMap сейчас выглядит как компактное, но уже сложное Windows-only desktop-приложение. Основной риск в том, что один большой `overlay.py` одновременно держит UI, WinAPI embedding, sensor parsing, config, alerts, autostart и process management.

Главные открытые риски:

- Runtime failures LHM/config теперь пишутся в файл лога, но все еще почти не видны прямо в UI.
- `read_sensors()` все еще all-or-nothing: ошибка одного hardware/sensor object сбрасывает весь sample в error state.
- Полный manifest/provenance для bundled `lib/*.dll` отсутствует.
- Sensor parsing и UI update logic почти не покрыты fake-object тестами.
- `overlay.py` остается монолитом, где безопасно менять поведение становится все труднее.

## Открытые находки

### P2 - Runtime failures LHM/config остаются без понятного UI-status

Файл: `overlay.py`

Почему это реально:

- Основной documented launch path запускает приложение через `pythonw.exe`, то есть без консоли.
- Файл лога уже дает диагностику, но пользователь все еще может не заметить LHM fallback или config save failure прямо в UI.
- `init_hardware_monitor()` fallback-ится на `psutil`, а config save failure логируется, но не получает compact user-facing state.

Что сделать:

- Для LHM/config failures показывать compact degraded status в UI/menu.
- Добавить tooltip/menu action для открытия или копирования пути к log file.
- Не превращать fallback на `psutil` в hard error, но явно показывать, что hardware sensors недоступны.

### P2 - Один сбойный hardware/sensor object обнуляет весь sensor sample

Файл: `overlay.py`

Почему это реально:

- `read_sensors()` вызывает `hw.Update()`, `sub.Update()` и читает `hw.Sensors` без локальной изоляции ошибок по одному hardware block.
- `sensor_loop()` ловит исключение только вокруг всего `read_sensors()` и заменяет весь sample на `{"error": str(e)}`.
- Из-за этого один проблемный device/driver/.NET object может скрыть остальные рабочие метрики, включая доступные CPU/RAM fallback values.

Что сделать:

- Изолировать ошибки внутри `read_sensors()` на уровне одного hardware block.
- Логировать failing hardware name/type и продолжать чтение остальных устройств.
- CPU load и RAM всегда получать через `psutil` fallback, даже если часть LHM чтения упала.
- Добавить fake hardware tests: один fake device бросает в `Update()`, второй возвращает нормальные sensors, итоговый sample остается частично полезным.

### P2 - Тестовый слой все еще покрывает не всю рискованную логику

Файл: `overlay.py`

Почему это реально:

- Есть `unittest` для helper/startup/error-state логики, но sensor parsing, autostart command/result handling, no-LHM fallback, peek state transitions и большая часть tkinter/WinAPI поведения остаются без тестов.
- `read_sensors()` и `update_ui()` большие и сильно ветвятся.
- Top-level Windows API import делает тестирование вне Windows сложным.

Что сделать:

- Добавить fake LHM objects для CPU/GPU/RAM/storage parsing.
- Добавить tests на autostart command/result handling с mocked `subprocess.run`/`winreg`.
- Добавить tests на no-LHM fallback и частичные sensor failures.
- Постепенно выносить pure logic из `OverlayApp` в тестируемые helpers.

### P2 - Полный manifest/provenance для bundled `lib/*.dll` отсутствует

Файлы: `setup.py`, `lib/`

Почему это реально:

- `setup.py` скачивает и проверяет только `LibreHardwareMonitorLib.dll` и `HidSharp.dll`.
- В `lib/` лежит больше 20 DLL, но их источник, версия и SHA256 не закреплены в машинно-проверяемом manifest.
- Если удалить `lib/` и запустить `python setup.py`, текущий скрипт восстановит только две direct DLL, а не весь tracked runtime set.
- Часть DLL не имеет Authenticode signature, поэтому pinned provenance особенно важен.

Что сделать:

- Добавить `lib_manifest.json` или `lib/MANIFEST.md` с package/source/version/SHA256 для каждой DLL.
- Добавить verification command/test для сверки manifest с текущим `lib/`.
- Решить, остаются ли DLL tracked в git или `setup.py` должен восстанавливать полный runtime graph.

### P3 - `setup.py` жестко зашит под win-x64

Файл: `setup.py`

Почему это реально:

- `LibreHardwareMonitorLib` извлекается только из `runtimes/win-x64/lib/net472/LibreHardwareMonitorLib.dll`.
- Проверки architecture Python/Windows в `setup.py` нет.
- README требует Windows, но не фиксирует 64-bit Python как обязательное условие.

Что сделать:

- Либо явно ограничить установку 64-bit Windows/Python с понятной ошибкой.
- Либо поддержать `win-x86`/`win-arm64` и закрепить SHA256 для каждого выбранного runtime entry.
- Обновить README под выбранную policy.

### P3 - Позиция окна валидируется только по левому верхнему углу

Файл: `overlay.py`

Почему это реально:

- Сохраненные `x/y` проверяются только как top-left point внутри virtual screen bounds.
- Если `x/y` находятся у края экрана, виджет может оказаться почти полностью вне видимой области.
- Риск выше после смены разрешения, DPI, набора мониторов или высоты виджета из-за disk rows.

Что сделать:

- После построения UI вызвать `update_idletasks()` и измерить фактические `winfo_width()/height()`.
- Clamp-ить позицию так, чтобы виджет оставался видимым хотя бы основной частью.
- Повторять clamp при изменении virtual screen geometry.

### P3 - Дублирование UI-строк, цветов и sensor keys усложняет безопасные изменения

Файл: `overlay.py`

Почему это реально:

- Цвета, font names и sensor keys повторяются вручную во многих местах.
- Alert policy и color policy живут отдельно.
- Любое переименование key или изменение threshold требует правок в нескольких частях `read_sensors()`, `update_ui()`, `_check_alerts()` и config/fan calibration.

Что сделать:

- Вынести theme constants: colors, fonts, row labels.
- Вынести sensor key constants или dataclass/typed dict.
- Свести color policy и alert policy к одной таблице thresholds там, где это безопасно.

### P3 - Sensor parsing зависит от конкретных английских имен LibreHardwareMonitor

Файл: `overlay.py`

Почему это реально:

- CPU total load ищется по `"total"`.
- GPU load ищется по точному `name == "gpu core"`.
- VRAM ищется по точным `"gpu memory used"` и `"gpu memory total"`.
- RAM ищется по точному `"memory"`.
- CPU fan/control matching зависит от `"cpu"`, `"optional"`, `"#1"`.

Что сделать:

- Добавить debug dump hardware/sensor names в log file или menu action.
- Добавить fallback matching для распространенных вариантов GPU/RAM/VRAM/fan sensor names.
- Покрыть fake sensor tests несколькими вариантами имен.

### P3 - README заявляет MIT, но LICENSE-файла нет

Файл: `README.md`

Почему это реально:

- README содержит секцию `Лицензия: MIT`.
- В tracked files нет `LICENSE`/`LICENSE.md`.

Что сделать:

- Добавить стандартный MIT `LICENSE` с актуальным copyright holder.

### P3 - Python dependencies заданы только нижними границами

Файл: `requirements.txt`

Почему это реально:

- `pythonnet>=3.0.0`
- `psutil>=5.9.0`
- Верхних ограничений, tested versions или lock/documented environment нет.
- Для Windows + pythonnet + .NET interop это риск воспроизводимости.

Что сделать:

- Зафиксировать tested versions или добавить documented environment.
- Минимум: указать Python version и package versions, на которых проект реально проверялся.

### P3 - README заявляет Python 3.7+, но текущие tests требуют более новый Python

Файлы: `README.md`, `tests/test_overlay_helpers.py`

Почему это реально:

- README указывает `Python 3.7+`.
- Тесты используют parenthesized multi-context `with (...)`, который не поддерживается Python 3.7.
- Даже если runtime app совместим с Python 3.7, официальный test suite на заявленной минимальной версии не запустится.

Что сделать:

- Либо поднять README minimum Python до реально поддерживаемой/tested версии.
- Либо переписать tests на Python 3.7-compatible syntax.

## Рекомендованный порядок следующих работ

1. Сделать `read_sensors()` устойчивым к сбою одного hardware/sensor block без потери всего sample.
2. Добавить user-visible UI status для LHM/config failures и удобный доступ к log file path.
3. Расширить `unittest`-набор:
   - sensor parsing на fake LHM objects;
   - no-LHM fallback и partial sensor failures.
4. Добавить DLL manifest и verification command для всего `lib/`.
5. Определиться с architecture policy в `setup.py`: поддержать x86/ARM64 или явно ограничить x64.
6. Синхронизировать README Python minimum с реально поддерживаемой/tested версией.
7. Улучшить position clamp с учетом размера виджета и multi-monitor changes.
8. Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
9. Добавить debug dump известных hardware/sensor names и fallback matching для распространенных sensor names.
10. Добавить MIT `LICENSE`.
11. Зафиксировать/задокументировать tested Python dependency versions.

## Сводка для следующей сессии

Осталось сделать:

- Сделать `read_sensors()` частично отказоустойчивым: сбой одного hardware/sensor block не должен валить весь sample.
- Добавить user-visible UI status для LHM/config failures и удобный доступ к log file path.
- Расширить `unittest`-набор: fake LHM sensors, no-LHM fallback, partial sensor failures.
- Описать и проверять все DLL из `lib/` через manifest с SHA256.
- Решить architecture policy в `setup.py`: поддержать `win-x86`/`win-arm64` или явно ограничить установку 64-bit Windows/Python.
- Синхронизировать README Python minimum с реально поддерживаемой/tested версией или переписать tests под Python 3.7.
- Добавить clamp позиции виджета по фактическому размеру окна.
- Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
- Добавить debug dump sensor names и fallback matching для распространенных имен LHM-сенсоров.
- Добавить MIT `LICENSE`.
- Зафиксировать tested versions зависимостей Python или завести lock/documented environment.
