"""Exercise the GGSN and Mac internet path using synthetic libgtp traffic.

This does not exercise the radio, PCU, real SGSN or handset. It requires the
gateway to be running and the real SGSN stopped so the loopback GTP ports are free.
"""
import json
import ipaddress
import os
import re
import socket
import subprocess

from common import ROOT,brew_prefix,prefix,state_dir,write_json
from configure import require_config
from network import owned_ggsn
from vty import ready


def parse_output(text, network):
    allocated = re.search(r"PDP ctx: received EUA with IP address: ([0-9.]+)",text)
    received = re.search(r"([0-9]+) (?:packets )?received",text,re.I)
    accepted = bool(allocated and ipaddress.IPv4Address(allocated[1]) in ipaddress.IPv4Network(network))
    return accepted, int(received[1]) if received else None


def require_gateway():
    p = require_config()
    state = state_dir()
    if not p["gprs_enabled"] or not (state / "run/network.json").is_file():
        raise ValueError("Start the GPRS internet gateway first")
    record = json.loads((state / "run/network.json").read_text())
    if record.get("status")!="ready" or not owned_ggsn(record):
        raise ValueError("Start the GPRS internet gateway first")
    if ready(p["ports"]["sgsn_vty"]):
        raise ValueError("Stop the real core/SGSN before running the emulator probe")
    return p,state


def parse_http_output(text):
    result = re.search(r"^RESULT accepted=([01]) dns_replies=([0-9]+) tcp=([01]) http=([01]) http_status=([0-9]+)$",text,re.M)
    if not result:
        return {"context_accepted":False,"dns_replies":0,"tcp_connected":False,
                "http_response":False,"http_status":None}
    return {"context_accepted":result[1]=="1","dns_replies":int(result[2]),
            "tcp_connected":result[3]=="1","http_response":result[4]=="1",
            "http_status":int(result[5]) or None}


def build_http_probe(state):
    env = os.environ.copy()
    brew = brew_prefix()
    env["PKG_CONFIG_PATH"] = ":".join(str(p) for p in [prefix("packet") / "lib/pkgconfig",
                prefix("bsc") / "lib/pkgconfig",brew / "lib/pkgconfig",brew / "share/pkgconfig"])
    flags = subprocess.check_output(["pkg-config","--cflags","--libs","libgtp","libosmocore"],env=env,text=True).split()
    executable = state / "run/gprs-internet-probe"
    log = state / "logs/packet-probe-build.log"
    with log.open("wb") as out:
        result = subprocess.run(["clang","-Wall","-Wextra",str(ROOT / "tests/gprs_internet_probe.c"),
                     "-o",str(executable),*flags],env=env,stdout=out,stderr=subprocess.STDOUT,timeout=30)
    if result.returncode:
        raise RuntimeError("Could not build the gateway probe; inspect " + str(log))
    return executable


def probe_http(host):
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?",host):
        raise ValueError("Invalid HTTP probe hostname")
    p,state = require_gateway()
    os.umask(0o077)
    executable = build_http_probe(state)
    address = socket.gethostbyname(host)
    log = state / "logs/packet-http-probe.log"
    timed_out = False
    with log.open("wb") as out:
        try:
            process = subprocess.run([str(executable),str(state / "data/probe"),p["apn"],
                         p["dns_servers"][0],address,host],stdout=out,stderr=subprocess.STDOUT,timeout=30)
            code = process.returncode
        except subprocess.TimeoutExpired:
            timed_out,code = True,-1
    result = parse_http_output(log.read_text(errors="replace"))
    passed = bool(not timed_out and code==0 and result["context_accepted"] and result["dns_replies"]>0
                  and result["tcp_connected"] and result["http_response"]
                  and result["http_status"] and 100<=result["http_status"]<=599)
    result.update({"probe":"native libgtp DNS and HTTP return path","return_code":code,
                   "timed_out":timed_out,"http_host":host,"passed":passed,
                   "http_success_status":bool(result["http_status"] and 200<=result["http_status"]<400),
                   "radio_tested":False,"handset_tested":False})
    write_json(state / "logs/packet-http-probe.json",result)
    print(json.dumps(result,indent=2))
    if not passed:
        print("Inspect the private probe and GGSN logs: " + str(log))
    return 0 if passed else 1


def probe(target="1.1.1.1",http=False,host="neverssl.com"):
    if http:
        return probe_http(host)
    target = str(ipaddress.IPv4Address(target))
    p,state = require_gateway()
    executable = prefix("packet") / "bin/sgsnemu"
    # Synthetic test identities only; no live subscriber database is read.
    imsi = "00101" + "0" * 9 + "1"
    args = [str(executable),"--listen=127.0.0.2","--remote=127.0.0.1","--contexts=1",
            "--timelimit=15","--gtpversion=1","--apn="+p["apn"],"--pdp-type=v4",
            "--imsi="+imsi,"--msisdn=1000","--imeisv="+("0"*15+"1"),
            "--statedir="+str(state / "data/probe"),
            "--pidfile="+str(state / "run/sgsnemu-probe.pid"),
            "--pinghost="+target,"--pingrate=1","--pingcount=3"]
    os.umask(0o077)
    log = state / "logs/packet-internet-probe.log"
    timed_out = False
    with log.open("wb") as out:
        process = subprocess.Popen(args,cwd=state / "data/probe",stdout=out,stderr=subprocess.STDOUT)
        try:
            # Upstream uses ten-second select sleeps and a separate disconnect
            # grace period after --timelimit, including when ICMP is blocked.
            process.wait(timeout=55)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    text = log.read_text(errors="replace")
    accepted, received = parse_output(text,p["data_network"])
    success = bool(not timed_out and process.returncode==0 and accepted and received and received>0)
    result = {"probe":"sgsnemu GTP-C/GTP-U through GGSN",
              "return_code":process.returncode,"timed_out":timed_out,"context_accepted":accepted,
              "icmp_replies":received,"internet_target":ipaddress.IPv4Address(target).is_global,
              "target":target,"passed":success,"radio_tested":False,"handset_tested":False}
    write_json(state / "logs/packet-internet-probe.json",result)
    print(json.dumps(result,indent=2))
    if not success:
        print("Inspect the private probe and GGSN logs: " + str(log))
        return 1
    return 0
