"""Temporary macOS GGSN tunnel, IPv4 forwarding and scoped PF NAT.

Start/refresh/stop require root. No persistent system configuration is installed.
The main PF ruleset is never loaded, flushed or disabled by this helper.
"""
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import signal
import socket
import subprocess
import time

from common import prefix, state_dir, write_json
from configure import require_config
from vty import ready

ANCHOR = "com.apple/osmocom-lab"


def run(args, check=True, **kwargs):
    result = subprocess.run(list(map(str, args)), capture_output=True, text=True, **kwargs)
    if check and result.returncode:
        raise RuntimeError(shlex.join(map(str,args)) + ": " + result.stderr.strip())
    return result.stdout + result.stderr


def default_interface():
    text = run(["/sbin/route", "-n", "get", "default"])
    match = re.search(r"^\s*interface:\s*([a-z]+[0-9]+)\s*$", text, re.M)
    if not match or match[1] == "lo0":
        raise ValueError("No usable default IPv4 interface")
    return match[1]


def interfaces():
    blocks = re.split(r"(?=^[A-Za-z][A-Za-z0-9]*: flags=)", run(["/sbin/ifconfig"]), flags=re.M)
    return {block.split(":",1)[0]: block for block in blocks if ": flags=" in block}


def reject_overlap(network, blocks):
    for name, text in blocks.items():
        for address, mask in re.findall(r"\binet ([0-9.]+).*?netmask (0x[0-9a-f]+)", text):
            netmask = str(ipaddress.IPv4Address(int(mask,16)))
            existing = ipaddress.IPv4Network(address+"/"+netmask, strict=False)
            if network.overlaps(existing):
                raise ValueError(f"Data subnet overlaps {name}: {existing}")


def pf_rules(network, tunnel, uplink, mtu):
    if any(not re.fullmatch(r"[a-z]+[0-9]+", value) for value in (tunnel,uplink)):
        raise ValueError("Invalid interface name")
    if not tunnel.startswith("utun") or tunnel == uplink:
        raise ValueError("Expected a distinct GGSN utun interface")
    net = ipaddress.IPv4Network(network,strict=True)
    return (f"scrub on {tunnel} inet all max-mss {max(536,mtu-40)}\n"
            f"nat on {uplink} inet from {net} to any -> ({uplink})\n")


def owned_ggsn(record):
    pid = record.get("ggsn_pid")
    if type(pid) is not int or pid <= 1:
        return False
    result = subprocess.run(["ps","-p",str(pid),"-o","command="],capture_output=True,text=True)
    args = shlex.split(result.stdout.strip())
    expected = [str(prefix("packet") / "bin/osmo-ggsn"), "-c",
                str(state_dir() / "configs/osmo-ggsn.cfg")]
    return result.returncode == 0 and args == expected


def save_record(record):
    path = state_dir() / "run/network.json"
    temporary = path.with_suffix(".tmp")
    write_json(temporary,record)
    temporary.chmod(0o600)
    uid = int(record["uid"])
    gid = int(record["gid"])
    os.chown(temporary,uid,gid)
    temporary.replace(path)


def cleanup(record):
    if record.get("anchor") != ANCHOR:
        raise ValueError("Unexpected PF anchor in network record")
    failures = []
    # Remove only this anchor's rules; preserve every other PF user and state.
    if record.get("anchor_loaded"):
        result = subprocess.run(["/sbin/pfctl","-a",ANCHOR,"-f","/dev/null"],capture_output=True,text=True)
        if result.returncode:
            failures.append("Could not clear lab PF anchor: " + result.stderr.strip())
    if owned_ggsn(record):
        os.kill(record["ggsn_pid"],signal.SIGTERM)
        deadline = time.monotonic()+8
        while owned_ggsn(record) and time.monotonic()<deadline:
            time.sleep(.1)
        if owned_ggsn(record):
            os.kill(record["ggsn_pid"],signal.SIGKILL)
    if record.get("forwarding_changed"):
        old = record.get("previous_forwarding")
        if old not in ("0","1"):
            raise ValueError("Invalid saved IPv4 forwarding value")
        run(["/usr/sbin/sysctl","-w","net.inet.ip.forwarding="+old])
    if record.get("pf_token"):
        token = str(record["pf_token"])
        if not token.isascii() or not token.isdigit():
            raise ValueError("Invalid PF reference token")
        run(["/sbin/pfctl","-X",token])
    if record.get("loopback_added"):
        run(["/sbin/ifconfig","lo0","-alias","127.0.0.2"])
    if failures:
        raise RuntimeError("; ".join(failures))
    (state_dir() / "run/network.json").unlink(missing_ok=True)


def start(p):
    state = state_dir()
    if (state / "run/network.json").exists():
        raise ValueError("Network state already exists; inspect status or stop it before restarting")
    if not p["gprs_enabled"]:
        raise ValueError("Enable gprs_enabled and render the profile first")
    executable = prefix("packet") / "bin/osmo-ggsn"
    config = state / "configs/osmo-ggsn.cfg"
    manifest = json.loads((state / "configs/manifest.json").read_text())
    if hashlib.sha256(config.read_bytes()).hexdigest() != manifest["files"][config.name]:
        raise ValueError("GGSN configuration differs from its rendered manifest")
    if not executable.is_file():
        raise ValueError("Build packet components with ./lab build --with-gprs first")
    for port in (p["ports"]["ggsn_vty"],p["ports"]["ggsn_ctrl"]):
        if ready(port):
            raise ValueError(f"GGSN TCP port {port} is already occupied")
    for port in (2123,2152,3386):
        with socket.socket(socket.AF_INET,socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1",port))
    blocks = interfaces()
    network = ipaddress.IPv4Network(p["data_network"])
    reject_overlap(network,blocks)
    uplink = default_interface()
    mtu_match = re.search(r"\bmtu ([0-9]+)",blocks[uplink])
    mtu = min(p["data_mtu"],int(mtu_match[1]))
    if mtu < 576:
        raise ValueError("Default uplink MTU is too small for this IPv4 profile")
    # Check live hooks, not just the on-disk file. Never replace the main rules.
    nat = run(["/sbin/pfctl","-sn"])
    filtering = run(["/sbin/pfctl","-sr"])
    if 'nat-anchor "com.apple/*"' not in nat or 'scrub-anchor "com.apple/*"' not in filtering:
        raise ValueError("The live PF rules lack the Apple wildcard hooks; no rules were changed")
    if run(["/sbin/pfctl","-a",ANCHOR,"-sn"]).strip():
        # Ignore the standard ALTQ diagnostic, which is printed on stderr.
        text = subprocess.run(["/sbin/pfctl","-a",ANCHOR,"-sn"],capture_output=True,text=True).stdout
        if text.strip():
            raise ValueError("The lab PF anchor is already occupied")
    record = {"anchor":ANCHOR,"uid":int(os.environ.get("SUDO_UID",state.stat().st_uid)),
              "gid":int(os.environ.get("SUDO_GID",state.stat().st_gid)),
              "uplink":uplink,"data_network":str(network),"status":"starting",
              "sgsn_vty":p["ports"]["sgsn_vty"],
              "previous_forwarding":run(["/usr/sbin/sysctl","-n","net.inet.ip.forwarding"]).strip()}
    save_record(record)
    child = None
    try:
        if not re.search(r"\binet 127\.0\.0\.2\s",blocks.get("lo0","")):
            record["loopback_added"] = True
            save_record(record)
            run(["/sbin/ifconfig","lo0","alias","127.0.0.2"])
        log = state / "logs/ggsn-network.log"
        env = {k:v for k,v in os.environ.items() if not k.startswith("DYLD_")}
        env["TZ"] = "UTC"
        with log.open("wb") as stream:
            os.chown(log,record["uid"],record["gid"])
            child = subprocess.Popen([str(executable),"-c",str(config)],cwd=state / "data/ggsn",
                                     env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
        record["ggsn_pid"] = child.pid
        save_record(record)
        gateway = str(network.network_address+1)
        deadline = time.monotonic()+10
        tunnel = None
        while time.monotonic()<deadline:
            if child.poll() is not None:
                raise RuntimeError("GGSN exited; inspect " + str(log))
            matching = [name for name,text in interfaces().items()
                        if re.search(r"\binet "+re.escape(gateway)+r"\s",text)]
            if len(matching)==1 and ready(p["ports"]["ggsn_vty"]):
                tunnel = matching[0]
                break
            time.sleep(.1)
        if not tunnel:
            raise RuntimeError("GGSN did not create the expected IPv4 tunnel; inspect its log")
        record["tunnel"] = tunnel
        rules = pf_rules(str(network),tunnel,uplink,mtu)
        rules_path = state / "run/pf-gprs.conf"
        rules_path.write_text(rules)
        rules_path.chmod(0o600)
        os.chown(rules_path,record["uid"],record["gid"])
        run(["/sbin/pfctl","-a",ANCHOR,"-n","-f",str(rules_path)])
        if record["previous_forwarding"] != "1":
            record["forwarding_changed"] = True
            save_record(record)
            run(["/usr/sbin/sysctl","-w","net.inet.ip.forwarding=1"])
        output = run(["/sbin/pfctl","-E"])
        token = re.search(r"Token\s*:\s*([0-9]+)",output)
        if not token:
            raise RuntimeError("PF did not return an enable-reference token")
        record["pf_token"] = token[1]
        save_record(record)
        record["anchor_loaded"] = True
        save_record(record)
        run(["/sbin/pfctl","-a",ANCHOR,"-f",str(rules_path)])
        if default_interface()!=uplink:
            raise RuntimeError("Default internet route changed during setup")
        record["status"] = "ready"
        save_record(record)
        print(json.dumps({"status":"ready","data_tunnel":tunnel,"internet_interface":uplink,
                          "data_network":str(network),"apn":p["apn"],"rf_enabled":False},indent=2))
    except BaseException:
        cleanup(record)
        if child:
            child.wait(timeout=5)
        raise


def refresh():
    """Follow a changed default route without restarting GGSN or the radio."""
    state = state_dir()
    path = state / "run/network.json"
    if not path.is_file():
        raise ValueError("Start the GPRS internet gateway first")
    record = json.loads(path.read_text())
    if (record.get("anchor") != ANCHOR or record.get("status") != "ready"
            or not record.get("anchor_loaded") or not owned_ggsn(record)):
        raise ValueError("No ready GGSN/PF gateway owned by this checkout")
    network = ipaddress.IPv4Network(record["data_network"],strict=True)
    if not 24 <= network.prefixlen <= 29 or not any(network.subnet_of(ipaddress.IPv4Network(n))
            for n in ("10.0.0.0/8","172.16.0.0/12","192.168.0.0/16")):
        raise ValueError("Invalid saved lab data subnet")
    blocks = interfaces()
    tunnel = record["tunnel"]
    gateway = str(network.network_address+1)
    if not re.search(r"\binet "+re.escape(gateway)+r"\s",blocks.get(tunnel,"")):
        raise ValueError("The owned GGSN tunnel no longer has its expected address")
    uplink = default_interface()
    mtu_matches = [re.search(r"\bmtu ([0-9]+)",blocks.get(name,"")) for name in (tunnel,uplink)]
    if not all(mtu_matches):
        raise ValueError("Cannot determine GGSN and internet interface MTUs")
    mtu = min(int(match[1]) for match in mtu_matches)
    if mtu < 576:
        raise ValueError("Default uplink MTU is too small for this IPv4 profile")
    nat = run(["/sbin/pfctl","-sn"])
    filtering = run(["/sbin/pfctl","-sr"])
    if 'nat-anchor "com.apple/*"' not in nat or 'scrub-anchor "com.apple/*"' not in filtering:
        raise ValueError("The live PF rules lack the Apple wildcard hooks; no rules were changed")
    if run(["/usr/sbin/sysctl","-n","net.inet.ip.forwarding"]).strip() != "1":
        raise ValueError("IPv4 forwarding is no longer enabled; inspect the gateway before refreshing")
    rules_path = state / "run/pf-gprs.conf"
    if not rules_path.is_file():
        raise ValueError("The previous lab PF rules are missing; no rules were changed")
    candidate = state / "run/pf-gprs-refresh.conf"
    candidate.write_text(pf_rules(str(network),tunnel,uplink,mtu))
    candidate.chmod(0o600)
    os.chown(candidate,int(record["uid"]),int(record["gid"]))
    loaded = False
    try:
        run(["/sbin/pfctl","-a",ANCHOR,"-n","-f",str(candidate)])
        run(["/sbin/pfctl","-a",ANCHOR,"-f",str(candidate)])
        loaded = True
        if default_interface() != uplink:
            raise RuntimeError("Default internet route changed during refresh")
        updated = dict(record,uplink=uplink)
        save_record(updated)
        candidate.replace(rules_path)
    except BaseException:
        if loaded:
            run(["/sbin/pfctl","-a",ANCHOR,"-f",str(rules_path)])
            save_record(record)
        raise
    finally:
        candidate.unlink(missing_ok=True)
    # Existing NAT states can retain the old interface's translated address.
    # Reset only flows originating in the owned lab pool, not the Mac's flows.
    run(["/sbin/pfctl","-k",str(network)])
    print(json.dumps({"status":"ready","data_tunnel":tunnel,
                      "previous_internet_interface":record["uplink"],
                      "internet_interface":uplink,"data_network":str(network),
                      "lab_connection_states_reset":True,"ggsn_restarted":False},indent=2))


def status():
    path = state_dir() / "run/network.json"
    if not path.exists():
        print("GPRS internet gateway is not running")
        return 1
    record = json.loads(path.read_text())
    alive = owned_ggsn(record)
    current = default_interface()
    unchanged = current==record.get("uplink")
    print(json.dumps({"status":record.get("status") if unchanged else "needs_refresh","ggsn_running":alive,
                      "data_tunnel":record.get("tunnel"),"internet_interface":record.get("uplink"),
                      "current_internet_interface":current,"default_route_unchanged":unchanged,
                      "ipv4_forwarding":run(["/usr/sbin/sysctl","-n","net.inet.ip.forwarding"]).strip()},indent=2))
    return 0 if alive and unchanged else 1


def network(action):
    if action=="status":
        return status()
    if os.geteuid()!=0:
        raise ValueError("Network start/refresh/stop require administrator authentication: sudo ./lab network "+action)
    os.umask(0o077)
    p = require_config() if action=="start" else None
    if action=="stop" and not (state_dir() / "run/network.json").exists():
        print("No owned GPRS gateway to stop")
        return 0
    with (state_dir() / "run/network.lock").open("a+") as lock:
        fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB)
        if action=="start":
            start(p)
        elif action=="refresh":
            refresh()
        elif action=="stop":
            path = state_dir() / "run/network.json"
            if not path.exists():
                print("No owned GPRS gateway to stop")
                return 0
            record = json.loads(path.read_text())
            if ready(record.get("sgsn_vty",4245)):
                raise ValueError("Stop the core/SGSN before removing its network tunnel")
            cleanup(record)
            print("GPRS gateway stopped; prior forwarding and PF reference restored")
        else:
            raise ValueError("Network action must be start, refresh, stop or status")
    return 0
