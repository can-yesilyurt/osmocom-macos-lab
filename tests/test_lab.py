"""Offline checks for configuration, VTY framing and process ownership."""
import contextlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import check_publication
import common
import configure
import runtime
import vty


class LocalState(unittest.TestCase):
    def setUp(self):
        # Keep Unix-domain paths short, including on macOS CI runners.
        self.temp = tempfile.TemporaryDirectory(prefix="osmolab-", dir="/tmp")
        self.root = Path(self.temp.name)
        self.profile = self.root / "profile.json"
        self.profile.write_text((common.ROOT / "lab.example.json").read_text())
        self.env = mock.patch.dict(os.environ, {
            "OSMO_LAB_STATE": str(self.root / "state"),
            "OSMO_LAB_PROFILE": str(self.profile),
        })
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def change(self, **fields):
        p = json.loads(self.profile.read_text())
        p.update(fields)
        self.profile.write_text(json.dumps(p))

    def render(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return configure.render()

    def test_private_render_starts_rf_locked_and_uses_oml_attenuation(self):
        self.change(mcc="520", mnc="999", operator_name="OSMO-TEST")
        self.render()
        configs = common.state_dir() / "configs"
        self.assertIn("rf_locked 1", (configs / "osmo-bsc.cfg").read_text())
        self.assertNotIn("rf_locked 0", (configs / "osmo-bsc.cfg").read_text())
        self.assertIn("osmotrx tx-attenuation oml", (configs / "osmo-bts-trx.cfg").read_text())
        self.assertIn("ipa-name MSC-520-999-0", (configs / "osmo-msc.cfg").read_text())
        self.assertNotIn("dev-args", (configs / "osmo-trx-lms.cfg").read_text())
        self.assertEqual(len(list(configs.glob("*.cfg"))), 8)
        for p in configs.glob("*.cfg"):
            self.assertNotIn("@", p.read_text())
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list((common.state_dir() / "data").iterdir()), [])

    def test_profile_change_requires_render(self):
        self.render()
        self.change(arfcn=30)
        with self.assertRaisesRegex(ValueError, "Profile changed"):
            configure.require_config()

    def test_invalid_profiles_do_not_render(self):
        original = self.profile.read_text()
        cases = [
            {"mcc": "５２０"}, {"mnc": 1}, {"arfcn": True}, {"arfcn": 125},
            {"operator_name": "name\nnetwork"}, {"device_args": "x\nchan 1"},
            {"max_power_reduction_db": 19}, {"rx_path": "unknown"},
            {"ports": {"bsc_vty": 4222}}, {"ports": {"bts_vty": 3002}},
            {"ports": {"trx_vty": 3003}}, {"ports": {"bsc_encap": 9898}},
            {"ports": {"bts_base": 15701}},
            {"ports": {"mgw_msc_rtp_first": 5000}},
        ]
        for fields in cases:
            with self.subTest(fields=fields):
                self.profile.write_text(original)
                self.change(**fields)
                with self.assertRaises(ValueError):
                    self.render()
                self.assertFalse((common.state_dir() / "configs").exists())

    def test_subscriber_auto_creation_can_be_disabled(self):
        self.change(auto_create_subscribers=False)
        self.render()
        self.assertIn("no subscriber-create-on-demand", (common.state_dir() / "configs/osmo-hlr.cfg").read_text())

    def test_long_socket_path_rejected_before_writing(self):
        with mock.patch.dict(os.environ, {"OSMO_LAB_STATE": str(self.root / ("x" * 100))}):
            with self.assertRaisesRegex(ValueError, "PCU socket path"):
                self.render()
            self.assertFalse(common.state_dir().exists())

    def test_rf_refuses_unowned_or_stale_core_before_contacting_vty(self):
        self.render()
        with mock.patch.object(runtime, "query") as query:
            with self.assertRaisesRegex(ValueError, "No core owned"):
                runtime.set_rf("on")
            common.write_json(common.state_dir() / "run/core-pids.json", {"bsc": os.getpid()})
            with self.assertRaisesRegex(ValueError, "stale"):
                runtime.set_rf("off")
            query.assert_not_called()


class VTYTests(unittest.TestCase):
    def exchange(self, response):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)
        port = listener.getsockname()[1]
        received, errors = [], []

        def serve():
            try:
                with listener, listener.accept()[0] as conn:
                    conn.settimeout(5)
                    conn.sendall(b"Welcome\r\nOsmo")
                    conn.sendall(b"> ")
                    buf = b""
                    for answer in (b"Osmo# ", b"Osmo# ", response):
                        while b"\r\n" not in buf:
                            buf += conn.recv(4096)
                        line, buf = buf.split(b"\r\n", 1)
                        received.append(line.decode())
                        conn.sendall(answer[:-1])
                        conn.sendall(answer[-1:])
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            result = vty.query(port, ["show msc"])
            self.assertEqual(received, ["enable", "terminal length 0", "show msc"])
            return result
        finally:
            thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
            if errors:
                raise errors[0]

    def test_fragmented_prompt_and_command_order(self):
        text = self.exchange(b"BSSMAP state: CONNECTED\r\nOsmo# ")
        self.assertIn("BSSMAP state: CONNECTED", text)

    def test_vty_command_failure_is_not_success(self):
        with self.assertRaisesRegex(RuntimeError, "VTY rejected"):
            self.exchange(b"% Unknown command\r\nOsmo# ")

    def test_line_injection_rejected_without_opening_socket(self):
        with mock.patch.object(socket, "create_connection") as connect:
            with self.assertRaises(ValueError):
                vty.query(1, ["show msc\r\nconfigure terminal"])
            connect.assert_not_called()


class OwnershipTests(unittest.TestCase):
    def test_shutdown_stops_owned_child_and_preserves_unrelated_process(self):
        script = "import signal,time; signal.signal(signal.SIGINT, lambda *a: exit(0)); print('ready',flush=True); time.sleep(30)"
        owned = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=subprocess.PIPE, text=True)
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            self.assertEqual(owned.stdout.readline().strip(), "ready")
            with contextlib.redirect_stdout(io.StringIO()):
                result = runtime.stop_children([("bsc", owned)])
            self.assertEqual(result, {"bsc": 0})
            self.assertIsNone(other.poll())
        finally:
            for proc in (owned, other):
                if proc.poll() is None:
                    proc.kill()
                proc.wait()
            owned.stdout.close()


class PublicationTests(unittest.TestCase):
    def test_private_artifacts_and_identifiers_rejected(self):
        self.assertTrue(check_publication.issues("data/hlr.db", b"not a real database"))
        self.assertTrue(check_publication.issues("note.txt", ("/" + "Users" + "/local-person/file").encode()))
        self.assertTrue(check_publication.issues("note.txt", ("9" * 15).encode()))
        self.assertTrue(check_publication.issues("note.txt", ("serial=" + "0" * 16).encode()))
        self.assertEqual(check_publication.issues("README.md", b"Use MSISDN 1001 to call 1002."), [])


if __name__ == "__main__":
    unittest.main()
