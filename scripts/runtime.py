"""Foreground service ownership and explicit runtime RF control."""
from pathlib import Path
import datetime
import fcntl
import json
import os
import shlex
import signal
import socket
import subprocess
import sys
import time

from common import prefix, state_dir, write_json
from configure import require_config
from vty import ready, query

SERVICES = [("stp","bsc","osmo-stp","osmo-stp","stp_vty"),
            ("mgw-bsc","bsc","osmo-mgw","osmo-mgw-bsc","mgw_bsc_vty"),
            ("mgw-msc","bsc","osmo-mgw","osmo-mgw-msc","mgw_msc_vty"),
            ("hlr","core","osmo-hlr","osmo-hlr","hlr_vty"),
            ("msc","core","osmo-msc","osmo-msc","msc_vty"),
            ("bsc","bsc","osmo-bsc","osmo-bsc","bsc_vty")]


def command(group, binary, config, flag="-c"):
    executable = prefix(group) / "bin" / binary
    if not executable.is_file():
        raise ValueError("Binary not built: " + str(executable) + "; run ./lab build")
    return [str(executable),flag,str(state_dir() / "configs" / (config+".cfg"))]


def environment(p, component):
    env = os.environ.copy()
    env["TZ"] = "UTC"
    ports = p["ports"]
    if component in ("stp","bsc","msc"):
        env["LIBSCTP_COMPAT_UDP_ENCAPS_PORT"] = str(ports[component+"_encap"])
        env["LIBSCTP_COMPAT_UDP_ENCAPS_REMOTE_PORT"] = str(ports["stp_encap"])
    return env


def require_owner():
    """Never alter a different checkout's radio just because its ports match."""
    registry = state_dir() / "run/core-pids.json"
    if not registry.is_file():
        raise ValueError("No core owned by this checkout; start ./lab core here first")
    pid = json.loads(registry.read_text())["bsc"]
    if type(pid) is not int or pid <= 1: raise ValueError("Invalid core ownership record")
    result = subprocess.run(["ps","-p",str(pid),"-o","command="],capture_output=True,text=True)
    expected = str(prefix("bsc") / "bin/osmo-bsc")
    args = shlex.split(result.stdout.strip())
    if result.returncode or not args or args[0] != expected:
        raise ValueError("Core ownership record is stale; this command will not touch another lab")


def show_status():
    p = require_config()
    require_owner()
    ports = p["ports"]
    bsc = query(ports["bsc_vty"],["show msc","show trx 0 0"])
    for line in bsc.splitlines():
        if any(key in line for key in ("BSSMAP state:","TRX 0 of BTS","Radio Carrier NM State:","RSL State:")):
            print(line.strip())
    if ready(ports["bts_vty"]):
        bts = query(ports["bts_vty"],["show transceiver","show phy"])
        for line in bts.splitlines():
            if any(key in line for key in ("poweron","poweroff","phy link state:","tx-attenuation :")):
                print(line.strip())
    else:
        print("BTS is not running")
    if "Admin 'Locked'" in bsc: print("RF locked/muted; SDR streams can remain running")
    return 0


def set_rf(action):
    p = require_config()
    require_owner()
    ports = p["ports"]
    before = query(ports["bsc_vty"],["show msc","show trx 0 0"])
    if action == "on":
        if "BSSMAP state: CONNECTED" not in before or "RSL State: connected" not in before:
            raise ValueError("The core, TRX and BTS must be running with their links connected")
        name = f"MSC-{p['mcc']}-{p['mnc']}-0"
        if name not in query(ports["hlr_vty"],["show gsup-connections"]):
            raise ValueError("MSC is not connected to HLR")
        bts = query(ports["bts_vty"],["show transceiver"])
        if "Admin 'Unlocked'" in before and "poweron" in bts:
            print("RF is already enabled")
            return show_status()
        query(ports["bts_vty"],["configure terminal","phy 0","instance 0","osmotrx tx-attenuation oml","end"])
    elif action != "off":
        raise ValueError("RF action must be on or off")
    value = "0" if action == "on" else "1"
    query(ports["bsc_vty"],["configure terminal","network","bts 0","trx 0","rf_locked "+value,"end"])
    end = time.monotonic() + 12
    while time.monotonic() < end:
        bsc = query(ports["bsc_vty"],["show trx 0 0"])
        if action == "off" and "Admin 'Locked'" in bsc:
            print("RF administratively locked/muted")
            return show_status()
        if action == "on" and "Oper 'Enabled', Admin 'Unlocked', Avail 'OK'" in bsc:
            if "poweron" in query(ports["bts_vty"],["show transceiver"]):
                print("RF enabled for this session; the saved startup configuration remains locked")
                return show_status()
        time.sleep(.25)
    raise RuntimeError("Radio state did not settle; inspect the TRX/BTS terminals")


def stop_children(children):
    codes = {}
    for name,proc in reversed(children):
        if proc.poll() is None:
            proc.send_signal(signal.SIGTERM if name.startswith("mgw") else signal.SIGINT)
            try: proc.wait(timeout=8)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try: proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        codes[name] = proc.returncode
        print(name.upper()+" stopped",flush=True)
    return codes


def run_core(check=False):
    p = require_config()
    ports = p["ports"]
    state = state_dir()
    stopping = False
    children, streams = [], []
    logdir = None
    code = 0
    os.umask(0o077)

    def request_stop(signum,frame):
        nonlocal stopping
        stopping = True

    def alive():
        if stopping: raise InterruptedError("Stop requested")
        for name,proc in children:
            if proc.poll() is not None:
                raise RuntimeError(f"{name} exited {proc.returncode}; see {logdir}")

    def wait_for(predicate,timeout,label):
        end = time.monotonic()+timeout
        while time.monotonic()<end:
            alive()
            if predicate(): return
            time.sleep(.2)
        raise RuntimeError("Timed out: "+label)

    saved_handlers = {sig:signal.signal(sig,request_stop) for sig in (signal.SIGINT,signal.SIGTERM)}
    with (state / "run/core.lock").open("a+") as lock:
        try:
            try: fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("This core is already running; use ./lab status instead of core --check")
            for port in [ports[k] for k in ports if k.endswith(("_vty","_ctrl")) and k not in ("bts_vty","bts_ctrl","trx_vty","trx_ctrl")]+[4222,3002,3003,ports["smpp"]]:
                if ready(port): raise ValueError(f"TCP port {port} is already occupied; no existing process was replaced")
            for key in ("stp_encap","bsc_encap","msc_encap","mgw_bsc_mgcp","mgw_msc_mgcp","bsc_mgcp_client","msc_mgcp_client"):
                with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
                    probe.bind(("127.0.0.1",ports[key]))
            text = (state / "configs/osmo-bsc.cfg").read_text()
            if "rf_locked 1" not in text or "rf_locked 0" in text:
                raise ValueError("Saved BSC configuration must start with rf_locked 1")
            stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
            logdir = state / "logs/runtime" / stamp
            logdir.mkdir(parents=True)
            for name,group,binary,config,vty in SERVICES:
                args = command(group,binary,config)
                if name == "hlr": args += ["-l",str(state / "data/hlr.db")]
                f = (logdir / (name+".log")).open("wb")
                streams.append(f)
                proc = subprocess.Popen(args,cwd=state / "data",env=environment(p,name),stdout=f,stderr=subprocess.STDOUT,start_new_session=True)
                children.append((name,proc))
                wait_for(lambda:ready(ports[vty]),8,name+" VTY")
                print(f"{name.upper()} ready: 127.0.0.1:{ports[vty]}",flush=True)
            write_json(state / "run/core-pids.json",{name:proc.pid for name,proc in children})
            wait_for(lambda:"ASP_ACTIVE" in query(ports["msc_vty"],["show cs7 instance 0 asp"]),20,"MSC M3UA")
            wait_for(lambda:f"MSC-{p['mcc']}-{p['mnc']}-0" in query(ports["hlr_vty"],["show gsup-connections"]),10,"MSC/HLR GSUP")
            wait_for(lambda:"BSSMAP state: CONNECTED" in query(ports["bsc_vty"],["show msc"]),20,"BSC/MSC BSSMAP")
            print("Core links ready. RF starts locked. Run ./lab trx and ./lab bts in separate terminals.",flush=True)
            print("Explicit RF enable: ./lab rf on\nLogs: "+str(logdir),flush=True)
            if not check:
                while not stopping:
                    alive()
                    time.sleep(.25)
        except InterruptedError:
            pass
        except (OSError,ValueError,RuntimeError) as exc:
            print(str(exc),file=sys.stderr)
            code = 1
        finally:
            codes = stop_children(children)
            for name,result in codes.items():
                if result not in ((0,-signal.SIGTERM) if name.startswith("mgw") else (0,)): code = 1
            for stream in streams: stream.close()
            if logdir: write_json(logdir / "shutdown.json",codes)
            registry = state / "run/core-pids.json"
            if children and registry.exists():
                recorded = json.loads(registry.read_text())
                if recorded.get("bsc") in [proc.pid for name,proc in children]: registry.unlink()
            for sig,handler in saved_handlers.items(): signal.signal(sig,handler)
    return code


def run_radio(component):
    p = require_config()
    ports = p["ports"]
    if component not in ("trx","bts"): raise ValueError("Unknown radio component")
    if ready(ports[component+"_vty"]):
        raise ValueError("That radio management port is already occupied; no existing process was replaced")
    if component == "bts":
        sockpath = state_dir() / "run/pcu.sock"
        if sockpath.exists():
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as probe:
                probe.settimeout(.2)
                result = probe.connect_ex(str(sockpath))
            if result == 0: raise ValueError("The PCU socket is still active")
            import errno
            if result != errno.ECONNREFUSED: raise ValueError("Cannot establish whether the PCU socket is stale")
            sockpath.unlink()
    args = command(component,"osmo-trx-lms" if component == "trx" else "osmo-bts-trx",
                   "osmo-trx-lms" if component == "trx" else "osmo-bts-trx","-C" if component == "trx" else "-c")
    if component == "bts": args.insert(1,"-s")
    os.umask(0o077)
    os.chdir(state_dir() / "data")
    os.execve(args[0],args,environment(p,component))
