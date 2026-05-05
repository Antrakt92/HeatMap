# HeatMap Audit

Этот файл смотрит только вперед: здесь хранятся открытые подтвержденные задачи, риски и улучшения. Закрытые пункты и история прошлых сессий не дублируются; при необходимости их можно посмотреть через `git log` / `git show`.

Актуально для текущего `main`.

## Краткое состояние

HeatMap сейчас выглядит как компактное, но уже сложное Windows-only desktop-приложение. Основной риск в том, что один большой `overlay.py` одновременно держит UI, WinAPI embedding, sensor parsing, config, alerts, autostart и process management.

Главные открытые риски:

- Restore graph/provenance для 21 `bundled-unknown` DLL пока не доказан.
- Sensor parsing и UI update logic покрыты только точечно; не хватает вариантов имен sensors и desktop-state transitions.
- `overlay.py` остается монолитом, где безопасно менять поведение становится все труднее.

## Открытые находки

### P2 - Тестовый слой все еще покрывает не всю рискованную логику

Файл: `overlay.py`

Почему это реально:

- Есть `unittest` для helper/startup/error-state логики, autostart command/result handling, runtime DLL verification, no-LHM fallback, partial sensor failures и LHM init sanity-check, но sensor-name variants, peek state transitions и большая часть tkinter/WinAPI поведения остаются без тестов.
- `read_sensors()` и `update_ui()` большие и сильно ветвятся.
- Top-level Windows API import делает тестирование вне Windows сложным.

Что сделать:

- Расширить fake LHM objects для вариантов sensor names, которые встречаются у разных версий LibreHardwareMonitor/драйверов.
- Добавить tests на peek state transitions и компактные UI degraded states.
- Постепенно выносить pure logic из `OverlayApp` в тестируемые helpers.

### P2 - Restore graph/provenance для `bundled-unknown` DLL пока не доказан

Файлы: `setup.py`, `lib_manifest.json`, `lib/`

Почему это реально:

- `lib_manifest.json` фиксирует hash/size baseline для всех 23 DLL, но 21 запись честно помечена как `bundled-unknown`.
- `setup.py` скачивает только direct NuGet DLL: `LibreHardwareMonitorLib.dll` и `HidSharp.dll`.
- Если удалить весь `lib/`, `python setup.py` восстановит direct DLL и затем упадет на full runtime verification, пока полный restore graph не реализован.
- Для Windows + pythonnet + LHM это лучше, чем ложная provenance, но все еще не полностью воспроизводимая установка.

Что сделать:

- Доказать package/source/version/package path для каждой `bundled-unknown` DLL.
- Автоматизировать восстановление полного runtime graph или явно закрепить tracked-DLL policy в README.
- После доказательства provenance заменить `bundled-unknown` entries на точные source records.

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

### P3 - `setup.py` download path все еще завершает процесс из helper-функции

Файл: `setup.py`

Почему это реально:

- `main(argv=None)` возвращает exit code и покрыт tests.
- Но `download_and_extract()` внутри себя вызывает `sys.exit(1)` на download/extract/hash/write ошибках.
- Из-за этого часть default setup failure path нельзя нормально unit-test через `main([])`, а helper нельзя безопасно переиспользовать из другого кода.

Что сделать:

- Заменить внутренние `sys.exit(1)` на возвращаемый `(ok, messages)` или typed exception уровня setup.
- Оставить `sys.exit(main())` только в `if __name__ == "__main__"`.
- Добавить tests на download failure, bad zip, missing exact package path и write failure без реального выхода процесса.

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

### P3 - `run_as_admin.bat` может запустить не тот Python

Файл: `run_as_admin.bat`

Почему это реально:

- Скрипт ищет `python.exe` только через `PATH` и затем запускает соседний `pythonw.exe`.
- Если зависимости установлены в `.venv` или пользователь запускал `pip install -r requirements.txt` не тем интерпретатором, elevated запуск может взять другой Python без `pythonnet`/`psutil`.
- Autostart уже использует `sys.executable`, но ручной launcher не закрепляет тот же interpreter.

Что сделать:

- Решить launcher policy: поддержать `.venv\Scripts\pythonw.exe`, `py -3`, или явно документировать "ставить зависимости в PATH Python".
- Добавить preflight/error message для отсутствующих `pythonnet`/`psutil` при ручном запуске.
- Обновить README под выбранный способ запуска.

### P3 - Python dependencies заданы только нижними границами

Файл: `requirements.txt`

Почему это реально:

- `pythonnet>=3.0.0`
- `psutil>=5.9.0`
- Верхних ограничений, tested versions или lock/documented environment нет.
- Текущая локальная проверка прошла на Python 3.13.11 и `psutil 7.2.2`, но это не отражено в README/requirements.
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

1. Доказать и автоматизировать restore graph для `bundled-unknown` DLL или закрепить tracked-DLL policy.
2. Определиться с architecture policy в `setup.py`: поддержать x86/ARM64 или явно ограничить x64.
3. Расширить fake LHM tests на варианты sensor names и peek/UI transitions.
4. Синхронизировать README Python minimum с реально поддерживаемой/tested версией.
5. Улучшить position clamp с учетом размера виджета и multi-monitor changes.
6. Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
7. Добавить debug dump известных hardware/sensor names и fallback matching для распространенных sensor names.
8. Нормализовать `setup.py` download failure path без `sys.exit()` внутри helper.
9. Уточнить launcher/interpreter policy для `run_as_admin.bat`.
10. Добавить MIT `LICENSE`.
11. Зафиксировать/задокументировать tested Python dependency versions.

## Сводка для следующей сессии

Осталось сделать:

- Доказать source/restore path для 21 `bundled-unknown` DLL или явно закрепить tracked-DLL policy.
- Решить architecture policy в `setup.py`: поддержать `win-x86`/`win-arm64` или явно ограничить установку 64-bit Windows/Python.
- Расширить fake LHM tests: варианты sensor names и peek/UI degraded states.
- Синхронизировать README Python minimum с реально поддерживаемой/tested версией или переписать tests под Python 3.7.
- Добавить clamp позиции виджета по фактическому размеру окна.
- Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
- Добавить debug dump sensor names и fallback matching для распространенных имен LHM-сенсоров.
- Нормализовать `setup.py` download failure path: убрать `sys.exit()` из helper и покрыть failure paths tests.
- Уточнить `run_as_admin.bat`/README interpreter policy, чтобы elevated запуск не брал Python без зависимостей.
- Добавить MIT `LICENSE`.
- Зафиксировать tested versions зависимостей Python или завести lock/documented environment.
