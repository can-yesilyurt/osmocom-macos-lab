"""Generate private local configurations; RF is always locked at startup."""
import hashlib
import ipaddress
import json
import os
import re

from common import ROOT, load_profile, state_dir, write_json


def render():
    p = load_profile()
    state = state_dir()
    if any(c.isspace() for c in str(state)):
        raise ValueError("Choose OSMO_LAB_STATE without whitespace")
    socket_path = state / "run/pcu.sock"
    if len(os.fsencode(socket_path)) > 103:
        raise ValueError("PCU socket path exceeds macOS limits; choose a shorter OSMO_LAB_STATE")
    for name in ("configs","data","run","logs"):
        (state / name).mkdir(parents=True,exist_ok=True,mode=0o700)
    values = {k.upper():str(v) for k,v in p.items() if k != "ports"}
    values.update({k.upper():str(v) for k,v in p["ports"].items()})
    access = "cs+ps" if p["gprs_enabled"] else "cs"
    values["SUBSCRIBER_POLICY"] = f"subscriber-create-on-demand {p['msisdn_digits']} {access}" if p["auto_create_subscribers"] else "no subscriber-create-on-demand"
    values["DEVICE_ARGS_LINE"] = " dev-args " + p["device_args"] if p["device_args"] else ""
    values["PCU_SOCKET"] = str(socket_path)
    network = ipaddress.IPv4Network(p["data_network"])
    values["DATA_GATEWAY_CIDR"] = str(network.network_address + 1) + "/" + str(network.prefixlen)
    values["DNS_0"], values["DNS_1"] = p["dns_servers"]
    values["APN_FALLBACK"] = " apn * ggsn 0" if p["accept_any_apn"] else ""
    values["DATA_DIR"] = str(state / "data")
    values["GGSN_DATA_DIR"] = str(state / "data/ggsn")
    values["SGSN_DATA_DIR"] = str(state / "data/sgsn")
    values["PDCH_6"] = values["PDCH_7"] = "PDCH" if p["gprs_enabled"] else "TCH/F"
    gprs = ["gprs mode none"]
    if p["gprs_enabled"]:
        gprs = ["gprs mode gprs", "gprs routing area 1", "gprs network-control-order nc0",
                "gprs cell bvci 2", "gprs nsei 101", "gprs nsvc 0 nsvci 101",
                f"gprs nsvc 0 local udp port {p['ports']['pcu_ns']}",
                f"gprs nsvc 0 remote udp port {p['ports']['sgsn_ns']}",
                "gprs nsvc 0 remote ip 127.0.0.2"]
    values["GPRS_CONFIG"] = "\n".join("  " + line for line in gprs)
    templates = ["osmo-stp","osmo-bsc","osmo-hlr","osmo-msc","osmo-bts-trx","osmo-trx-lms"]
    if p["gprs_enabled"]:
        for side in ("ggsn","sgsn","probe"):
            (state / "data" / side).mkdir(parents=True,exist_ok=True,mode=0o700)
        templates += ["osmo-pcu", "osmo-sgsn", "osmo-ggsn"]
    hashes = {}

    def save(name,text,substitutions):
        def replace(match):
            return substitutions[match.group(1)]
        text = re.sub(r"@([A-Z0-9_]+)@",replace,text)
        if "@" in text:
            raise ValueError("Unresolved template token in " + name)
        dest = state / "configs" / (name+".cfg")
        dest.write_text(text)
        dest.chmod(0o600)
        hashes[dest.name] = hashlib.sha256(text.encode()).hexdigest()

    for name in templates:
        save(name,(ROOT / "templates" / (name+".cfg.in")).read_text(),values)
    mgw_template = (ROOT / "templates/osmo-mgw.cfg.in").read_text()
    for side in ("bsc","msc"):
        subs = dict(values)
        for key,suffix in [("MGW_VTY","vty"),("MGW_CTRL","ctrl"),("MGW_MGCP","mgcp"),("RTP_FIRST","rtp_first"),("RTP_LAST","rtp_last")]:
            subs[key] = str(p["ports"][f"mgw_{side}_{suffix}"])
        save("osmo-mgw-"+side,mgw_template,subs)
    write_json(state / "configs/manifest.json",{"profile":p,"files":hashes})
    print("Rendered configurations with rf_locked 1: " + str(state / "configs"))
    return p


def require_config():
    p = load_profile()
    manifest = state_dir() / "configs/manifest.json"
    if not manifest.is_file() or json.loads(manifest.read_text())["profile"] != p:
        raise ValueError("Profile changed or not configured; run ./lab configure")
    return p
