# HeatMap Audit Backlog

Файл содержит только подтверждённые открытые задачи. Закрытые findings удаляются,
а не сохраняются как история сессий. Приоритет отражает impact и likelihood, а не
размер изменения.

## P3 - Выполнить physical multi-monitor и mixed-DPI acceptance matrix

Файл: `overlay.py`

Почему это реально:

- Pure/fake tests покрывают monitor gaps, exposed edges, right-side work area,
  stale callbacks, verified WorkerW parent transitions и Peek/Topmost invariants.
- Single-monitor Windows acceptance покрывает 125% scaling, bottom taskbar,
  Explorer restart, Show Desktop, repeated Peek/Topmost transitions и close during
  animation. На shell topology без dedicated WorkerW overlay остаётся независимым
  `HWND_BOTTOM`, потому что parenting к `Progman` уничтожает Tk HWND вместе с Explorer.
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

## P3 - Убрать синхронное открытие отдельного monitor из Copy diagnostics

Файл: `overlay.py`, `OverlayApp.copy_diagnostics`.

Команда открывает новый LHM Computer и читает все sensors на потоке Tk. Медленный
driver может задержать интерфейс на время диагностики. Обычный запуск и фоновое
восстановление уже используют sensor worker, но этот ручной путь остаётся отдельным.

Что сделать: собирать diagnostic snapshot вне Tk, передавать результат в главный
поток для записи в clipboard; проверить закрытие окна во время сбора и очистку
отдельного Computer. Не обращаться к worker-owned Computer из второго потока.

## P3 - Повторная проверка Task Scheduler при старте

Файл: `overlay.py`, `main`, `reconcile_autostart_security`, `OverlayApp.__init__`.

Elevated startup сначала проверяет/мигрирует autostart task, затем отдельно
запрашивает его состояние для меню. Каждый запрос запускает PowerShell. В текущей
локальной проверке один вызов `is_autostart_enabled` занял 0,881 секунды; время
зависит от состояния Windows и не является замером загрузки ОС.

Что сделать: возвращать проверенное состояние вместе с результатом reconciliation
и использовать его при создании меню. Сохранить fail-closed миграцию, проверку
interactive/elevated identity и свежую проверку перед ручным изменением задачи.

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

1. **Desktop acceptance:** physical multi-monitor/mixed-DPI matrix и только
   воспроизводимые follow-up fixes.
