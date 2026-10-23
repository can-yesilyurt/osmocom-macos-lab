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
from build import require_timer_callbacks, require_compatible_sources
import common
import configure
import runtime
import network
from packet_probe import parse_output,parse_http_output
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

    def test_gprs_render_reserves_data_slots_and_enables_packet_subscribers(self):
        self.change(gprs_enabled=True)
        self.render()
        cfg = common.state_dir() / "configs"
        bsc = (cfg / "osmo-bsc.cfg").read_text()
        self.assertIn("gprs mode gprs", bsc)
        self.assertEqual(bsc.count("phys_chan_config PDCH"), 2)
        self.assertEqual(bsc.count("phys_chan_config TCH/F"), 4)
        self.assertIn("rf_locked 1", bsc)
        self.assertIn("subscriber-create-on-demand 5 cs+ps", (cfg / "osmo-hlr.cfg").read_text())
        self.assertIn("ip ifconfig 10.45.0.1/24", (cfg / "osmo-ggsn.cfg").read_text())
        self.assertIn("authentication optional", (cfg / "osmo-sgsn.cfg").read_text())
        self.assertEqual(len(list(cfg.glob("*.cfg"))), 11)
        self.assertNotIn("apn * ggsn 0", (cfg / "osmo-sgsn.cfg").read_text())
        self.change(accept_any_apn=True)
        self.render()
        self.assertIn("apn * ggsn 0", (cfg / "osmo-sgsn.cfg").read_text())

    def test_gprs_profile_rejects_injection_and_broad_or_conflicting_networks(self):
        original = self.profile.read_text()
        for fields in [dict(apn="osmo\nno shutdown"), dict(data_network="0.0.0.0/0"),
                       dict(data_network="10.45.0.1/24"),dict(data_network="127.0.0.0/24"),
                       dict(data_network="10.0.0.0/8"),dict(data_network=None),
                       dict(dns_servers=["1.1.1.1\nshutdown","8.8.8.8"]),dict(data_mtu=True),
                       dict(gprs_enabled=True,ports={"bsc_mgcp_client":2123}),
                       dict(gprs_enabled=True,ports={"pcu_ns":23000}),dict(accept_any_apn="yes")]:
            with self.subTest(fields=fields):
                self.profile.write_text(original)
                self.change(**fields)
                with self.assertRaises(ValueError):
                    self.render()

    def test_pf_rules_scope_nat_to_data_pool_and_keep_tunnel_uplink(self):
        rules = network.pf_rules("10.45.0.0/24","utun5","utun4",1280)
        self.assertIn("nat on utun4 inet from 10.45.0.0/24 to any -> (utun4)",rules)
        self.assertIn("scrub on utun5 inet all max-mss 1240",rules)
        for tunnel,uplink in [("utun4","utun4"),("utun5","en0\npass all")]:
            with self.assertRaises(ValueError):
                network.pf_rules("10.45.0.0/24",tunnel,uplink,1280)

    def test_cleanup_preserves_main_pf_and_unrelated_processes(self):
        record = dict(anchor=network.ANCHOR,anchor_loaded=True,forwarding_changed=True,
                      previous_forwarding="0",pf_token="1234",loopback_added=True)
        (common.state_dir() / "run").mkdir(parents=True)
        with mock.patch.object(network,"owned_ggsn",return_value=False), \
             mock.patch.object(network,"run",return_value="") as run, \
             mock.patch.object(subprocess,"run",return_value=mock.Mock(returncode=0,stderr="")) as command:
            network.cleanup(record)
            command.assert_called_once_with(["/sbin/pfctl","-a",network.ANCHOR,"-f","/dev/null"],capture_output=True,text=True)
            calls = [call.args[0] for call in run.call_args_list]
            self.assertIn(["/sbin/pfctl","-X","1234"],calls)
            self.assertIn(["/usr/sbin/sysctl","-w","net.inet.ip.forwarding=0"],calls)
            self.assertFalse(any("-d" in c or "-F" in c for c in calls))

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


class BuildChecks(unittest.TestCase):
    def test_packet_probe_requires_assigned_address_and_real_reply_count(self):
        # A cause-128 DELETE response is not evidence of PDP creation.
        rejected = "Received delete PDP context response. Cause value: 128\n3 packets transmitted, 0 packets received\n"
        self.assertEqual(parse_output(rejected,"10.45.0.0/24"),(False,0))
        accepted = "Received create PDP context response.\nPDP ctx: received EUA with IP address: 10.45.0.2\n3 packets transmitted, 3 packets received\n"
        self.assertEqual(parse_output(accepted,"10.45.0.0/24"),(True,3))
        self.assertEqual(parse_output(accepted,"10.46.0.0/24"),(False,3))

    def test_http_probe_reports_transport_separately_from_server_status(self):
        text = "RESULT accepted=1 dns_replies=1 tcp=1 http=1 http_status=403\n"
        result = parse_http_output(text)
        self.assertTrue(result["context_accepted"])
        self.assertTrue(result["tcp_connected"])
        self.assertEqual(result["http_status"],403)
        result = parse_http_output("PDP creation cause=128\nDNS query sent\n")
        self.assertFalse(result["http_response"])
        self.assertIsNone(result["http_status"])

    def test_new_dependencies_preserve_existing_build_provenance(self):
        original = {"core":{"commit":"a"*40,"version":"1"}}
        extended = {**original,"packet":{"commit":"b"*40,"version":"1"}}
        require_compatible_sources(original,extended)
        for changed in ({}, {"core":{"commit":"c"*40,"version":"1"}}):
            with self.assertRaisesRegex(ValueError,"different source pins"):
                require_compatible_sources(original,changed)

    def test_zero_exit_timer_program_requires_real_callbacks(self):
        require_timer_callbacks("setup rc=0\nticks=6 in 2.6 s (expected 1 + 5)\n")
        with self.assertRaisesRegex(RuntimeError, "expected six callbacks"):
            require_timer_callbacks("setup rc=-1\nticks=0 in 2.6 s (expected 1 + 5)\n")


if __name__ == "__main__":
    unittest.main()
