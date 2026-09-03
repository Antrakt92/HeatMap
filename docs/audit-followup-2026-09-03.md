# HeatMap: повторный аудит запуска, датчиков и lifecycle

Дата: 2026-09-03. Базовый commit: `ffaccc84d240c20b55196de6942bfda3982b5cdb`.
Проверены оставшиеся startup paths, Copy diagnostics, sensor parsing,
отказ WinAPI после reparent и завершение потоков. Изменения прошли независимую
проверку агентами; runtime bundle и elevation policy сохранены.

## Исправления

- Elevated startup передаёт проверенное autostart state из reconciliation в
  конструктор меню. Повторный PowerShell query больше не нужен. Query error
  отображается как `Autostart: ERROR`, отсутствие задачи — `OFF`; свежие проверки
  identity, task ownership и созданной задачи при изменениях сохраняются.
- Reconciliation входит в область `try/finally`, освобождающую single-instance
  mutex при неожиданном исключении.
- `Copy diagnostics` открывает собственный LHM Computer в отдельном worker.
  Поток Tk получает готовый результат через queue; duplicate request не запускает
  второй сбор. При ошибке или shutdown clipboard не очищается. Computer закрывает
  создавший его worker, включая отмену во время Open.
- Shutdown ждёт sensor и diagnostics workers в пределах общего лимита 5 секунд.
  Отсутствующий или не запустившийся worker не мешает закрытию; нормальное
  завершение диагностики закрывает Computer до уничтожения окна.
- Raw проценты CPU/GPU/RAM, fan control, storage utilization и remaining life
  проверяются на диапазон 0–100 до округления. Отрицательные или нечисловые
  RPM/clock/VRAM отбрасываются. VRAM usage требует `0 <= used <= total`, `total > 0`.
- `Percentage Used` у NVMe может превышать 100: это допустимый износ и нулевая
  оценка remaining life. Такое значение сохранено согласно
  [Microsoft NVMe health structure](https://learn.microsoft.com/en-us/windows/win32/api/nvme/ns-nvme-nvme_health_info_log).
- Температура CPU больше не зависит от порядка sensors/CPU blocks. Выбирается
  максимум в предпочтительной группе Package/Tctl/Tdie; только при её отсутствии
  — максимум остальных допустимых CPU temperatures. Tctl offsets не переопределяются.
- Известный CPU Fan #2 больше не получает PWM от Fan #1: вместо ложного OFF
  остаётся RPM-based отображение без чужого control value.
- Пустой список LHM Hardware отмечается как fallback и запрашивает восстановление
  с существующим cooldown 30 секунд.
- Если SetParent выполнился, а последующий SetWindowPos завершился ошибкой,
  состояние embedding учитывает фактический живой parent. Последующий detach
  действительно отсоединяет окно перед Peek/Topmost.

## Проверка производительности

Повторный Task Scheduler query устранён по числу вызовов, а не за счёт ослабления
проверок. Hidden real-Tk constructor проверен для ON/OFF/ERROR с запретом повторного
query: все три состояния созданы без вызова. Ранее локальный одиночный query занимал
около 0,88 s; это ориентир стоимости удалённой операции, а не измеренное ускорение
Windows boot. Нынешний проход не измеряет полный logon или cold-cache запуск.

Синтетический заблокированный Open подтверждает, что Copy diagnostics возвращает
управление Tk до завершения hardware initialization. Отдельный poll существует
только во время активного запроса; частота обычного sensor sampling не увеличена.

## Выполненные проверки

Проверки выполняются через существующий `.venv\Scripts\python.exe`:

- `python -m unittest discover -s tests`: **222 passed**, исходно 192.
- Unit regressions: недопустимые raw значения, границы, порядок CPU temperatures,
  fan matching, empty inventory, retained/lost desktop parent, cached autostart,
  mutex cleanup, slow diagnostics и cancellation.
- `python -m compileall -q overlay.py setup.py tests`: passed.
- `python setup.py --verify`: passed.
- `python setup.py --preflight`: passed.
- `python tools/sync_runtime_manifest.py --check`: passed.
- `pwsh -NoProfile -File tools/test_task_scheduler_integration.ps1`: passed;
  отдельная disposable task классифицирована как safe_current/LeastPrivilege,
  затем удалена. Production task, launcher и UAC не запускались.
- `git diff --check`: passed.

Новые sensor/native-parent regressions воспроизвели дефекты до исправления.
DLL/runtime integrity и известные RPC/CLIXML autostart protections проверены
существующими suites. Установленные DLL, driver, пользовательские настройки и
работающий экземпляр приложения не менялись.

## Ручная проверка

После обычного elevated перезапуска проверить реальные CPU/GPU/RAM/storage/fan
readings, `Copy diagnostics`, закрытие во время сбора и несколько переходов
Peek/Always on top. После следующего Windows sign-in проверить запуск с прежней
30-секундной задержкой. Physical multi-monitor/mixed-DPI acceptance остаётся
отдельным пунктом `AUDIT.md`.

Native Open/Close нельзя безопасно прервать извне. Если driver зависнет дольше
ограниченного shutdown wait, cleanup daemon worker не гарантирован при завершении
процесса. Это ограничение не выдаётся за проверенное отсутствие зависаний driver.
