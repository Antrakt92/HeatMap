# HeatMap Audit Backlog

Файл содержит только подтверждённые открытые задачи. Закрытые findings удаляются,
а не сохраняются как история сессий. Приоритет отражает impact и likelihood, а не
размер изменения.

## P2 - Проверить температуры GPU и новую индикацию после перезапуска

Файлы: `overlay.py`, `tests/test_temperature_policy.py`; пороги: `README.md`.

Фоновое чтение GPU через текущий LHM без elevation дало три последовательных
замера RX 7900 XT: Core 54°C, Memory 74°C, Hotspot 108/109/110°C. Это подтверждает
источник показания, но не является независимой проверкой точности драйвера или
диагнозом системы охлаждения. Разница Hotspot–Core 54–56°C требует проверки.

Парсер, независимые строки и пороги алертов, недоступные/устаревшие значения,
пики и исключения Samsung покрыты автоматическими регрессиями. Активный elevated
overlay во время игры не перезапускался; визуальная и звуковая проверка новой
версии остаётся открытой.

Manual Windows smoke после игры:

- Закрыть старый HeatMap и запустить обновлённый обычным `run_as_admin.bat` с UAC.
- Сопоставить G.CORE и HOTSPOT с Current/Junction в Adrenalin в один момент времени;
  проверить V.TEMP, недоступные датчики `--`, отсутствие обрезанных подписей и
  перекрытий при текущем DPI, Details OFF/ON, размещение выросшего окна у края.
- При обычной игровой нагрузке проверить отдельный красный HOTSPOT и звук при
  включённых Alerts; не создавать перегрев специально. Проверить Alerts OFF и
  Reset peaks. Свежесть/ошибки безопасно покрыты fake-тестами, не отключать драйвер.
- Если Hotspot снова устойчиво 108–110°C при существенно более холодном Core,
  уменьшить нагрузку, сопоставить показания Adrenalin и проверить охлаждение.
  Снижение температур после исправления отображения не заявляется.

## P2 - Подтвердить аппаратную активацию автоматического обдува

Файлы: `case_fans.py`, `enable_case_fans.py`, `thermal_policy.py`.

Опциональный профиль SYS1/SYS2/SYS4 проверяет точную модель платы, работающие
тахометры, исходные 100% на общих SYS5/SYS6, команду и рост RPM на полном обдуве.
Активация сохраняется только после проверки нормальной остановки и readback
возврата исходных значений. Автотесты покрывают частичную ошибку записи, отказ
возврата, неверную плату/датчики, ошибку чтения, уход владельца и stale/dead status.
Реальная pythonnet IHardware-обёртка и доступ к Model проверены без hardware writes.

Для подтверждения на машине запустить `enable_case_fans.bat` с явным UAC, проверить
`%LOCALAPPDATA%/HeatMap/activation-result.json` и свежий `fan-status/*.json`:
100% с ростом RPM, restore без ошибок, затем AIRFLOW AUTO и новые показания под
обычной игровой нагрузкой. Сравнение температуры до/после требует одинаковой
игровой сцены/мощности; активация не доказывает устранение большого Hotspot–Core.

Нельзя гарантировать восстановление после принудительного убийства самого native
worker или зависания драйвера/Windows. Не тестировать это на реальном ПК под
нагрузкой; при таком отказе нужен перезапуск Windows. Автозапуск из checkout
сохраняет UAC. Полностью silent privileged installation не реализована.

## P2 - Подтвердить Show Desktop на реальной оболочке Windows после перезапуска

Файл: `overlay.py`; процедура: `docs/audit-2026-09-05.md`.

Автотесты покрывают панель задач, сохранение позиции, отменённые анимации,
tray-focused desktop и размещение ниже приложений. Отдельный native smoke
использует настоящие временные Win32-окна: восстановление после минимизации,
порядок окон, отсутствие захвата фокуса, успешный DWM attribute setter.
Он не переключает Explorer в Show Desktop и не заменяет проверку пользовательского
сценария. Нужны наведение и повторные клики на кнопке возле часов, Win+D во время
Peek, Peek OFF, auto-hide taskbar и возврат в приложение.

В независимом fallback окно нельзя гарантированно разместить под дочерними
значками Explorer. README теперь явно отличает этот режим от WorkerW embedding.
Проверить отсутствие неприемлемого перекрытия значков в выбранной позиции.

## P3 - Выполнить physical multi-monitor и mixed-DPI acceptance matrix

Файл: `overlay.py`

Почему это реально:

- Pure/fake tests покрывают monitor gaps, exposed edges, right-side work area,
  stale callbacks, verified WorkerW parent transitions и Peek/Topmost invariants.
- Предыдущая single-monitor acceptance не выявила повторный сценарий Show Desktop
  из пользовательского отчёта. Новая проверка выделена выше. На shell topology без
  dedicated WorkerW overlay остаётся независимым окном над поверхностью desktop,
  потому что parenting к `Progman` уничтожает Tk HWND вместе с Explorer.
- Доступная topology содержит только один monitor; negative coordinates, staggered
  layouts, mixed DPI и disconnect/reconnect нельзя подтвердить без второго display
  или отдельной Windows VM.

Что сделать:

- Пройти manual matrix на двух monitors: horizontal, staggered, negative origin,
  right/top taskbar и mixed 100%/150% scaling.
- Проверить disconnect/reconnect в desktop, visible Peek и animation states, затем
  drag/save/relaunch между monitors.
- Проверить internal seam не активирует Peek, а exposed outer edge активирует его
  только на monitor под cursor.
- Top/right taskbar проверять только на Windows/VM, где такие layouts официально
  поддерживаются; не менять shell registry ради теста.

Тесты и проверка:

- Overlay всегда остаётся хотя бы частично в work area реального monitor и не
  перекрывает taskbar в Peek mode.
- Последние pixels exposed edge остаются click-through для scrollbar/resize/taskbar.
- Menu state совпадает с native parent/topmost state после каждого transition, а
  display removal не оставляет invisible live process или stale saved position.

## Parking - Атомарно обновить LibreHardwareMonitor и PawnIO до следующего bundle

Promote when: появляется конкретная hardware fix/security reason для upgrade либо
scheduled latest-compatible/provenance lane показывает несовместимость.

Почему не сейчас:

- LibreHardwareMonitor и PawnIO образуют совместимую пару; независимое обновление
  только одного компонента возвращает исходный failure mode после package update.
- Следующий bundle должен обновлять LHM DLL graph, PawnIO installer metadata,
  runtime lock, manifest, sensor fixtures и licenses одной reviewable change.

Требуемая проверка при promotion:

- Clean-room restore и staged CLR smoke проходят до runtime swap.
- Failed download/hash/type import сохраняет предыдущий рабочий bundle.
- После driver install/reboot elevated hardware smoke подтверждает CPU, GPU, RAM,
  storage и доступные fan sensors.

## Рекомендованные следующие bundles

1. **Show Desktop acceptance:** точный пользовательский сценарий после перезапуска.
2. **Desktop acceptance:** physical multi-monitor/mixed-DPI matrix и только
   воспроизводимые follow-up fixes.
