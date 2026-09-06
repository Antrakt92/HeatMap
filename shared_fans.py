"""Verified, opt-in ownership of the entire B550 IT8792E fan group."""
import time

from pawnio_shared import IsaBus, SignedEcBridge
from thermal_policy import finite

SHARED_NAMES = ('System Fan #4', 'System Fan #5 / Pump', 'System Fan #6 / Pump')
CHIP = '/lpc/it8792e/0'
INDICES = (2, 0, 1)
REGISTERS = (0x13, 0x15, 0x16, 0x17, 0x63, 0x6B, 0x73)
PWM_REGISTERS = (0x63, 0x6B, 0x73)


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
        boards = [h for h in computer.Hardware if str(h.HardwareType) == 'Motherboard']
        if len(boards) != 1 or str(getattr(boards[0], '__implementation__', boards[0]).Model) != 'B550_AORUS_PRO_AC':
            raise RuntimeError('Shared fan control supports only B550 AORUS PRO AC')
        items = [s for s in boards[0].SubHardware if str(s.Identifier) == CHIP]
        if len(items) != 1:
            raise RuntimeError('Missing or ambiguous secondary fan controller')
        self.hardware = getattr(items[0], '__implementation__', items[0])
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
        self.sensors = []
        for name, index in zip(SHARED_NAMES, INDICES):
            pair = []
            for kind in ('Control', 'Fan'):
                found = [s for s in self.hardware.Sensors if str(s.SensorType) == kind
                         and str(s.Identifier) == f'{CHIP}/{kind.lower()}/{index}' and str(s.Name) == name]
                if len(found) != 1:
                    raise RuntimeError(f'Missing or ambiguous shared channel: {name} {kind}')
                pair.append(found[0])
            self.sensors.append((name, *pair))

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
    def __init__(self, backend, bridge, bus=IsaBus, sleep=time.sleep):
        self.backend, self.bridge, self.bus, self.sleep = backend, bridge, bus, sleep
        self.baseline = None
        self.touched = False
        self.restored = False

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
        with self.bus():
            if not self.touched:
                if self.bridge.read_mode() != 1:
                    raise RuntimeError('Shared fan ownership changed before activation')
                # Capture dynamic duty immediately before stopping the EC.
                self.baseline = {r: self.backend.read(r) for r in REGISTERS}
                if self.baseline[0x13] & 7 != 7:
                    raise RuntimeError('Shared-fan output modes changed before activation')
                self.touched = True  # A failed native call may already have written.
                self.bridge.write_mode(0)
                self.sleep(0.5)
                if self.bridge.read_mode() != 0:
                    raise RuntimeError('Motherboard still owns shared fan outputs')
                # Prime duty before clearing automatic bits; never issue a zero
                # command from the LHM Control object's initial SoftwareValue.
                for register in PWM_REGISTERS:
                    self.backend.write(register, 255)
                for register in (0x15, 0x16, 0x17):
                    self.backend.write(register, self.baseline[register] & 0x7F)
            elif self.bridge.read_mode() != 0:
                raise RuntimeError('Shared fan ownership was lost')
            self._check_modes()
            value = round(percent * 255 / 100)
            self.backend.write(0x63, value)  # SYS5
            self.backend.write(0x6B, 255)    # Unused SYS6; do not leave a stopped output.
            self.backend.write(0x73, value)  # SYS4

    def check(self):
        with self.bus():
            if self.touched:
                if self.bridge.read_mode() != 0:
                    raise RuntimeError('Shared fan controller ownership changed')
                self._check_modes()

    def _check_modes(self):
        # Equal PWM feedback does not prove manual control: another controller
        # can restore an automatic mode while duty temporarily remains unchanged.
        if (self.backend.read(0x13) != self.baseline[0x13]
                or any(self.backend.read(r) != self.baseline[r] & 0x7F
                       for r in (0x15, 0x16, 0x17))):
            raise RuntimeError('Shared fan output or manual mode changed')

    def restore(self):
        if not self.touched or self.restored:
            return []
        errors = []
        try:
            with self.bus():
                # If a partial takeover failed before EC disable, leave firmware
                # alone; do not fight a live automatic controller with duty writes.
                try:
                    mode = self.bridge.read_mode()
                    if mode == 0:
                        for register in REGISTERS[1:]:
                            try:
                                self.backend.write(register, self.baseline[register])
                            except Exception as exc:
                                errors.append(str(exc))
                    elif any(self.backend.read(r) != self.baseline[r] for r in (0x15, 0x16, 0x17)):
                        errors.append('Shared register modes changed while firmware owns outputs')
                except Exception as exc:
                    errors.append(str(exc))
                # Attempt the known original EC mode even if an earlier read or
                # register restore failed. Keep that failure as unconfirmed proof.
                try:
                    self.bridge.write_mode(1)
                    self.sleep(0.5)
                    if self.bridge.read_mode() != 1:
                        errors.append('Shared firmware restoration was not confirmed')
                except Exception as exc:
                    errors.append(str(exc))
                if self.backend.read(0x13) != self.baseline[0x13]:
                    errors.append('Shared output bits were not restored')
        except Exception as exc:
            errors.append(str(exc))
        if not errors:
            self.restored = True
        return errors

    def close(self):
        self.bridge.close()


def open_shared_session(computer):
    backend = LhmSecondary(computer)
    with IsaBus():
        bridge = SignedEcBridge()
    session = SharedFanSession(backend, bridge)
    try:
        session.prepare()
        return session
    except Exception:
        bridge.close()
        raise
