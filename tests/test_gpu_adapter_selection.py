import unittest
from unittest import mock

import amd_gpu_fan as native


BOARD = dict(name='AMD Radeon RX 7900 XT', vendor='1002', device='744c',
             subsystem='240c', subvendor='1458')
SLOTS = {7: 'name', 3: 'vendor', 14: 'device', 16: 'subsystem', 17: 'subvendor'}


def candidate(identity):
    gpu = mock.MagicMock()
    gpu.__enter__.return_value = gpu
    def close(*_):
        gpu.close()
        return False
    gpu.__exit__.side_effect = close
    gpu.get.side_effect = lambda slot, _kind: identity[SLOTS[slot]].encode()
    return gpu


class GpuAdapterSelectionTests(unittest.TestCase):
    def test_integrated_adapter_order_does_not_change_exact_board_selection(self):
        igpu = dict(BOARD, name='AMD Radeon Graphics', device='13c0', subsystem='0000')
        for identities in ([igpu, BOARD], [BOARD, igpu], [BOARD]):
            with self.subTest(identities=identities):
                probes = [candidate(identity) for identity in identities]
                retained = candidate(BOARD)
                gpus = mock.Mock()
                gpus.call.return_value = len(probes)
                gpus.child.side_effect = probes + [retained]
                selected, identity = native.select_profile_gpu(gpus)
                self.assertIs(selected, retained)
                self.assertEqual(identity, BOARD)
                self.assertEqual(gpus.child.call_args.args[2], (identities.index(BOARD),))
                for gpu in probes:
                    gpu.close.assert_called_once()
                retained.close.assert_not_called()

    def test_no_match_and_duplicate_matching_boards_are_rejected_without_retaining_refs(self):
        for identities in ([dict(BOARD, subvendor='1043')], [BOARD, BOARD]):
            with self.subTest(identities=identities):
                probes = [candidate(identity) for identity in identities]
                gpus = mock.Mock()
                gpus.call.return_value = len(probes)
                gpus.child.side_effect = probes
                with self.assertRaisesRegex(native.AdlxError, 'exactly one matching'):
                    native.select_profile_gpu(gpus)
                for gpu in probes:
                    gpu.close.assert_called_once()

    def test_selected_slot_identity_is_rechecked_before_retaining(self):
        probe, changed = candidate(BOARD), candidate(dict(BOARD, device='13c0'))
        gpus = mock.Mock()
        gpus.call.return_value = 1
        gpus.child.side_effect = [probe, changed]
        with self.assertRaisesRegex(native.AdlxError, 'identity changed'):
            native.select_profile_gpu(gpus)
        probe.close.assert_called_once()
        changed.close.assert_called_once()

    def test_identity_read_failure_releases_probe_and_prevents_tuning(self):
        probe = candidate(BOARD)
        probe.get.side_effect = native.AdlxError('GPU disappeared')
        gpus = mock.Mock()
        gpus.call.return_value = 1
        gpus.child.return_value = probe
        with self.assertRaisesRegex(native.AdlxError, 'disappeared'):
            native.select_profile_gpu(gpus)
        probe.close.assert_called_once()
        self.assertEqual(gpus.child.call_count, 1)
