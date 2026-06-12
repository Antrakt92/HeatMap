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
- CPU fan/control matching уже умеет top-level/subhardware и numbered
  fallback, но остальные sensor-name варианты всё еще покрыты точечно.

Что сделать:

- Использовать добавленный menu action `Copy diagnostics` для снятия
  hardware/sensor-name dump с машин, где LHM называет сенсоры иначе.
- Добавить fallback matching для распространенных вариантов GPU/RAM/VRAM/fan sensor names.
- Покрыть fake sensor tests несколькими вариантами имен.

## Рекомендованный порядок следующих работ

1. Доказать и автоматизировать restore graph для `bundled-unknown` DLL или закрепить tracked-DLL policy.
2. Расширить fake LHM tests на варианты sensor names и peek/UI transitions.
3. Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
4. Добавить debug dump известных hardware/sensor names и fallback matching для распространенных sensor names.

## Сводка для следующей сессии

Осталось сделать:

- Доказать source/restore path для 21 `bundled-unknown` DLL или явно закрепить tracked-DLL policy.
- Расширить fake LHM tests: варианты sensor names и peek/UI degraded states.
- Разбить `overlay.py`: pure sensor/config/threshold/autostart logic отдельно, tkinter/WinAPI shell отдельно.
- На базе `Copy diagnostics` собрать реальные sensor-name variants и добавить fallback matching
  для распространенных имен LHM-сенсоров.
