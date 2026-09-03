# HeatMap — Desktop Hardware Monitor Overlay

Легковесный оверлей для Windows, который отображает температуры, нагрузку и состояние компонентов ПК прямо на рабочем столе — поверх обоев, но под иконками и окнами приложений.

## Что показывает

| Компонент | Метрики |
|-----------|---------|
| **CPU** | Температура, нагрузка (%), скорость вентилятора |
| **GPU** | Температура, нагрузка (%), VRAM, скорость вентилятора |
| **RAM** | Использование (%) |
| **Диски** | Температура, занятое место (%) — поддержка нескольких дисков |

CPU/GPU/RAM обновляются каждые 2 секунды; storage sensors — примерно раз в 30 секунд, чтобы не создавать лишний I/O. Значения подсвечиваются цветом:
- **Зелёный** — норма
- **Жёлтый** — повышенные значения
- **Красный** — критические значения

Окно создаётся до открытия аппаратных датчиков и показывает статус прогрева.
Инициализация и восстановление датчиков выполняются в фоновом потоке, с паузой
не менее 30 секунд между повторными попытками. Когда Peek выключен или включён
режим «Поверх всех окон», частый опрос края экрана приостанавливается.

Некорректные значения датчиков не отображаются как реальные проценты или обороты.
Для CPU выбирается самая высокая допустимая температура из группы Package/Tctl/Tdie;
если таких датчиков нет, используется самая высокая доступная температура CPU.

## Возможности

- **Встраивание в рабочий стол** — виджет находится между обоями и иконками, не мешает работе
- **Перетаскивание** — зажмите заголовок и перетащите в удобное место
- **Система алертов** — звуковое предупреждение при критических температурах/нагрузке (CPU ≥ 85°C, GPU ≥ 90°C, диск ≥ 55°C, RAM ≥ 95%, занятое место ≥ 90%)
- **Автозапуск** — least-privilege задача стартует через 30 секунд после входа и показывает обычный UAC prompt перед доступом к sensors
- **Режим "Поверх всех окон"** — переключается через правый клик
- **Сохранение позиции** — запоминает расположение между запусками

## Скриншот

![HeatMap overlay](https://github.com/user-attachments/assets/8b58ff7c-3003-424c-b0aa-58bbc2b2c027)

## Установка

### Требования
- 64-bit Python 3.10+
- 64-bit Windows
- Права администратора (необходимы для чтения датчиков)

Проверенная среда: Python 3.13, pythonnet 3.1.0, psutil 7.2.2. Точные production versions закреплены в `constraints-known-good.txt`; отдельный CI lane проверяет новые допустимые версии.

### Шаги

1. **Клонируйте репозиторий:**
   ```bash
   git clone https://github.com/Antrakt92/HeatMap.git
   cd HeatMap
   ```

2. **Создайте виртуальное окружение и установите зависимости:**
   ```bash
   python -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt -c constraints-known-good.txt
   ```

3. **Проверьте библиотеки для мониторинга:**
   ```bash
   .venv\Scripts\python setup.py
   ```
   `setup.py` восстанавливает полный Windows runtime из точных NuGet assets,
   закреплённых в `runtime-lock.json`. До замены `lib/` он проверяет SHA-256
   каждого пакета, exact path/hash/size каждой DLL и весь staged runtime по
   `lib_manifest.json`; при ошибке сохраняется предыдущий рабочий runtime.
   Явный эквивалент команды — `python setup.py --restore-runtime`.
   Перед restore закройте запущенный HeatMap: Windows не позволяет атомарно
   заменить CLR DLL directory, пока процесс держит assemblies загруженными.

   Для CPU temperature и motherboard fan sensors также нужен PawnIO driver.
   Получите совместимый installer через проверяемый flow:
   ```bash
   .venv\Scripts\python setup.py --download-pawnio
   ```
   Setup фиксирует официальную версию, проверяет size, SHA-256, Authenticode
   publisher/certificate и только затем показывает путь к installer. Запустите
   этот файл от администратора и перезапустите Windows. Setup никогда не запускает
   driver installer автоматически.
   Если setup сообщает о pending installer operations, сначала перезапустите
   Windows, затем запускайте PawnIO installer; после установки нужен ещё один
   restart перед hardware smoke.

   После установки и перезапуска проверьте реальное открытие LHM и CPU sensor:
   ```bash
   .venv\Scripts\python setup.py --hardware-smoke
   ```
   Команда должна выполняться в elevated terminal и завершаться успешно только
   при наличии реального положительного CPU temperature reading.

   Чтобы только проверить уже существующий `lib/` без скачивания:
   ```bash
   .venv\Scripts\python setup.py --verify
   ```

4. **Запустите:**
   ```bash
   run_as_admin.bat
   ```
   Launcher проверяет кандидатов в порядке `.venv`, `venv`, затем все пары
   `python.exe`/`pythonw.exe` из `PATH` и выбирает первый интерпретатор, который
   полностью проходит CLR/LHM preflight. Preflight также сверяет установленные
   distributions с точными production versions из `constraints-known-good.txt`,
   поэтому новый пустой Python в начале `PATH` не блокирует рабочую среду и stale
   packages не принимаются как supported. Warning-only degraded state не блокирует
   UAC: текст сохраняется в `%LOCALAPPDATA%\HeatMap\last_preflight_warning.txt`, а
   состояние driver остаётся видимым внутри overlay.

   Или вручную от имени администратора:
   ```bash
   .venv\Scripts\python overlay.py
   ```

## Управление

| Действие | Как |
|----------|-----|
| Переместить | Зажать заголовок и перетащить |
| Контекстное меню | Правый клик по виджету |
| Поверх всех окон | ПКМ → "Always on top" |
| Автозапуск | ПКМ → "Autostart" |
| Алерты вкл/выкл | ПКМ → "Alerts" |
| Peek с края | ПКМ → "Peek from edge" |
| Расширенные метрики | ПКМ → "Details" |
| Диагностика sensors | ПКМ → "Copy diagnostics" |
| Подготовить PawnIO repair | ПКМ → "Prepare verified PawnIO repair..." |
| Быстрый PawnIO repair | Нажать красную строку "Driver: install PawnIO" |
| Лог | ПКМ → "Open log file" / "Copy log path" |
| Сброс пиков | ПКМ → "Reset peaks" |
| Закрыть | Кнопка ✕ или ПКМ → "Close" |

`Copy diagnostics` собирает сведения в фоне. Пока отображается
`Collecting diagnostics...`, окно продолжает работать; по завершении результат
попадает в буфер обмена. Повторное нажатие не запускает ещё один сбор.

Результаты проверки запуска, датчиков и обработки ошибок описаны в
[дополнительном отчёте аудита](docs/audit-followup-2026-09-03.md).

### Безопасность автозапуска

Source checkout и его virtualenv изменяемы обычным пользователем, поэтому Task
Scheduler не должен исполнять их с `HighestAvailable` без нового consent. HeatMap
создаёт задачу с `LeastPrivilege`; она запускает обычный launcher, а elevation
происходит только через видимый UAC prompt. Старые небезопасные задачи мигрируются
через disable/verification/delete/re-create sequence. XML передаётся Task Scheduler
in-memory, без privileged чтения файла из пользовательского `%TEMP%`. Настраивать
автозапуск нужно той же Windows-учётной записью, которая открыла desktop session;
over-the-shoulder UAC с другими admin credentials отвергается. Checkout path с
символом `%` для Task Scheduler action не поддерживается, потому что `cmd.exe`
выполняет environment expansion. Silent autostart без UAC потребует отдельной
защищённой установки в admin-owned location и не имитируется ACL/hash-проверкой
checkout. Автоматический restart задачи намеренно отключён: для интерактивного UAC
launcher retry policy создавала бы повторяющиеся consent prompts.

## Технологии

- **Python** + **tkinter** — интерфейс
- **LibreHardwareMonitor** — чтение датчиков через .NET interop (pythonnet)
- **psutil** — дополнительные системные метрики
- **Windows API (ctypes)** — встраивание в рабочий стол

## Зачем нужны права администратора?

LibreHardwareMonitor требует прямой доступ к аппаратным датчикам для чтения температур, напряжений и оборотов вентиляторов. Без прав администратора эти данные недоступны.

## Лицензия

Код HeatMap распространяется по MIT license. Состав, версии и лицензии bundled
runtime перечислены в `runtime-lock.json` и `THIRD_PARTY_NOTICES.md`.
