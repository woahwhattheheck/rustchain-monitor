import unittest
from unittest import mock

import rustchain_monitor as monitor_mod


class TLSVerificationPolicyTests(unittest.TestCase):
    def test_monitor_verifies_tls_by_default(self):
        monitor = monitor_mod.RustChainMonitor("https://public.example")
        self.assertIs(monitor.session.verify, True)

    def test_insecure_mode_is_explicit_and_exact_boolean(self):
        monitor = monitor_mod.RustChainMonitor("https://self-signed.example", insecure=True)
        self.assertIs(monitor.session.verify, False)

        with self.assertRaisesRegex(ValueError, "insecure must be a boolean"):
            monitor_mod.RustChainMonitor("https://public.example", insecure=1)

    def test_node_target_policy_defaults_secure_and_rejects_coercion(self):
        secure = monitor_mod.normalize_node_target({"url": "https://public.example"})
        insecure = monitor_mod.normalize_node_target(
            {"url": "https://self-signed.example", "insecure": True}
        )
        self.assertIs(secure["insecure"], False)
        self.assertIs(insecure["insecure"], True)

        for bad_value in (1, "true", None, [], {}):
            with self.subTest(bad_value=bad_value):
                with self.assertRaisesRegex(ValueError, "field 'insecure' must be a boolean"):
                    monitor_mod.normalize_node_target(
                        {"url": "https://public.example", "insecure": bad_value}
                    )

    def test_multi_node_tls_policy_isolated_per_target(self):
        created = []

        class FakeMonitor:
            def __init__(self, node_url, use_color=True, history_db_path=None, insecure=False):
                created.append((node_url, insecure))

            def collect_network_snapshot(self):
                return {"summary": {}}

        targets = [
            monitor_mod.normalize_node_target(
                {"url": "https://public.example", "name": "public"}
            ),
            monitor_mod.normalize_node_target(
                {
                    "url": "https://self-signed.example",
                    "name": "operator",
                    "insecure": True,
                }
            ),
        ]
        with mock.patch.object(monitor_mod, "RustChainMonitor", FakeMonitor):
            snapshots = monitor_mod.collect_multi_node_snapshots(targets)

        self.assertEqual(
            created,
            [
                ("https://public.example", False),
                ("https://self-signed.example", True),
            ],
        )
        self.assertEqual(len(snapshots), 2)
        self.assertTrue(all(row["summary"]["scrape_ok"] for row in snapshots))


if __name__ == "__main__":
    unittest.main()
