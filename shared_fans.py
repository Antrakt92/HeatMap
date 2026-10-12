"""Verified, opt-in ownership of the entire B550 IT8792E fan group."""
import os
import time
from pathlib import Path

from pawnio_shared import IsaBus, SignedEcBridge
from thermal_policy import finite
from startup_readiness import StartupNotReady
import fan_common

SHARED_NAMES = ('System Fan #4', 'System Fan #5 / Pump', 'System Fan #6 / Pump')
CHIP = '/lpc/it8792e/0'
INDICES = (2, 0, 1)
REGISTERS = (0x13, 0x15, 0x16, 0x17, 0x63, 0x6B, 0x73)
PWM_REGISTERS = (0x63, 0x6B, 0x73)
# Recovery-journal identity. The module pins the exact signed PawnIO bridge
# (IsaBridgeEC.bin from PawnIO.Modules 0.2.10); only a journal written for
# this board through this module may be restored.
SHARED_BOARD = 'B550_AORUS_PRO_AC'
SHARED_MODULE = {'file': 'hardware_modules/IsaBridgeEC.bin', 'version': '0.2.10'}
SHARED_JOURNAL_NAME = 'shared-recovery.json'
SHARED_JOURNAL_PROFILE = 'b550-aorus-pro-ac-shared-takeover'


def _command_registers(baseline, percent):
    command = dict(baseline)
    command.update({r: baseline[r] & 0x7F for r in (0x15, 0x16, 0x17)})
    value = round(percent * 255 / 100)
    command.update({0x63: value, 0x6B: 255, 0x73: value})
    return command


def _restore_conflict(current, mode, baseline, expected, pending):
    if current[0x13] != baseline[0x13]:
        return 'Shared output registers changed; external settings preserved'
    if mode == 1:
        # Firmware may update duty immediately after regaining ownership.
        if any(current[r] != baseline[r] for r in (0x15, 0x16, 0x17)):
            return 'Shared register modes changed while firmware owns outputs; settings preserved'
        return None
    if mode != 0 or any(current[r] not in (baseline[r], expected[r], pending[r])
                         for r in REGISTERS[1:]):
        return 'Shared fan controller settings changed; external settings preserved'
    return None


class SharedRecoveryJournal:
    """Durable takeover evidence for the shared EC fan group.

    Journal format (shared-recovery.json, next to the case fan-status files,
    like the GPU worker's sibling recovery.json)::

        {"profile": "b550-aorus-pro-ac-shared-takeover", "version": 2,
         "board": "B550_AORUS_PRO_AC",
         "module": {"file": "hardware_modules/IsaBridgeEC.bin", "version": "0.2.10"},
         "baseline": {"13": 119, "15": 128, ...},
         "expected": {...}, "pending": {...}}  # two-digit hex register keys

    The journal is written atomically (temp file + fsync + os.replace) before
    every command, mirroring gpu_fans.py RecoveryJournal.before_write. Expected
    and pending states cover native calls that partly succeed before raising.
    A new worker restores only a known unfinished command. Legacy journals have
    no ownership proof and may only be retired after firmware has already
    regained its original register modes. Unknown settings are never overwritten.
    Anything else (missing file excluded) raises with settings preserved and
    keeps the file: kill -9 and kernel hangs during the native calls remain
    uncovered, like on the GPU path.
    """

    def __init__(self, path):
        self.path = Path(path)

    def before_write(self, baseline, expected, pending):
        payload = dict(profile=SHARED_JOURNAL_PROFILE, version=2, board=SHARED_BOARD,
                       module=dict(SHARED_MODULE),
                       **{name: {f'{r:02X}': state[r] for r in REGISTERS}
                          for name, state in (('baseline', baseline), ('expected', expected), ('pending', pending))})
        self._decode(payload)
        temporary = fan_common.stage_json_payload(self.path, payload)
        try:
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self):
        self.path.unlink(missing_ok=True)

    def _decode(self, saved):
        if (not isinstance(saved, dict) or saved.get('profile') != SHARED_JOURNAL_PROFILE
                or type(saved.get('version')) is not int or saved['version'] not in (1, 2)
                or saved.get('board') != SHARED_BOARD or saved.get('module') != SHARED_MODULE
                or not isinstance(saved.get('baseline'), dict)
                or set(saved['baseline']) != {f'{register:02X}' for register in REGISTERS}
                or any(type(value) is not int or not 0 <= value <= 255
                       for value in saved['baseline'].values())):
            raise RuntimeError('Shared recovery journal identity or settings invalid; settings preserved')
        baseline = {r: saved['baseline'][f'{r:02X}'] for r in REGISTERS}
        if baseline[0x13] & 7 != 7:
            raise RuntimeError('Shared recovery baseline output modes invalid; settings preserved')
        if saved['version'] == 1:
            return baseline, None, None
        states = []
        for key in ('expected', 'pending'):
            state = saved.get(key)
            if (not isinstance(state, dict) or set(state) != {f'{r:02X}' for r in REGISTERS}
                    or any(type(v) is not int or not 0 <= v <= 255 for v in state.values())):
                raise RuntimeError('Shared recovery command invalid; settings preserved')
            state = {r: state[f'{r:02X}'] for r in REGISTERS}
            if state != baseline and (state[0x13] != baseline[0x13]
                    or any(state[r] != baseline[r] & 0x7F for r in (0x15, 0x16, 0x17))
                    or state[0x6B] != 255 or state[0x63] != state[0x73]
                    or not round(255 * 60 / 100) <= state[0x63] <= 255):
                raise RuntimeError('Unknown shared recovery command; settings preserved')
            states.append(state)
        return baseline, *states

    def recover(self, backend, bridge, bus=IsaBus):
        def _open_utf8(path):
            return open(path, encoding='utf-8')

        try:
            saved = fan_common.load_json_payload(self.path, _open_utf8)
        except FileNotFoundError:
            return False
        except (OSError, ValueError) as exc:
            raise RuntimeError(f'Shared recovery journal unreadable; settings preserved: {exc}') from exc
        baseline, expected, pending = self._decode(saved)
        with bus():
            current = {register: backend.read(register) for register in REGISTERS}
            mode = bridge.read_mode()
            if expected is None and (mode != 1 or any(current[r] != baseline[r]
                                                        for r in (0x13, 0x15, 0x16, 0x17))):
                raise RuntimeError('Legacy shared recovery journal lacks ownership proof; settings preserved')
            conflict = _restore_conflict(current, mode, baseline, expected or baseline, pending or baseline)
            if conflict:
                raise RuntimeError(conflict)
            if mode == 1:
                self.clear()
                return False
            errors = []
            try:
                for register in REGISTERS[1:]:
                    try:
                        backend.write(register, baseline[register])
                    except Exception as exc:
                        errors.append(str(exc))
                try:
                    bridge.write_mode(1)
                    if bridge.read_mode() != 1:
                        errors.append('Shared firmware restoration was not confirmed')
                except Exception as exc:
                    errors.append(str(exc))
                try:
                    if {register: backend.read(register) for register in REGISTERS} != baseline:
                        errors.append('Shared output registers were not restored')
                except Exception as exc:
                    errors.append(str(exc))
            except Exception as exc:
                errors.append(str(exc))
            if errors:
                raise RuntimeError('Interrupted shared session restoration unconfirmed: '
                                   + '; '.join(errors))
            self.clear()
            return True


def select_shared_sensors(computer):
    """Validate secondary enumeration without opening another hardware handle."""
    boards = [h for h in computer.Hardware if str(h.HardwareType) == 'Motherboard']
    if not boards:
        raise StartupNotReady('Waiting for motherboard sensors')
    if len(boards) != 1 or str(getattr(getattr(boards[0], '__implementation__', boards[0]), 'Model', '')) != 'B550_AORUS_PRO_AC':
        raise RuntimeError('Shared fan control supports only B550 AORUS PRO AC')
    items = [s for s in boards[0].SubHardware if str(s.Identifier) == CHIP]
    if len(items) > 1:
        raise RuntimeError('Ambiguous secondary fan controller')
    if not items:
        if any(str(sensor.Name) in SHARED_NAMES for sub in boards[0].SubHardware for sensor in sub.Sensors):
            raise RuntimeError('Unexpected shared fan controller identity')
        raise StartupNotReady('Waiting for secondary fan controller')
    hardware = getattr(items[0], '__implementation__', items[0])
    sources = [(str(sub.Identifier), sensor) for sub in boards[0].SubHardware for sensor in sub.Sensors]
    sensors = []
    for name, index in zip(SHARED_NAMES, INDICES):
        pair = []
        for kind in ('Control', 'Fan'):
            expected = f'{CHIP}/{kind.lower()}/{index}'
            found = [(owner, s) for owner, s in sources
                     if str(s.SensorType) == kind and (str(s.Name) == name or str(s.Identifier) == expected)]
            if len(found) > 1:
                raise RuntimeError(f'Ambiguous shared channel: {name} {kind}')
            if not found:
                raise StartupNotReady(f'Waiting for shared channel: {name} {kind}')
            owner, sensor = found[0]
            if (str(sensor.Identifier) != expected or str(sensor.Name) != name
                    or owner != CHIP):
                raise RuntimeError(f'Unexpected shared channel identity: {name} {kind}')
            if kind == 'Control' and sensor.Control is None:
                raise RuntimeError(f'Shared fan control object unavailable: {name}')
            if kind == 'Fan' and sensor.Value is None:
                raise StartupNotReady(f'Waiting for shared channel reading: {name} {kind}')
            minimum, maximum = (0, 100) if kind == 'Control' else (0 if name == SHARED_NAMES[2] else 200, 10000)
            if sensor.Value is not None and (isinstance(sensor.Value, (bool, str))
                                             or finite(float(sensor.Value), minimum, maximum) is None):
                raise RuntimeError(f'Invalid or stopped shared channel: {name} {kind}')
            if kind == 'Fan' and name == SHARED_NAMES[2] and float(sensor.Value) != 0:
                raise RuntimeError('SYS6 is connected; the four-fan profile does not apply')
            pair.append(sensor)
        sensors.append((name, *pair))
    return hardware, sensors


def make_shared_computer():
    from LibreHardwareMonitor.Hardware import Computer, ISettings

    class StartupSettings(ISettings):
        __namespace__ = 'HeatMap.SharedFanStartup'

        def Contains(self, name):
            return name in self.keys

        def GetValue(self, name, default):
            return '100' if name in self.keys else default

        def SetValue(self, name, value):
            pass

        def Remove(self, name):
            pass

        keys = frozenset(f'/lpc/it8688e/0/control/{index}/control/value' for index in (1, 2))

    # Prime the independent Control objects without selecting Software mode.
    # Their first explicit SetSoftware then cannot send the default zero value.
    return Computer(StartupSettings())


class LhmSecondary:
    """Narrow adapter to the exact manifest-pinned LHM 0.9.5 register methods.

    The old SetSoftware/SetDefault path invokes an older MMIO module and caches
    EC enable state. These two register methods do neither. Never replace its
    private fields or call its shared-controller Enable/Restore methods.
    """
    def __init__(self, computer):
        from System import Array, Object, Byte, Boolean
        from System.Reflection import BindingFlags
        self.Array, self.Object, self.Byte, self.Boolean = Array, Object, Byte, Boolean
        self.hardware, self.sensors = select_shared_sensors(computer)
        flags = BindingFlags.Instance | BindingFlags.NonPublic
        field = self.hardware.GetType().GetField('_superIO', flags)
        if field is None:
            raise RuntimeError('Unsupported LHM secondary adapter')
        self.sio = field.GetValue(self.hardware)
        if str(self.sio.GetType().FullName) != 'LibreHardwareMonitor.Hardware.Motherboard.Lpc.IT87XX':
            raise RuntimeError('Unsupported secondary register backend')
        self.read_method = self.sio.GetType().GetMethod('ReadByte', flags)
        self.write_method = self.sio.GetType().GetMethod('WriteByte', flags)
        if self.read_method is None or self.write_method is None:
            raise RuntimeError('Pinned secondary register interface is unavailable')

    def read(self, register):
        if register not in REGISTERS:
            raise ValueError('Not a shared-fan register')
        args = self.Array[self.Object]([self.Byte(register), self.Boolean(False)])
        value = int(self.read_method.Invoke(self.sio, args))
        if not bool(args[1]):
            raise RuntimeError(f'Unreadable shared-fan register {register:02X}')
        return value

    def write(self, register, value):
        # Output enable and unrelated registers are never written by HeatMap.
        if register not in REGISTERS[1:] or type(value) is not int or not 0 <= value <= 255:
            raise ValueError('Not a supported shared-fan register write')
        self.write_method.Invoke(self.sio, self.Array[self.Object]([self.Byte(register), self.Byte(value)]))
        if self.read(register) != value:
            raise RuntimeError(f'Shared-fan register {register:02X} write was not confirmed')

    def readings(self):
        return [dict(name=name, rpm=finite(float(tach.Value), 0, 10000) if tach.Value is not None else None,
                     control_pct=finite(float(sensor.Value), 0, 100) if sensor.Value is not None else None)
                for name, sensor, tach in self.sensors]


class SharedFanSession:
    def __init__(self, backend, bridge, bus=IsaBus, sleep=time.sleep, journal=None):
        self.backend, self.bridge, self.bus, self.sleep = backend, bridge, bus, sleep
        self.journal = journal
        self.baseline = None
        self.touched = False
        self.restored = False
        self.expected = self.pending = None

    def prepare(self):
        with self.bus():
            if self.bridge.read_mode() != 1:
                raise RuntimeError('Shared fans are already outside motherboard automatic control')
            registers = {r: self.backend.read(r) for r in REGISTERS}
            if registers[0x13] & 7 != 7:
                raise RuntimeError('Shared-fan output modes are unsupported')
            for fan in self.backend.readings():
                minimum = 0 if fan['name'] == SHARED_NAMES[2] else 200
                if finite(fan['rpm'], minimum, 10000) is None:
                    raise RuntimeError(f"Shared fan tachometer is unavailable: {fan['name']}")
            # This profile is for the user's four fans; refuse an extra device
            # instead of silently applying a fixed command to an unknown fifth fan.
            if self.backend.readings()[2]['rpm'] != 0:
                raise RuntimeError('SYS6 is connected; the four-fan profile does not apply')
            self.baseline = registers

    def apply(self, percent):
        if finite(percent, 60, 100) is None or self.baseline is None or self.restored:
            raise ValueError('Shared fan command requires a prepared session and 60..100%')
        value = round(percent * 255 / 100)
        with self.bus():
            if not self.touched:
                if self.bridge.read_mode() != 1:
                    raise RuntimeError('Shared fan ownership changed before activation')
                # Capture dynamic duty immediately before stopping the EC.
                self.baseline = {r: self.backend.read(r) for r in REGISTERS}
                if self.baseline[0x13] & 7 != 7:
                    raise RuntimeError('Shared-fan output modes changed before activation')
                self.expected = dict(self.baseline)
            else:
                if self.bridge.read_mode() != 0:
                    raise RuntimeError('Shared fan ownership was lost')
                self._check_modes()
            pending = _command_registers(self.baseline, percent)
            if self.journal is not None:
                self.journal.before_write(self.baseline, self.expected, pending)
            self.pending = pending  # Durable intent covers each partial write.
            if not self.touched:
                self.touched = True  # A failed native call may already have written.
                self.bridge.write_mode(0)
                self.sleep(0.5)
                if self.bridge.read_mode() != 0:
                    raise RuntimeError('Motherboard still owns shared fan outputs')
                # Prime duty before clearing automatic bits; never issue a zero
                # command from the LHM Control object's initial SoftwareValue.
                for register in PWM_REGISTERS:
                    self.backend.write(register, 255 if register == 0x6B else value)
                for register in (0x15, 0x16, 0x17):
                    self.backend.write(register, self.baseline[register] & 0x7F)
            self.backend.write(0x63, value)  # SYS5
            self.backend.write(0x6B, 255)    # Unused SYS6; do not leave a stopped output.
            self.backend.write(0x73, value)  # SYS4
            if {r: self.backend.read(r) for r in REGISTERS} != pending:
                raise RuntimeError('Shared fan command readback mismatch')
            self.expected = pending

    def check(self):
        with self.bus():
            if self.touched:
                if self.bridge.read_mode() != 0:
                    raise RuntimeError('Shared fan controller ownership changed')
                self._check_modes()

    def _check_modes(self):
        if {r: self.backend.read(r) for r in REGISTERS} != self.expected:
            raise RuntimeError('Shared fan output, duty or manual mode changed')

    def restore(self):
        if not self.touched or self.restored:
            return []
        errors = []
        try:
            with self.bus():
                current = {r: self.backend.read(r) for r in REGISTERS}
                mode = self.bridge.read_mode()
                conflict = _restore_conflict(current, mode, self.baseline,
                                              self.expected, self.pending)
                if conflict:
                    return [conflict]
                # If a partial takeover failed before EC disable, leave firmware
                # alone; do not fight a live automatic controller with duty writes.
                wrote_pwm = False
                try:
                    if mode == 0:
                        for register in REGISTERS[1:]:
                            try:
                                self.backend.write(register, self.baseline[register])
                            except Exception as exc:
                                errors.append(str(exc))
                        wrote_pwm = True
                except Exception as exc:
                    errors.append(str(exc))
                # Attempt the known original EC mode even if an earlier read or
                # register restore failed. Keep that failure as unconfirmed proof.
                try:
                    if mode == 0:
                        self.bridge.write_mode(1)
                        self.sleep(0.5)
                    if self.bridge.read_mode() != 1:
                        errors.append('Shared firmware restoration was not confirmed')
                except Exception as exc:
                    errors.append(str(exc))
                if wrote_pwm:
                    # Duties we wrote must read back; an unverified PWM leaves
                    # the header at an unknown speed despite a clean mode check.
                    try:
                        mismatched = [f'{register:#04x}' for register in PWM_REGISTERS
                                      if self.backend.read(register) != self.baseline[register]]
                        if mismatched:
                            errors.append('Shared PWM duties were not restored: ' + ', '.join(mismatched))
                    except Exception as exc:
                        errors.append(str(exc))
                if self.backend.read(0x13) != self.baseline[0x13]:
                    errors.append('Shared output bits were not restored')
        except Exception as exc:
            errors.append(str(exc))
        if not errors:
            self.restored = True
            if self.touched and self.journal is not None:
                # Verified restoration retires the takeover evidence; a failed
                # restore keeps it for the next worker start.
                self.journal.clear()
        return errors

    def close(self):
        self.bridge.close()


def open_shared_session(computer, journal=None):
    backend = LhmSecondary(computer)
    with IsaBus():
        bridge = SignedEcBridge()
    session = SharedFanSession(backend, bridge, journal=journal)
    try:
        session.prepare()
        return session
    except Exception:
        bridge.close()
        raise


def recover_shared_journal(computer, journal, bus=IsaBus):
    """Restore an interrupted shared takeover before any new one.

    Returns True when a journal was restored, False when no journal exists.
    Any other outcome raises with settings preserved and keeps the journal.
    """
    backend = LhmSecondary(computer)
    with bus():
        bridge = SignedEcBridge()
    try:
        return journal.recover(backend, bridge, bus=bus)
    finally:
        bridge.close()
