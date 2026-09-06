from dataclasses import replace
import unittest
from unittest import mock

import overlay


class ImmediateAutostartTests(unittest.TestCase):
    user = "S-1-5-21-test"

    def setUp(self):
        self.current = overlay._parse_autostart_task_xml(overlay._build_autostart_task_xml(self.user))
        self.old = replace(self.current, trigger_delay="PT30S")
        self.identity = mock.patch.object(overlay, "_resolve_autostart_identity", return_value=(self.user, (self.user,), None))
        self.identity.start()
        self.addCleanup(self.identity.stop)

    def test_zero_delay_is_emitted_and_scheduler_omission_is_equivalent(self):
        self.assertEqual(self.current.trigger_delay, "PT0S")
        for delay in ("", "PT0S", "PT0M", "PT0H", "P0D", "PT0H0M0S", "P0DT0H0M0S"):
            with self.subTest(delay=delay):
                self.assertEqual(overlay._classify_autostart_task(replace(self.current, trigger_delay=delay), self.user), overlay.AUTOSTART_SAFE_CURRENT)

    def test_delayed_current_user_task_is_migrated_using_original_definition_guard(self):
        with (
            mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(self.old, None)),
            mock.patch.object(overlay, "enable_autostart", return_value=(True, "migrated")) as enable,
        ):
            result = overlay.reconcile_autostart_security()
        self.assertTrue(result.ok)
        self.assertTrue(result.enabled)
        enable.assert_called_once_with(expected_existing=self.old)

    def test_disabled_task_and_disabled_trigger_remain_untouched(self):
        for definition in (replace(self.old, enabled="false"), replace(self.old, trigger_enabled="false")):
            with self.subTest(definition=definition), mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(definition, None)), mock.patch.object(overlay, "enable_autostart") as enable:
                result = overlay.reconcile_autostart_security()
            self.assertTrue(result.ok)
            self.assertFalse(result.enabled)
            self.assertFalse(result.changed)
            enable.assert_not_called()

    def test_absence_is_preserved(self):
        with mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(None, None)), mock.patch.object(overlay, "enable_autostart") as enable:
            result = overlay.reconcile_autostart_security()
        self.assertTrue(result.ok)
        self.assertFalse(result.enabled)
        enable.assert_not_called()

    def test_wrong_principal_trigger_and_foreign_source_are_never_migrated(self):
        for definition in (replace(self.old, principal_user_id="other"), replace(self.old, trigger_user_id="other"), replace(self.old, source="Foreign")):
            with self.subTest(definition=definition), mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(definition, None)), mock.patch.object(overlay, "enable_autostart") as enable:
                result = overlay.reconcile_autostart_security()
            self.assertFalse(result.ok)
            self.assertFalse(result.changed)
            enable.assert_not_called()

    def test_concurrent_disable_aborts_before_mutations(self):
        with mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(replace(self.old, enabled="false"), None)), mock.patch.object(overlay, "_remove_autostart_task_fail_closed") as remove, mock.patch.object(overlay, "_register_autostart_xml") as register:
            ok, message = overlay.enable_autostart(expected_existing=self.old)
        self.assertFalse(ok)
        self.assertIn("changed during migration", message)
        remove.assert_not_called()
        register.assert_not_called()

    def test_explicit_enable_and_disable_do_not_replace_another_users_task(self):
        definition = replace(self.old, principal_user_id="other")
        for operation in (overlay.enable_autostart, overlay.disable_autostart):
            with self.subTest(operation=operation.__name__), mock.patch.object(overlay, "_query_autostart_task_definition", return_value=(definition, None)), mock.patch.object(overlay, "_remove_autostart_task_fail_closed") as remove, mock.patch.object(overlay, "_register_autostart_xml") as register:
                ok, message = operation()
            self.assertFalse(ok)
            self.assertIn("another", message)
            remove.assert_not_called()
            register.assert_not_called()

    def test_migration_failure_leaves_disabled_old_task_without_registering(self):
        with mock.patch.object(overlay, "_query_autostart_task_definition", side_effect=[(self.old, None), (self.old, None), (replace(self.old, enabled="false"), None)]), mock.patch.object(overlay, "_disable_autostart_task", return_value=(True, "disabled")), mock.patch.object(overlay, "_delete_autostart_task", return_value=(False, "denied")), mock.patch.object(overlay, "_register_autostart_xml") as register:
            ok, message = overlay.enable_autostart(expected_existing=self.old)
        self.assertFalse(ok)
        self.assertIn("disabled task could not be deleted", message)
        register.assert_not_called()

    def test_concurrent_disable_at_removal_recheck_is_preserved(self):
        with mock.patch.object(overlay, "_query_autostart_task_definition", side_effect=[(self.old, None), (replace(self.old, enabled="false"), None)]), mock.patch.object(overlay, "_disable_autostart_task") as disable, mock.patch.object(overlay, "_register_autostart_xml") as register:
            ok, message = overlay.enable_autostart(expected_existing=self.old)
        self.assertFalse(ok)
        self.assertIn("changed before removal", message)
        disable.assert_not_called()
        register.assert_not_called()

    def test_old_enabled_task_uses_verified_fail_closed_replacement(self):
        calls = []
        def register(xml):
            definition = overlay._parse_autostart_task_xml(xml)
            self.assertEqual(definition.trigger_delay, "PT0S")
            self.assertEqual(definition.run_level, "LeastPrivilege")
            calls.append("register")
            return True, "created"
        with mock.patch.object(overlay, "_query_autostart_task_definition", side_effect=[(self.old, None), (self.old, None), (replace(self.old, enabled="false"), None), (None, None), (self.current, None)]), mock.patch.object(overlay, "_disable_autostart_task", side_effect=lambda: (calls.append("disable") or (True, "disabled"))), mock.patch.object(overlay, "_delete_autostart_task", side_effect=lambda: (calls.append("delete") or (True, "deleted"))), mock.patch.object(overlay, "_register_autostart_xml", side_effect=register), mock.patch.object(overlay, "_delete_legacy_autostart_value", return_value=(True, "absent")):
            ok, message = overlay.enable_autostart(expected_existing=self.old)
        self.assertTrue(ok, message)
        self.assertEqual(calls, ["disable", "delete", "register"])


if __name__ == "__main__":
    unittest.main()
