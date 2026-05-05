# HeatMap Audit

Этот файл смотрит только вперед: здесь хранятся открытые подтвержденные задачи, риски и улучшения. Закрытые пункты и история прошлых сессий не дублируются; при необходимости их можно посмотреть через `git log` / `git show`.

Актуально для текущего `main`.

## Краткое состояние

HeatMap сейчас выглядит как компактное, но уже сложное Windows-only desktop-приложение. Основной риск в том, что один большой `overlay.py` одновременно держит UI, WinAPI embedding, sensor parsing, config, alerts, autostart и process management.

Главные открытые риски:

- Полный manifest/provenance для bundled `lib/*.dll` отсутствует.
- Sensor parsing и UI update logic покрыты только точечно; не хватает вариантов имен sensors, fan/VRAM cases, ignored-iGPU edge cases и desktop-state transitions.
- Config/runtime status уже видим в UI, но частично битые config поля пока silently нормализуются без degraded status.
- `overlay.py` остается монолитом, где безопасно менять поведение становится все труднее.

## Открытые находки

### P2 - Тестовый слой все еще покрывает не всю рискованную логику

Файл: `overlay.py`

Почему это реально:

- Есть `unittest` для helper/startup/error-state логики, autostart command/result handling, no-LHM fallback и partial sensor failures, но sensor-name variants, fan/VRAM cases, peek state transitions и большая часть tkinter/WinAPI поведения остаются без тестов.
- `read_sensors()` и `update_ui()` большие и сильно ветвятся.
- Top-level Windows API import делает тестирование вне Windows сложным.

Что сделать:

- Расширить fake LHM objects для GPU/VRAM/fan parsing и вариантов sensor names.
- Добавить regression test на Intel iGPU skip before update, чтобы intentionally ignored iGPU не создавал ложный `Sensors: partial data`.
- Добавить tests на peek state transitions и компактные UI degraded states.
- Постепенно выносить pure logic из `OverlayApp` в тестируемые helpers.

### P3 - Ignored Intel iGPU все еще обновляется перед skip

Файл: `overlay.py`

Почему это реально:

- `_read_hardware_block()` получает `hw_type`, затем вызывает `hw.Update()` и `sub.Update()`.
- Проверка `GpuIntel` + уже найденная discrete GPU data происходит только после update.
- Если Intel iGPU должен быть проигнорирован, но его LHM object бросает исключение в `Update()`, весь sample помечается как `Sensors: partial data`, хотя пользовательские GPU-метрики уже собраны и iGPU намеренно не нужен.

Что сделать:

- Перенести early skip для `HardwareType.GpuIntel` перед `hw.Update()` / `sub.Update()`, когда discrete GPU data уже есть.
- Добавить fake LHM test: NVIDIA/AMD GPU успешно заполняет `gpu_temp`, следующий Intel GPU бросает в `Update()`, итоговый sample не получает `_sensor_status = partial`.
- Сохранить текущую policy: Intel GPU читается, если discrete GPU data еще не найдена.

### P3 - Частично битый config silently нормализуется без status

Файл: `overlay.py`

Почему это реально:

- `load_config_result()` теперь показывает warning/status для invalid JSON, non-dict и read failure.
- Но если JSON валидный, а отдельные поля имеют неверный тип или значение (`x: true`, `gpu_fan_max_rpm: -1`), код молча заменяет их defaults.
- Это безопасно для запуска, но пользователь не понимает, почему позиция/настройки сброшены.

Что сделать:

- Накапливать список invalid config keys при normalization.
- Возвращать warning message из `load_config_result()` для частично исправленного config.
- UI status должен оставаться compact (`Config adjusted` или текущий `Config save failed` split на load/save status), без превращения defaults migration в hard error.
- Добавить tests на invalid individual fields, чтобы bad fields сохраняли safe defaults и давали user-visible diagnostic.

### P2 - Полный manifest/provenance для bundled `lib/*.dll` отсутствует

Файлы: `setup.py`, `lib/`

Почему это реально:

- `setup.py` скачивает и проверяет только `LibreHardwareMonitorLib.dll` и `HidSharp.dll`.
- В `lib/` tracked 23 DLL, но `setup.py` восстанавливает только 2; источник, версия и SHA256 остальных 21 не закреплены в машинно-проверяемом manifest.
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

1. Расширить fake LHM tests на GPU/VRAM/fan parsing, варианты sensor names и ignored Intel iGPU skip-before-update.
2. Сделать config normalization diagnostic для частично битых config fields.
3. Добавить DLL manifest и verification command для всего `lib/`.
4. Определиться с architecture policy в `setup.py`: поддержать x86/ARM64 или явно ограничить x64.
5. Синхронизировать README Python minimum с реально поддерживаемой/tested версией.
6. Улучшить position clamp с учетом размера виджета и multi-monitor changes.
7. Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
8. Добавить debug dump известных hardware/sensor names и fallback matching для распространенных sensor names.
9. Добавить MIT `LICENSE`.
10. Зафиксировать/задокументировать tested Python dependency versions.

## Сводка для следующей сессии

Осталось сделать:

- Расширить fake LHM tests: GPU/VRAM/fan parsing, варианты sensor names, ignored Intel iGPU skip-before-update и peek/UI degraded states.
- Сделать diagnostic/status для частично битых config fields, которые сейчас silently заменяются defaults.
- Описать и проверять все DLL из `lib/` через manifest с SHA256.
- Решить architecture policy в `setup.py`: поддержать `win-x86`/`win-arm64` или явно ограничить установку 64-bit Windows/Python.
- Синхронизировать README Python minimum с реально поддерживаемой/tested версией или переписать tests под Python 3.7.
- Добавить clamp позиции виджета по фактическому размеру окна.
- Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
- Добавить debug dump sensor names и fallback matching для распространенных имен LHM-сенсоров.
- Добавить MIT `LICENSE`.
- Зафиксировать tested versions зависимостей Python или завести lock/documented environment.
