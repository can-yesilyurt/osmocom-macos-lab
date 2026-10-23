#!/usr/bin/env python3
"""Exercise the built core and BTS configuration without opening an SDR.

Requires a completed build and ./lab configure. Refuses occupied ports through
the normal launcher. Stops only the processes it creates.
"""
import json
import os
import signal
import subprocess
import sys
import time

sys.dont_write_bytecode = True
from common import ROOT, state_dir
from configure import require_config
from runtime import command
from vty import query, ready


def stop(proc):
    if proc.poll() is None:
        proc.send_signal(signal.SIGINT)
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=10)
    if proc.returncode != 0:
        raise RuntimeError(f"Smoke-test child exited {proc.returncode}; inspect private smoke logs")


def main():
    p = require_config()
    state = state_dir()
    logs = state / "logs/smoke"
    logs.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    checks = []
    core = None
    with (logs / "core.log").open("w") as out:
        try:
            core = subprocess.Popen([str(ROOT / "lab"), "core"], stdout=out,
                                    stderr=subprocess.STDOUT, start_new_session=True)
            end = time.monotonic() + 60
            while "Core links ready." not in (logs / "core.log").read_text():
                if core.poll() is not None or time.monotonic() > end:
                    raise RuntimeError("Core startup failed; inspect " + str(logs / "core.log"))
                time.sleep(.2)
            checks.append("BSC/MSC BSSMAP, MSC/STP M3UA and MSC/HLR GSUP connected")
            status = subprocess.run([str(ROOT / "lab"), "status"], capture_output=True, text=True, check=True)
            if "Admin 'Locked'" not in status.stdout or "BTS is not running" not in status.stdout:
                raise RuntimeError("Unexpected RF/BTS state in the core-only check")
            checks.append("Status reports RF locked and BTS absent")
            record = (state / "run/core-pids.json").read_text()
            duplicate = subprocess.run([str(ROOT / "lab"), "core", "--check"], capture_output=True, text=True)
            if (duplicate.returncode != 1 or "already running" not in duplicate.stderr
                    or core.poll() is not None
                    or record != (state / "run/core-pids.json").read_text()):
                raise RuntimeError("Duplicate launcher did not preserve the original core")
            checks.append("Duplicate launcher refused without stopping the original core")
        finally:
            if core is not None:
                stop(core)
    checks.append("Core children shut down cleanly")

    # --vty-test parses the real BTS configuration but suppresses BSC connection.
    if ready(p["ports"]["bts_vty"]):
        raise RuntimeError("BTS VTY already occupied; no BTS was replaced")
    args = command("bts", "osmo-bts-trx", "osmo-bts-trx") + ["--vty-test"]
    with (logs / "bts-vty.log").open("w") as out:
        bts = subprocess.Popen(args, cwd=state / "data", stdout=out,
                               stderr=subprocess.STDOUT, start_new_session=True)
        try:
            end = time.monotonic() + 10
            while not ready(p["ports"]["bts_vty"]):
                if bts.poll() is not None or time.monotonic() > end:
                    raise RuntimeError("BTS VTY test failed; inspect private smoke logs")
                time.sleep(.1)
            text = query(p["ports"]["bts_vty"], ["show running-config"])
            if "osmotrx tx-attenuation oml" not in text:
                raise RuntimeError("BTS did not retain OML attenuation")
            checks.append("Real BTS parses the profile with OML attenuation in VTY test mode")
        finally:
            stop(bts)
    result = {"passed": True, "checks": checks, "sdr_opened": False, "rf_enabled": False}
    (logs / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
