"""Interactive sensor owners must not inherit Scheduler's background priority."""
from dataclasses import replace
import unittest
from unittest import mock
import xml.etree.ElementTree as ET

import overlay


class AutostartPriorityTests(unittest.TestCase):
    user = 'S-1-5-21-test'

    def setUp(self):
        self.xml = overlay._build_autostart_task_xml(self.user)
        self.current = overlay._parse_autostart_task_xml(self.xml)

    def test_generated_task_explicitly_uses_normal_interactive_priority(self):
        root = ET.fromstring(self.xml)
        self.assertEqual(root.findtext('.//' + overlay._xml_tag('Priority')), '6')
        self.assertEqual(self.current.priority, '6')
        self.assertEqual(overlay._classify_autostart_task(self.current, self.user),
                         overlay.AUTOSTART_SAFE_CURRENT)

    def test_background_default_and_other_priorities_require_repair(self):
        for priority in ('', '7', '8', '10', '0', '4', '5', 'invalid'):
            with self.subTest(priority=priority):
                definition = replace(self.current, priority=priority)
                self.assertEqual(overlay._classify_autostart_task(definition, self.user),
                                 overlay.AUTOSTART_STALE_HEATMAP)

    def test_old_xml_without_priority_is_not_current(self):
        root = ET.fromstring(self.xml)
        settings = root.find(overlay._xml_tag('Settings'))
        priority = settings.find(overlay._xml_tag('Priority'))
        if priority is not None:
            settings.remove(priority)
        definition = overlay._parse_autostart_task_xml(ET.tostring(root))
        self.assertEqual(overlay._classify_autostart_task(definition, self.user),
                         overlay.AUTOSTART_STALE_HEATMAP)

    def test_enabled_background_task_uses_guarded_migration(self):
        old = replace(self.current, priority='7')
        with (mock.patch.object(overlay, '_resolve_autostart_identity',
                                return_value=(self.user, (self.user,), None)),
              mock.patch.object(overlay, '_query_autostart_task_definition', return_value=(old, None)),
              mock.patch.object(overlay, 'enable_autostart', return_value=(True, 'migrated')) as enable):
            result = overlay.reconcile_autostart_security()
        self.assertTrue(result.changed and result.ok and result.enabled)
        enable.assert_called_once_with(expected_existing=old)

    def test_disabled_background_task_and_read_only_checks_do_not_write(self):
        for enabled, read_only in (('false', False), ('true', True)):
            with self.subTest(enabled=enabled, read_only=read_only):
                old = replace(self.current, priority='7', enabled=enabled)
                with (mock.patch.object(overlay, '_resolve_autostart_identity',
                                        return_value=(self.user, (self.user,), None)),
                      mock.patch.object(overlay, '_query_autostart_task_definition', return_value=(old, None)),
                      mock.patch.object(overlay, 'enable_autostart') as enable):
                    result = overlay.reconcile_autostart_security(read_only=read_only)
                self.assertFalse(result.changed)
                enable.assert_not_called()


if __name__ == '__main__':
    unittest.main()
