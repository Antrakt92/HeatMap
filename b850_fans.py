"""Exact B850 SYS1/2/3 ownership and CPU/GPU airflow assistance.

Register layout: manifest-pinned LHM 0.9.5 IT87XX.cs. No EC takeover,
PWM/DC switching, CPU outputs or unused headers are written.
"""
import math
import os
from pathlib import Path
import re
import threading
import time

import psutil

import fan_common
from gpu_fans import (ASSIST_ON, demand as gpu_demand,
                      emergency_cooling as gpu_emergency_cooling, recovery_journal_exists)
from hardware_access_guard import require_hardware_access
from pawnio_shared import IsaBus
from startup_readiness import StartupNotReady, StartupCancelled, wait_for_readiness
from thermal_policy import FanRamp, finite, interpolate, TACH_PERSISTENCE_SECONDS

PROFILE = "b850-aorus-elite-wifi7-ice-case-123"
BOARD = "Gigabyte B850 AORUS ELITE WIFI7 ICE"
CHIP = "/lpc/it8696e/0"
TARGETS = tuple(f"System Fan #{n}" for n in (1, 2, 3))
MODES = (0x15, 0x16, 0x17, 0x7F, 0xA7, 0xAF)
DUTIES = (0x63, 0x6B, 0x73, 0x7B, 0xA3, 0xAB)
REGISTERS = (0x13, *MODES, *DUTIES)
TARGET_REGISTERS = tuple(r for i in (1, 2, 3) for r in (MODES[i], DUTIES[i]))
MIN_DUTY = 60


def _same_owned_registers(current, expected):
    # CPU/CPU OPT and unused automatic duties may change on every sensor poll.
    return (current[0x13] & 6 == expected[0x13] & 6
            and all(current[r] == expected[r] for r in TARGET_REGISTERS))


def _gpu_tachs(data):
    entries = data.get("gpu_fans")
    if entries is None or entries == []:
        return [("primary", data.get("gpu_fan"))]
    if not isinstance(entries, list) or any(not isinstance(row, dict) for row in entries):
        return None
    readings = [(str(row.get("id") or row.get("name") or index), row.get("rpm"))
                for index, row in enumerate(entries)]
    if len({name for name, _rpm in readings}) != len(readings):
        return None
    return readings


def _source_identity(data):
    if not isinstance(data, dict):
        raise RuntimeError("Invalid B850 CPU/GPU snapshot")
    if (data.get("cpu_fan_id") != f"{CHIP}/fan/0"
            or data.get("cpu_optional_fan_id") != f"{CHIP}/fan/4"
            or data.get("gpu_name") != "AMD Radeon RX 7900 XT"
            or ("gpu_name_match_count" in data and
                (type(data["gpu_name_match_count"]) is not int or data["gpu_name_match_count"] != 1))):
        raise RuntimeError("CPU/GPU reference identity changed; assistance stopped")
    return data.get("gpu_id"), data["gpu_name"]


def _initial_command(readings, reference):
    # Automatic mode retains the last manual duty byte. It is not the current
    # firmware output; use the commissioned tach fraction as a bounded estimate.
    if (not isinstance(reference, dict) or set(reference) != set(TARGETS)
            or any(finite(value, 200, 10000) is None for value in reference.values())):
        raise RuntimeError("B850 fans need explicit commissioning before automatic control")
    fractions = [100 * row["rpm"] / reference[row["name"]] for row in readings]
    return min(100, max(MIN_DUTY, math.ceil(max(fractions))))


class GpuTachFeedback:
    def __init__(self):
        self.running = set()
        self.stopped_since = {}

    def update(self, data, now):
        readings = _gpu_tachs(data)
        if readings is None:
            return "GPU tachometer feedback invalid; full airflow"
        present = {name: finite(rpm, 0, 10000) for name, rpm in readings}
        self.running.update(name for name, rpm in present.items() if rpm is not None and rpm > 0)
        hot = any((finite(data.get(key), 1, 150) or 0) >= threshold
                  for key, threshold in ASSIST_ON.items())
        if not hot:
            self.stopped_since.clear()
            return None
        for name in self.running:
            rpm = present.get(name)
            if rpm is None:
                return "Previously running GPU tachometer feedback missing; full airflow"
            if rpm == 0:
                start = self.stopped_since.setdefault(name, now)
                if now - start >= TACH_PERSISTENCE_SECONDS:
                    return "Previously running GPU fan stopped; full airflow"
            else:
                self.stopped_since.pop(name, None)
        return None


def synchronized_demand(data, cpu_reference, gpu_reference):
    """Maximum fan fraction and thermal demand; fractions are not airflow."""
    temperatures = ("cpu_temp", *ASSIST_ON)
    if any(finite(data.get(key), 1, 150) is None for key in temperatures):
        return 100, "Missing temperature; full airflow", {}
    fractions = {}
    gpu_tachs = _gpu_tachs(data)
    if gpu_tachs is None:
        return 100, "Invalid GPU fan feedback; full airflow", {}
    for component, channels, reference in (
            ("CPU", [(data.get(key), data.get(key + "_pct"))
                     for key in ("cpu_fan", "cpu_optional_fan")], cpu_reference),
            ("GPU", [(rpm, data.get("gpu_fan_pct") if index == 0 else None)
                     for index, (_name, rpm) in enumerate(gpu_tachs)], gpu_reference)):
        values = []
        for rpm_value, duty_value in channels:
            rpm = finite(rpm_value, 0, 10000)
            # Duty describes the requested speed, not whether the cooler turns.
            if rpm is None:
                return 100, f"Missing {component} fan feedback; full airflow", {}
            if component == "CPU" and rpm == 0:
                return 100, "CPU fan stopped; full airflow", {}
            pct = finite(duty_value, 0, 100)
            if pct is not None:
                values.append((pct, "controller"))
                continue
            if rpm is None or finite(reference, 200, 10000) is None:
                return 100, f"Missing {component} fan reference/feedback; full airflow", {}
            values.append((min(100, 100 * rpm / reference), "RPM estimate"))
        fraction, source = max(values, key=lambda item: item[0])
        fractions[component] = dict(percent=math.ceil(fraction), source=source)
    demands = {component + " fans": value["percent"] for component, value in fractions.items()}
    if data["cpu_temp"] >= 70:
        demands["CPU temperature"] = math.ceil(interpolate(data["cpu_temp"], ((70, 85), (80, 100))))
    if any(data[key] >= threshold for key, threshold in ASSIST_ON.items()):
        target, sensor = gpu_demand(data)
        demands["GPU " + sensor + " temperature"] = target
    reason = max(demands, key=demands.__getitem__)
    return max(MIN_DUTY, demands[reason]), reason, fractions


def controller(computer):
    boards = [hw for hw in computer.Hardware if str(hw.HardwareType) == "Motherboard"]
    if not boards:
        raise StartupNotReady("Waiting for B850 motherboard")
    if len(boards) != 1 or str(boards[0].Name) != BOARD:
        raise RuntimeError("B850 SYS1/2/3 profile motherboard identity mismatch")
    chips = [hw for hw in boards[0].SubHardware if str(hw.Identifier) == CHIP]
    if len(chips) != 1:
        raise RuntimeError("B850 IT8696E controller is missing or ambiguous")
    return getattr(chips[0], "__implementation__", chips[0])


def select_tachs(computer, *, startup=False, require_running=True):
    chip = controller(computer)
    selected = []
    for index, name in zip((1, 2, 3), TARGETS):
        matches = [s for s in chip.Sensors if str(s.SensorType) == "Fan"
                   and str(s.Identifier) == f"{CHIP}/fan/{index}"]
        if len(matches) != 1 or str(matches[0].Name) != f"Fan #{index + 1}":
            raise RuntimeError(f"{name}: tachometer identity missing or ambiguous")
        if not require_running:
            # Interrupted register ownership must be returned even when its
            # tachometer cannot prove rotation. Identity checks still apply.
            selected.append(matches[0])
            continue
        if matches[0].Value is None:
            raise StartupNotReady(f"Waiting for {name} tachometer")
        value = matches[0].Value
        if isinstance(value, (bool, str)) or finite(float(value), 0, 10000) is None:
            raise RuntimeError(f"{name}: invalid tachometer reading")
        if float(value) < 200:
            if startup:
                raise StartupNotReady(f"Waiting for {name} running tachometer")
            raise RuntimeError(f"{name}: running tachometer required")
        selected.append(matches[0])
    return chip, selected


class RegisterBackend:
    """Narrow pinned native adapter, with the same ISA mutex as LHM."""
    def __init__(self, computer, *, require_running=True):
        from System import Array, Object, Byte, Boolean
        from System.Reflection import BindingFlags
        self.Array, self.Object, self.Byte, self.Boolean = Array, Object, Byte, Boolean
        self.chip, self.tachs = select_tachs(computer, require_running=require_running)
        report = str(self.chip.GetReport())
        if (len(re.findall(r"^Chip ID: 0x8696\s*$", report, re.M)) != 1
                or "Base Address: 0x0A40" not in report):
            raise RuntimeError("Unexpected B850 controller report")
        flags = BindingFlags.Instance | BindingFlags.NonPublic
        field = self.chip.GetType().GetField("_superIO", flags)
        if field is None:
            raise RuntimeError("Pinned B850 backend unavailable")
        self.sio = field.GetValue(self.chip)
        if str(self.sio.GetType().FullName) != "LibreHardwareMonitor.Hardware.Motherboard.Lpc.IT87XX":
            raise RuntimeError("Unexpected B850 native backend")
        ec = self.sio.GetType().GetField("_gigabyteController", flags)
        if ec is None or ec.GetValue(self.sio) is not None:
            raise RuntimeError("Shared EC takeover is forbidden for the B850 profile")
        self.read_method = self.sio.GetType().GetMethod("ReadByte", flags)
        self.write_method = self.sio.GetType().GetMethod("WriteByte", flags)
        if self.read_method is None or self.write_method is None:
            raise RuntimeError("Pinned B850 register interface unavailable")

    def read(self, register):
        if register not in REGISTERS:
            raise ValueError("Not a B850 fan register")
        args = self.Array[self.Object]([self.Byte(register), self.Boolean(False)])
        value = int(self.read_method.Invoke(self.sio, args))
        if not bool(args[1]):
            raise RuntimeError("Unreadable B850 register")
        return value

    def write(self, register, value):
        if register not in TARGET_REGISTERS or type(value) is not int or not 0 <= value <= 255:
            raise ValueError("Only SYS1/2/3 mode/duty registers may be written")
        self.write_method.Invoke(self.sio, self.Array[self.Object]([self.Byte(register), self.Byte(value)]))

    def snapshot(self):
        return {r: self.read(r) for r in REGISTERS}

    def readings(self):
        return [dict(name=name, rpm=finite(float(tach.Value), 0, 10000) if tach.Value is not None else None,
                     control_pct=round(self.read(DUTIES[index]) * 100 / 255))
                for index, name, tach in zip((1, 2, 3), TARGETS, self.tachs)]


class B850Session:
    def __init__(self, backend, journal, bus=IsaBus):
        self.backend, self.journal, self.bus = backend, Path(journal), bus
        self.touched = False
        self.last_command = None
        with bus():
            if recovery_journal_exists(self.journal):
                self.recover()
            self.baseline = backend.snapshot()
            if not _same_owned_registers(self.baseline, backend.snapshot()) or self.baseline[0x13] & 6 != 6:
                raise RuntimeError("B850 fan configuration is changing or outputs are disabled")
            if any(self.baseline[MODES[i]] & 0x80 == 0 for i in (1, 2, 3)):
                raise RuntimeError("SYS1/2/3 are already manually owned; use motherboard automatic mode first")
        self.expected = dict(self.baseline)

    def save(self, next_state):
        payload = dict(profile=PROFILE, version=1, board=BOARD,
                       baseline={str(k): v for k, v in self.baseline.items()},
                       expected={str(k): v for k, v in self.expected.items()},
                       pending={str(k): v for k, v in next_state.items()})
        temporary = fan_common.stage_json_payload(self.journal, payload)
        try:
            os.replace(temporary, self.journal)
        finally:
            temporary.unlink(missing_ok=True)

    def recover(self):
        payload = fan_common.load_json_payload(self.journal, lambda p: open(p, encoding="utf-8"))
        if (not isinstance(payload, dict) or payload.get("profile") != PROFILE
                or payload.get("board") != BOARD or type(payload.get("version")) is not int
                or payload["version"] != 1):
            raise RuntimeError("B850 recovery journal identity mismatch; settings preserved")
        states = []
        for key in ("baseline", "expected", "pending"):
            values = payload.get(key)
            if (not isinstance(values, dict) or set(values) != {str(r) for r in REGISTERS}
                    or any(type(v) is not int or not 0 <= v <= 255 for v in values.values())):
                raise RuntimeError("Invalid B850 recovery registers; settings preserved")
            states.append({int(r): v for r, v in values.items()})
        baseline, expected, pending = states
        if (baseline[0x13] & 6 != 6 or any(baseline[MODES[i]] & 0x80 == 0 for i in (1, 2, 3))
                or any(state[r] != baseline[r] for state in (expected, pending)
                       for r in REGISTERS if r not in TARGET_REGISTERS)):
            raise RuntimeError("Unsafe B850 recovery baseline; settings preserved")
        for state in (expected, pending):
            if state == baseline:
                continue
            if (any(state[MODES[i]] != baseline[MODES[i]] & 0x7F for i in (1, 2, 3))
                    or len({state[DUTIES[i]] for i in (1, 2, 3)}) != 1
                    or not round(255 * MIN_DUTY / 100) <= state[DUTIES[1]] <= 255):
                raise RuntimeError("Unknown B850 recovery command; settings preserved")
        current = self.backend.snapshot()
        if (current[0x13] & 6 != baseline[0x13] & 6
                or any(current[r] not in (baseline[r], expected[r], pending[r]) for r in TARGET_REGISTERS)):
            raise RuntimeError("B850 external settings conflict; recovery preserved")
        restored = dict(current)
        restored.update({r: baseline[r] for r in TARGET_REGISTERS})
        errors = self._restore_registers(baseline)
        if errors:
            raise RuntimeError("B850 recovery unconfirmed: " + "; ".join(errors))
        if not _same_owned_registers(self.backend.snapshot(), restored):
            raise RuntimeError("B850 recovery readback failed")
        self.journal.unlink()

    def _restore_registers(self, baseline, indices=(1, 2, 3)):
        errors = []
        for i in indices:
            try:
                self.backend.write(MODES[i], baseline[MODES[i]])
            except Exception as exc:
                errors.append(f"{TARGETS[i - 1]} automatic mode: {exc}")
            try:
                if self.backend.snapshot()[MODES[i]] != baseline[MODES[i]]:
                    errors.append(f"{TARGETS[i - 1]} automatic mode restoration unconfirmed")
                    continue
                # Confirm handback first: the retained manual baseline may be
                # much slower than the still-active HeatMap cooling command.
                self.backend.write(DUTIES[i], baseline[DUTIES[i]])
            except Exception as exc:
                errors.append(f"{TARGETS[i - 1]} retained duty: {exc}")
        return errors

    def apply(self, percent):
        if finite(percent, MIN_DUTY, 100) is None:
            raise ValueError("Invalid B850 case command")
        with self.bus():
            if not _same_owned_registers(self.backend.snapshot(), self.expected):
                raise RuntimeError("B850 external settings changed; assistance stopped")
            pending = dict(self.expected)
            for i in (1, 2, 3):
                pending[MODES[i]] = self.baseline[MODES[i]] & 0x7F
                pending[DUTIES[i]] = round(255 * percent / 100)
            self.save(pending)  # Durable intent also covers a partially completed write.
            self.touched = True
            try:
                for i in (1, 2, 3):
                    self.backend.write(DUTIES[i], pending[DUTIES[i]])
                    self.backend.write(MODES[i], pending[MODES[i]])
            finally:
                # Keep intent for restoration even when a native write raises.
                self.pending = pending
            if not _same_owned_registers(self.backend.snapshot(), pending):
                raise RuntimeError("B850 fan command readback mismatch")
            self.expected = pending
            self.last_command = percent

    def check(self):
        with self.bus():
            if not _same_owned_registers(self.backend.snapshot(), self.expected):
                raise RuntimeError("B850 external settings changed; assistance stopped")

    def restore(self):
        if not self.touched:
            return []
        try:
            with self.bus():
                current = self.backend.snapshot()
                pending = getattr(self, "pending", self.expected)
                if current[0x13] & 6 != self.baseline[0x13] & 6:
                    return ["External B850 output modes changed; settings and recovery journal preserved"]
                restored = dict(current)
                errors = []
                eligible = []
                for i, name in zip((1, 2, 3), TARGETS):
                    registers = (DUTIES[i], MODES[i])
                    if any(current[r] not in (self.baseline[r], self.expected[r], pending[r]) for r in registers):
                        errors.append(f"{name}: external settings preserved; restoration unconfirmed")
                        continue
                    eligible.append(i)
                    restored.update({r: self.baseline[r] for r in registers})
                errors.extend(self._restore_registers(self.baseline, eligible))
                if not _same_owned_registers(self.backend.snapshot(), restored):
                    errors.append("B850 motherboard control restoration readback mismatch")
                if errors:
                    return errors
                self.journal.unlink(missing_ok=True)
                self.touched = False
                return []
        except Exception as exc:
            return [f"B850 restore: {exc}"]


def validate_status(status):
    if status.get("controlled_channels") not in (list(TARGETS), []):
        return "Invalid B850 case channels"
    if status.get("firmware_channels") != []:
        return "Invalid B850 firmware channels"
    if status.get("state") == "active" and finite(status.get("command_pct"), MIN_DUTY, 100) is None:
        return "Invalid B850 case command"
    if status.get("state") == "active" and status.get("controlled_channels") != list(TARGETS):
        return "Missing B850 active channels"
    fractions = status.get("fractions", {})
    if (not isinstance(fractions, dict) or any(
            name not in ("CPU", "GPU") or not isinstance(value, dict)
            or finite(value.get("percent"), 0, 100) is None
            or value.get("source") not in ("controller", "RPM estimate")
            for name, value in fractions.items())):
        return "Invalid B850 fan fraction report"
    return None


def worker(path, owner_pid, owner_created, full_rpm=None, commission=False):
    import overlay
    from case_fans import write_status, write_terminal_status
    from shared_fans import make_shared_computer
    if not overlay._is_admin() or overlay._runtime_dll_errors():
        raise RuntimeError("Verified runtime and administrator access are required")
    import clr
    clr.AddReference(os.path.join(overlay.LIB_DIR, "LibreHardwareMonitorLib.dll"))
    computer = make_shared_computer()
    computer.IsCpuEnabled = computer.IsGpuEnabled = computer.IsMotherboardEnabled = True
    owner = psutil.Process(owner_pid)
    if abs(owner.create_time() - owner_created) > .01:
        raise RuntimeError("B850 owner identity changed")
    stop = threading.Event()
    heartbeat = fan_common.OwnerHeartbeat(stop)
    threading.Thread(target=heartbeat.listen, daemon=True).start()
    session = None
    baseline = []
    error = None
    stop_cause = None
    attempted = False
    verified = None
    journal_path = Path(path).parent / "b850-recovery.json"
    config, config_error = overlay.load_config_result()
    if config_error:
        raise RuntimeError(config_error)

    def guard():
        fan_common.check_guarded(stop, owner, heartbeat,
                                lambda: require_hardware_access("independent_case_control"),
                                "Overlay stopped", "Case fan owner heartbeat expired")

    def publish(state, **details):
        write_status(path, state, profile=PROFILE, **details)

    try:
        guard()
        computer.Open()
        if recovery_journal_exists(journal_path):
            # Restore old ownership before readiness requires healthy tachs or
            # CPU/GPU temperatures; a crashed command may have lost either.
            guard()
            attempted = True  # The prior worker's unfinished ownership is known.
            publish("checking", phase="recovering", reason="Restoring interrupted SYS1/2/3 ownership",
                    baseline=[], controlled_channels=list(TARGETS), firmware_channels=[],
                    control_attempted=True, restore_confirmed=False, recovery_pending=True)
            recovery_backend = RegisterBackend(computer, require_running=False)
            guard()
            session = B850Session(recovery_backend, journal_path)
            guard()
        ready_samples = 0
        source_identity = None
        gpu_feedback = GpuTachFeedback()
        def ready():
            nonlocal ready_samples, source_identity
            data = overlay.read_sensors(computer, update_storage=False)
            if not isinstance(data, dict):
                raise RuntimeError("Invalid B850 startup snapshot; no fan commands sent")
            try:
                chip, tachs = select_tachs(computer, startup=True)
            except StartupNotReady:
                ready_samples = 0
                raise
            if any(data.get(key) is None for key in ("cpu_temp", *ASSIST_ON, "cpu_fan", "cpu_optional_fan", "gpu_fan")):
                ready_samples = 0
                raise StartupNotReady("Waiting for CPU/GPU fan and temperature feedback")
            if any(finite(data[key], 1, 150) is None for key in ("cpu_temp", *ASSIST_ON)):
                raise RuntimeError("Invalid B850 startup temperature; no fan commands sent")
            identity = _source_identity(data)
            if source_identity is not None and source_identity != identity:
                raise RuntimeError("CPU/GPU reference identity changed during startup; no fan commands sent")
            source_identity = identity
            gpu_feedback.update(data, time.monotonic())
            ready_samples += 1
            if ready_samples < 2:
                raise StartupNotReady("Verifying a second running SYS1/2/3 sample")
            return chip, tachs
        wait_for_readiness(ready, stop, guard,
            lambda reason, *_args: publish("checking", phase="waiting", reason=str(reason),
                baseline=[], controlled_channels=list(TARGETS) if attempted else [], firmware_channels=[],
                control_attempted=attempted, restore_confirmed=bool(session),
                recovery_pending=recovery_journal_exists(journal_path)))
        backend = RegisterBackend(computer)
        session = B850Session(backend, journal_path)
        baseline = [dict(name=name, rpm=float(tach.Value), control_pct=None)
                    for name, tach in zip(TARGETS, backend.tachs)]
        ramp = FanRamp(value=MIN_DUTY if commission else _initial_command(baseline, full_rpm))
        started = time.monotonic()
        calibrated = False
        minimum_since = None
        while not stop.is_set():
            guard()
            data = overlay.read_sensors(computer, update_storage=False)
            now = time.monotonic()
            guard()
            if _source_identity(data) != source_identity:
                raise RuntimeError("CPU/GPU reference identity changed; assistance stopped")
            # LHM exposes no per-sensor generation. Identical valid readings do
            # not prove a frozen source; unrelated tach changes cannot prove it
            # fresh either. Native read errors and the post-read owner guard
            # remain authoritative, with missing feedback handled below.
            demand, reason, fractions = synchronized_demand(
                data, config.get("cpu_fan_reference_rpm"), config.get("gpu_fan_max_rpm"))
            readings = [dict(name=name, rpm=finite(float(tach.Value), 0, 10000) if tach.Value is not None else None)
                        for name, tach in zip(TARGETS, backend.tachs)]
            if any(finite(row["rpm"], 200, 10000) is None for row in readings):
                raise RuntimeError("B850 SYS1/2/3 tachometer lost or stopped")
            if commission and now - started < 16:
                demand, reason = 100, "Explicit full-speed response check"
            elif commission:
                if not calibrated:
                    if any(row["rpm"] < before["rpm"] * 1.08 for row, before in zip(readings, baseline)):
                        raise RuntimeError("SYS1/2/3 did not confirm a full-speed RPM increase")
                    verified = {row["name"]: row["rpm"] for row in readings}
                    calibrated = True
                demand, reason = MIN_DUTY, "Explicit minimum-duty response check"
            elif (not isinstance(full_rpm, dict) or set(full_rpm) != set(TARGETS)
                  or any(finite(value, 200, 10000) is None for value in full_rpm.values())):
                raise RuntimeError("B850 fans need explicit commissioning before automatic control")
            feedback_failure = gpu_feedback.update(data, now)
            if feedback_failure is not None:
                demand, reason = 100, feedback_failure
            # Commissioning checks both endpoints directly; normal falls are bounded.
            emergency = (not fractions or (finite(data.get("cpu_temp"), 1, 150) or 0) >= 80
                         or gpu_emergency_cooling(data)
                         or feedback_failure is not None)
            if commission and calibrated and emergency:
                raise RuntimeError("Minimum-duty commissioning stopped: thermal/fan protection requires full airflow")
            command = demand if commission else ramp.update(demand, now, emergency=emergency)
            session.check()
            if command != session.last_command:
                guard()
                attempted = True
                session.apply(command)
            with IsaBus():
                feedback = backend.readings()
            guard()
            if any(abs(row["control_pct"] - command) > 1 for row in feedback):
                raise RuntimeError("B850 command feedback mismatch")
            minimum_verified = False
            if commission and command == MIN_DUTY:
                minimum_since = now if minimum_since is None else minimum_since
                if now - minimum_since >= 6:
                    if any(finite(row["rpm"], 200, 10000) is None
                           or row["rpm"] > verified[row["name"]] * .95 for row in feedback):
                        raise RuntimeError("SYS1/2/3 did not confirm the minimum-duty RPM decrease")
                    minimum_verified = True
            publish("active" if not commission or minimum_verified else "checking",
                    reason=reason, command_pct=command, demand_pct=demand, fans=feedback,
                    fractions=fractions, baseline=baseline, controlled_channels=list(TARGETS),
                    firmware_channels=[], control_attempted=attempted, verified_full_rpm=verified,
                    temperatures={key: data.get(key) for key in ("cpu_temp", *ASSIST_ON)})
            stop.wait(2)
    except fan_common.OwnerHeartbeatExpired as exc:
        error, stop_cause = str(exc), "heartbeat_expired"
    except StartupCancelled:
        pass
    except Exception as exc:
        error = str(exc)
    finally:
        restore_errors = fan_common.restore_with_retry(session.restore) if session else []
        try:
            computer.Close()
        except Exception as exc:
            restore_errors.append(f"Close: {exc}")
        try:
            recovery_pending = recovery_journal_exists(journal_path)
        except OSError as exc:
            recovery_pending = None
            restore_errors.append(f"Recovery journal inspection: {exc}")
        publication_error = write_terminal_status(path, "error" if error or restore_errors else "stopped",
            profile=PROFILE, reason=error or "Returned SYS1/2/3 to motherboard control",
            baseline=baseline, controlled_channels=list(TARGETS) if session or attempted else [], firmware_channels=[],
            control_attempted=attempted, restore_errors=restore_errors,
            restore_confirmed=bool(session and not restore_errors),
            recovery_pending=recovery_pending,
            **({"stop_cause": stop_cause} if stop_cause else {}))
        error = error or publication_error
    return 1 if error or restore_errors else 0
