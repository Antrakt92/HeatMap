"""Narrow ADLX fan-only binding to the AMD display driver's installed runtime.

C ABI: GPUOpen-LibrariesAndSDKs/ADLX d9f04a9bba022d6cf6333f005dd540b4ad19fb63,
SDK/Include/{ISystem,IGPUTuning,IGPUManualFanTuning,IPerformanceMonitoring2}.h.
No clock, voltage, power or ResetToFactory entry points are exposed.
"""
import ctypes as c
import os
from contextlib import contextmanager

P = c.c_void_p
I = c.c_int
B = c.c_uint8


class AdlxError(RuntimeError):
    pass


class IntRange(c.Structure):
    _fields_ = [('min', I), ('max', I), ('step', I)]


def check(result, operation):
    if result != 0:
        raise AdlxError(f'{operation}: ADLX error {result}')


class Interface:
    """Owned reference to an explicitly selected, versioned C vtable."""
    def __init__(self, pointer):
        if not pointer:
            raise AdlxError('ADLX returned a null interface')
        self.pointer = P(pointer.value if isinstance(pointer, P) else pointer)

    def call(self, slot, types=(), values=(), result=I):
        if not self.pointer:
            raise AdlxError('ADLX interface already released')
        table = c.cast(self.pointer, c.POINTER(c.POINTER(P))).contents
        function = c.WINFUNCTYPE(result, P, *types)(table[slot])
        return function(self.pointer, *values)

    def get(self, slot, kind=I):
        value = kind()
        check(self.call(slot, (c.POINTER(kind),), (c.byref(value),)), f'Get slot {slot}')
        return value.value

    def child(self, slot, types=(), values=()):
        value = P()
        check(self.call(slot, (*types, c.POINTER(P)), (*values, c.byref(value))), f'Interface slot {slot}')
        return Interface(value)

    def query(self, name):
        return self.child(2, (c.c_wchar_p,), (name,))

    def close(self):
        if self.pointer:
            self.call(1, result=c.c_long)
            self.pointer = P()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class AmdGpuFan:
    """Construction and reads never change fan settings; mutations are explicit."""
    def __init__(self):
        self.dll = None
        self.system = None
        self.owned = []
        self.initialized = False
        try:
            if os.name != 'nt' or c.sizeof(P) != 8:
                raise AdlxError('GPU fan control requires 64-bit Windows')
            kernel = c.WinDLL('kernel32', use_last_error=True)
            directory = c.create_unicode_buffer(32768)
            kernel.GetSystemDirectoryW.argtypes = (c.c_wchar_p, c.c_uint)
            kernel.GetSystemDirectoryW.restype = c.c_uint
            count = kernel.GetSystemDirectoryW(directory, len(directory))
            if not 0 < count < len(directory):
                raise AdlxError('Cannot locate the Windows driver directory')
            self.dll = c.CDLL(os.path.join(directory.value, 'amdadlx64.dll'))
            self.dll.ADLXInitialize.argtypes = (c.c_uint64, c.POINTER(P))
            self.dll.ADLXInitialize.restype = I
            self.dll.ADLXTerminate.argtypes = ()
            self.dll.ADLXTerminate.restype = I
            pointer = P()
            # The 1.0 base ABI is stable; optional metrics are obtained by QueryInterface.
            check(self.dll.ADLXInitialize(1 << 48, c.byref(pointer)), 'ADLXInitialize')
            self.initialized = True
            self.system = Interface(pointer)  # IADLXSystem is not reference-counted.
            with self.system.child(1) as gpus:
                if gpus.call(3, result=c.c_uint) != 1:
                    raise AdlxError('GPU fan profile requires exactly one AMD GPU')
                self.gpu = self.keep(gpus.child(11, (c.c_uint,), (0,)))
            self.identity = {key: self.gpu.get(slot, c.c_char_p).decode('utf-8') for key, slot in (
                ('name', 7), ('vendor', 3), ('device', 14), ('subsystem', 16), ('subvendor', 17))}
            if (self.identity['name'] != 'AMD Radeon RX 7900 XT' or
                    tuple(self.identity[key].lower().removeprefix('0x') for key in
                          ('vendor', 'device', 'subsystem', 'subvendor')) != ('1002', '744c', '240c', '1458')):
                raise AdlxError(f'Unsupported GPU fan profile: {self.identity}')
            tuning = self.keep(self.system.child(8))
            supported = B()
            check(tuning.call(10, (P, c.POINTER(B)), (self.gpu.pointer, c.byref(supported))), 'Fan tuning support')
            if not supported.value:
                raise AdlxError('AMD driver does not support fan tuning')
            self.fan = self.keep(tuning.child(16, (P,), (self.gpu.pointer,)))
            self.metrics_service = self.keep(self.system.child(9))
            speed, temperature = IntRange(), IntRange()
            check(self.fan.call(3, (c.POINTER(IntRange), c.POINTER(IntRange)),
                                (c.byref(speed), c.byref(temperature))), 'Fan ranges')
            self.speed_range = [speed.min, speed.max, speed.step]
            self.temperature_range = [temperature.min, temperature.max, temperature.step]
            if not (0 <= speed.min <= 30 and speed.max == 100 and speed.step == 1):
                raise AdlxError(f'Unsupported fan percentage range: {self.speed_range}')
        except Exception as exc:
            try:
                self.close()
            except Exception as cleanup_error:
                add_note = getattr(exc, 'add_note', None)
                if callable(add_note):
                    add_note(f'ADLX initialization cleanup: {cleanup_error}')
            raise

    def keep(self, interface):
        self.owned.append(interface)
        return interface

    @contextmanager
    def states(self):
        with self.fan.child(4) as states:
            if states.call(3, result=c.c_uint) != 5:
                raise AdlxError('Expected five AMD fan curve points')
            yield states

    def snapshot(self):
        with self.states() as states:
            points = []
            for index in range(5):
                with states.child(11, (c.c_uint,), (index,)) as point:
                    points.append([point.get(5), point.get(3)])
        supported = self.fan.get(8, B)
        return {'points': points, 'zero_rpm': bool(self.fan.get(9, B)) if supported else None}

    def set_points(self, points):
        if (len(points) != 5 or any(len(point) != 2 or any(type(v) is not int for v in point)
                                   for point in points)):
            raise AdlxError('Invalid fan curve')
        with self.states() as states:
            for index, (temperature, speed) in enumerate(points):
                with states.child(11, (c.c_uint,), (index,)) as point:
                    check(point.call(6, (I,), (temperature,)), 'Fan point temperature')
                    check(point.call(4, (I,), (speed,)), 'Fan point speed')
            error_index = I(-1)
            result = self.fan.call(6, (P, c.POINTER(I)), (states.pointer, c.byref(error_index)))
            check(result, f'Validate fan curve (point {error_index.value})')
            check(self.fan.call(7, (P,), (states.pointer,)), 'Apply fan curve')

    def set_zero_rpm(self, enabled):
        check(self.fan.call(10, (B,), (bool(enabled),)), 'Set Zero RPM')

    def readings(self):
        with self.metrics_service.child(18, (P,), (self.gpu.pointer,)) as base:
            with base.query('IADLXGPUMetrics1') as metrics:
                values = {}
                for key, slot, kind in (('gpu_core_temp', 7, c.c_double),
                                        ('gpu_hotspot_temp', 8, c.c_double),
                                        ('gpu_memory_temp', 15, c.c_double),
                                        ('gpu_fan', 11, I)):
                    try:
                        values[key] = metrics.get(slot, kind)
                    except AdlxError:
                        values[key] = None
                values['timestamp_ms'] = metrics.get(3, c.c_int64)
                return values

    def close(self):
        errors = []
        # A failed release must not prevent the remaining references and runtime
        # from being closed. Remove ownership first to avoid double release.
        while self.owned:
            interface = self.owned.pop()
            try:
                interface.close()
            except Exception as exc:
                errors.append(str(exc))
        if self.initialized:
            self.initialized = False
            self.system = None
            try:
                check(self.dll.ADLXTerminate(), 'ADLXTerminate')
            except Exception as exc:
                errors.append(str(exc))
        if errors:
            raise AdlxError('; '.join(errors))


if __name__ == '__main__':
    import json
    adapter = AmdGpuFan()
    try:
        print(json.dumps({'gpu': adapter.identity, 'speed_range': adapter.speed_range,
                          'temperature_range': adapter.temperature_range,
                          'baseline': adapter.snapshot(), 'readings': adapter.readings()}, indent=2))
    finally:
        adapter.close()
