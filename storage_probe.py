"""Bounded sensor probes of disks proven by Windows volume extents."""
import json
import logging
import multiprocessing
from pathlib import Path
import re
import time

from hardware_access_guard import require_hardware_access
from thermal_policy import finite
from storage_identity import disk_properties


PROBE_TIMEOUT_SECONDS = 8
STORAGE_STALE_SECONDS = 45
log = logging.getLogger(__name__)
_PROBE_STAGES = frozenset({"start", "runtime", "properties", "smart", "read", "close"})


def _send_probe_message(pipe, result):
    pipe.send_bytes(json.dumps(dict(result, sample_time=time.monotonic()),
                               allow_nan=False).encode("utf-8"))


def _reply_sample_time(result):
    now = time.monotonic()
    stamp = result.get("sample_time", now)
    if finite(stamp, 0, now) is None:
        raise ValueError("Invalid probe sample time")
    return stamp


def display_name(model):
    return re.sub(
        r"^(Samsung|WDC|Western Digital|Kingston|Crucial|Seagate|Toshiba|SK Hynix|Intel|Micron|SanDisk|ADATA|Corsair)\s*(SSD\s*)?",
        "", model, flags=re.IGNORECASE,
    ).strip() or model


def _native_sample(number, identified=lambda sample: None, progress=lambda stage: None):
    """Use the verified 1.1.1 bundle without its global device enumeration.

    SYNC: the internal constructor is from the pinned DiskInfoToolkit source
    1abae1b8de1a7ec866ffc247bad266cdcda61b5f. Fail closed on runtime/API drift.
    It uses the same identification/SMART policy as LHM on this physical path.
    The bundle may enable SMART when initial attributes are unusable.
    These processes never own fan controllers or modify user files.
    """
    require_hardware_access("monitor")
    progress("runtime")
    from setup import verify_lib_manifest
    ok, messages = verify_lib_manifest()
    if not ok:
        raise RuntimeError("Storage runtime integrity check failed: " + "; ".join(messages))
    # Prefer the driver's standard temperature property. It avoids raw SMART
    # identification on devices whose ATA requests block despite usable Windows
    # temperature data. Unsupported properties fall back to the bundled reader.
    progress("properties")
    properties = disk_properties(number, identified)
    if properties and properties.get("temp") is not None:
        properties["name"] = display_name(properties["name"])
        properties["source"] = "Windows storage temperature property"
        return properties
    progress("runtime")
    import clr
    import System
    from System.Reflection import Assembly, BindingFlags
    root = Path(__file__).resolve().parent
    assembly = Assembly.LoadFrom(str(root / "lib/DiskInfoToolkit.dll"))
    descriptor_type = assembly.GetType("DiskInfoToolkit.Internal.StorageDevice")
    descriptor = System.Activator.CreateInstance(descriptor_type, True)
    for name, value in (("DeviceID", ""), ("HardwareID", ""),
                        ("PhysicalPath", r"\\.\PhysicalDrive" + str(number)),
                        ("DriveNumber", System.Int32(number))):
        descriptor_type.GetProperty(name).SetValue(descriptor, value, None)
    storage_type = assembly.GetType("DiskInfoToolkit.Storage")
    flags = BindingFlags.Instance | BindingFlags.NonPublic
    constructors = [ctor for ctor in storage_type.GetConstructors(flags)
                    if len(ctor.GetParameters()) == 2]
    if len(constructors) != 1:
        raise RuntimeError("Unsupported storage runtime constructor")
    # The preceding Windows query and runtime verification can take seconds.
    # Identification in the constructor already performs native disk I/O.
    require_hardware_access("monitor")
    progress("smart")
    storage = constructors[0].Invoke(System.Array[System.Object](["", descriptor]))
    try:
        if not storage_type.GetProperty("IsValid", flags).GetValue(storage, None):
            raise RuntimeError("Storage identification unavailable")
        require_hardware_access("monitor")
        progress("read")
        storage.Update()
        sample = {"name": display_name(str(storage.Model).strip()), "disk_number": number,
                  "temp": finite(storage.Smart.Temperature, 1, 150)}
        life = finite(storage.Smart.Life, 0, 100)
        if life is not None:
            sample["life_pct"] = life
        # Reuse LHM's conversion of auxiliary NVMe sensors, without StorageGroup
        # or Computer.Open. Warning/critical thresholds are not temperatures.
        clr.AddReference(str(root / "lib/LibreHardwareMonitorLib.dll"))
        from LibreHardwareMonitor.Hardware import Computer, SensorType
        from LibreHardwareMonitor.Hardware.Storage import StorageDevice
        settings = clr.GetClrType(Computer).GetField("_settings", flags).GetValue(Computer())
        hardware = StorageDevice(storage, "nvme" if storage.IsNVMe else "ssd", settings)
        require_hardware_access("monitor")
        hardware.Update()
        temperatures = []
        for sensor in hardware.Sensors:
            name = str(sensor.Name)
            if sensor.SensorType != SensorType.Temperature or any(
                word in name.casefold() for word in ("warning", "critical")
            ):
                continue
            value = finite(sensor.Value, 1, 150)
            if value is not None:
                temperatures.append({"name": name, "temp": value})
        if len(temperatures) > 1:
            sample["temperatures"] = temperatures
            sample["aux_temp"] = max(item["temp"] for item in temperatures)
        sample["temp"] = finite(storage.Smart.Temperature, 1, 150)
        sample["source"] = "DiskInfoToolkit SMART"
        return sample
    finally:
        try:
            progress("close")
        finally:
            storage.Dispose()


def _probe_worker(number, pipe):
    def identified(sample):
        sample['name'] = display_name(sample['name'])
        sample['phase'] = 'identified'
        _send_probe_message(pipe, sample)

    def progress(stage):
        _send_probe_message(pipe, {"disk_number": number, "phase": "stage", "stage": stage})

    try:
        try:
            progress("start")
            result = _native_sample(number, identified, progress)
        except Exception as exc:
            result = {"disk_number": number, "error": str(exc)[:500]}
        _send_probe_message(pipe, result)
    finally:
        pipe.close()


def _validated_sample(result, number):
    if not isinstance(result, dict) or type(result.get("disk_number")) is not int or result["disk_number"] != number:
        raise ValueError("Storage probe returned a different disk identity")
    if "error" in result:
        if not isinstance(result["error"], str):
            raise ValueError("Invalid storage probe error")
        sample = {"disk_number": number, "error": result["error"][:500]}
        if isinstance(result.get('name'), str) and 0 < len(result['name']) <= 256:
            sample['name'] = result['name']
        return sample
    name = result.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 256:
        raise ValueError("Invalid storage model")
    temp = finite(result.get("temp"), 1, 150)
    if result.get("temp") is not None and temp is None:
        raise ValueError("Invalid storage temperature")
    sample = {"disk_number": number, "name": name, "temp": round(temp) if temp is not None else None}
    source = result.get("source")
    if source in ("Windows storage temperature property", "DiskInfoToolkit SMART"):
        sample["source"] = source
    life = finite(result.get("life_pct"), 0, 100)
    if life is not None:
        sample["life_pct"] = life
    temperatures = result.get("temperatures", [])
    if not isinstance(temperatures, list) or len(temperatures) > 16:
        raise ValueError("Invalid auxiliary storage temperatures")
    readings = []
    for reading in temperatures:
        if not isinstance(reading, dict) or not isinstance(reading.get("name"), str) or len(reading["name"]) > 128:
            raise ValueError("Invalid auxiliary storage sensor")
        value = finite(reading.get("temp"), 1, 150)
        if value is None:
            raise ValueError("Invalid auxiliary storage temperature")
        readings.append({"name": reading["name"], "temp": round(value)})
    if readings:
        sample["temperatures"] = readings
        sample["aux_temp"] = max(reading["temp"] for reading in readings)
    return sample


def run_bounded_probe(worker, argument, consume, failure, cancelled, timeout):
    """Kill only this disposable storage probe on timeout/shutdown.

    Windows multiprocessing bypasses the venv redirector, so terminating this
    process cannot leave the actual native reader orphaned behind a shim.
    """
    if cancelled():
        return None
    receive = send = process = None
    started = False
    try:
        context = multiprocessing.get_context("spawn")
        receive, send = context.Pipe(duplex=False)
        process = context.Process(target=worker, args=(argument, send), daemon=True)
        process.start()
        started = True
        send.close()
        deadline = time.monotonic() + timeout
        while True:
            now = time.monotonic()
            if cancelled():
                return None
            expired = now >= deadline
            if expired and timeout <= 0:
                break
            ready = receive.poll(0 if expired else min(0.1, max(0, deadline - time.monotonic())))
            if ready:
                result = json.loads(receive.recv_bytes(16384).decode("utf-8"))
                # The owner can be descheduled while a finished child already
                # has its reply queued. Accept only proven timely completion;
                # never extend the native query deadline or refresh sample age.
                stamped = isinstance(result, dict) and "sample_time" in result
                if (expired or stamped) and (not isinstance(result, dict)
                        or finite(result.get("sample_time"), 0, deadline) is None):
                    break
                sample = consume(result)
                if sample is not None:
                    return sample
                # A finished worker can have both identification and final
                # messages queued. Its exit does not invalidate unread replies.
                continue
            if expired:
                break
            if not process.is_alive() and not receive.poll(0):
                break
        return failure("probe timed out or exited without a reading")
    except (OSError, EOFError, ValueError, RuntimeError) as exc:
        return failure("probe failed: " + str(exc)[:300])
    finally:
        for name, pipe in (("receive", receive), ("send", send)):
            if pipe is not None:
                try:
                    pipe.close()
                except OSError:
                    log.warning("Storage probe %s pipe could not close", name, exc_info=True)
        if started:
            process.join(timeout=0.2)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
            if not process.is_alive():
                process.close()
            else:
                # WHY: Windows can delay process exit while a faulty disk
                # finishes kernel I/O, even after TerminateProcess. Do not let
                # multiprocessing's unbounded atexit join hang the overlay.
                # Only this already-terminated probe is removed; its Popen
                # finalizer still closes process handles when it is collected.
                from multiprocessing.process import _children
                _children.discard(process)
        elif process is not None:
            try:
                process.close()
            except (OSError, ValueError):
                log.warning("Storage probe process could not close after failed start", exc_info=True)


def probe_disk(number, cancelled=lambda: False, *, timeout=PROBE_TIMEOUT_SECONDS):
    if type(number) is not int or not 0 <= number <= 0x7fffffff:
        raise ValueError("Invalid physical disk number")
    metadata = {}

    def consume(result):
        if isinstance(result, dict) and result.get("phase") == "stage":
            if (type(result.get("disk_number")) is not int or result["disk_number"] != number
                    or result.get("stage") not in _PROBE_STAGES):
                raise ValueError("Invalid storage probe stage")
            _reply_sample_time(result)
            metadata["probe_stage"] = result["stage"]
            return None
        sample = _validated_sample(result, number)
        if result.get('phase') == 'identified':
            if 'error' in sample:
                raise ValueError("Invalid storage identification message")
            metadata['name'] = sample['name']
            return None
        if "sample_time" in result:
            sample["sample_time"] = _reply_sample_time(result)
        return dict(metadata, **sample)

    def failure(error):
        stage = metadata.get("probe_stage")
        detail = "Storage " + error + (f" (last stage: {stage})" if stage else "")
        return {"disk_number": number, **metadata, "error": detail}

    return run_bounded_probe(
        _probe_worker, number, consume,
        failure,
        cancelled, timeout,
    )


def snapshot_data(snapshot, numbers, now):
    disks, errors = [], []
    for number in sorted(numbers):
        entry = snapshot.get(number)
        if entry is None:
            continue
        stamp, sample = entry
        disk = dict(sample)
        error = disk.pop("error", None)
        if not error and (now - stamp > STORAGE_STALE_SECONDS or now < stamp):
            error = "Storage temperature reading expired"
        if error:
            disk = {"disk_number": number, "name": disk.get("name", f"Disk {number}"), "temp": None}
            errors.append(f"Disk {number}: {error}")
        disks.append(disk)
    return {"disks": disks, "storage_errors": errors,
            "storage_sensor_source": "isolated Windows properties / DiskInfoToolkit SMART"}
