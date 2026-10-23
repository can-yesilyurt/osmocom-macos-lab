"""Paths and profile validation shared by the build and foreground launchers."""
from pathlib import Path
import json
import ipaddress
import os
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PORTS = {
    "stp_vty": 4239, "m3ua": 2905, "stp_encap": 9899,
    "bsc_vty": 4242, "bsc_ctrl": 4249, "bsc_encap": 9897,
    "mgw_bsc_vty": 4243, "mgw_bsc_ctrl": 4267, "mgw_bsc_mgcp": 2427,
    "bsc_mgcp_client": 2727, "mgw_bsc_rtp_first": 4002, "mgw_bsc_rtp_last": 5999,
    "mgw_msc_vty": 4244, "mgw_msc_ctrl": 4268, "mgw_msc_mgcp": 2428,
    "msc_mgcp_client": 2728, "mgw_msc_rtp_first": 6002, "mgw_msc_rtp_last": 7999,
    "hlr_vty": 4258, "hlr_ctrl": 4259,
    "msc_vty": 4254, "msc_ctrl": 4255, "msc_encap": 9898, "smpp": 2775,
    "bts_vty": 4241, "bts_ctrl": 4238, "trx_vty": 4237, "trx_ctrl": 4236,
    "trx_base": 15700, "bts_base": 15800,
    "pcu_vty": 4240, "sgsn_vty": 4245, "sgsn_ctrl": 4251,
    "ggsn_vty": 4260, "ggsn_ctrl": 4257, "sgsn_ns": 23000, "pcu_ns": 23001,
}


def state_dir():
    return Path(os.environ.get("OSMO_LAB_STATE", ROOT / ".lab")).expanduser().resolve()


def prefix(group):
    return state_dir() / "local" / group


def load_profile(path=None):
    path = Path(path or os.environ.get("OSMO_LAB_PROFILE", ROOT / "lab.json"))
    if not path.is_file():
        raise ValueError("Copy lab.example.json to lab.json and edit the local settings first")
    supplied = json.loads(path.read_text())
    defaults = json.loads((ROOT / "lab.example.json").read_text())
    if not isinstance(supplied, dict) or set(supplied) - set(defaults):
        raise ValueError("Unknown profile fields; see lab.example.json")
    defaults.update(supplied)
    p = defaults
    for key in ("mcc", "mnc"):
        if not isinstance(p[key], str) or not re.fullmatch(r"[0-9]{3}" if key == "mcc" else r"[0-9]{2,3}", p[key]):
            raise ValueError(key + " must be a string of the correct digit length")
    if p["band"] != "GSM900" or type(p["arfcn"]) is not int or not 1 <= p["arfcn"] <= 124:
        raise ValueError("This release supports the validated GSM900 profile, ARFCNs 1-124")
    for key, lo, hi in (("lac",1,65533),("cell_id",0,65535),("bsic",0,63),
                        ("rx_gain",0,73),("max_power_reduction_db",0,24),("msisdn_digits",3,15)):
        if type(p[key]) is not int or not lo <= p[key] <= hi:
            raise ValueError(f"{key} must be an integer in {lo}..{hi}")
    if p["max_power_reduction_db"] % 2:
        raise ValueError("max_power_reduction_db must be even (the BSC uses 2 dB steps)")
    if type(p["auto_create_subscribers"]) is not bool:
        raise ValueError("auto_create_subscribers must be true or false")
    if type(p["gprs_enabled"]) is not bool:
        raise ValueError("gprs_enabled must be true or false")
    if type(p["accept_any_apn"]) is not bool:
        raise ValueError("accept_any_apn must be true or false")
    if not isinstance(p["apn"], str) or len(p["apn"]) > 63 or not re.fullmatch(r"[a-z0-9]+(?:[-.][a-z0-9]+)*", p["apn"]):
        raise ValueError("apn must be a lowercase APN of at most 63 characters")
    if not isinstance(p["data_network"], str):
        raise ValueError("data_network must be an IPv4 CIDR string")
    network = ipaddress.IPv4Network(p["data_network"], strict=True)
    private = [ipaddress.IPv4Network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
    if not 24 <= network.prefixlen <= 29 or not any(network.subnet_of(n) for n in private):
        raise ValueError("data_network must be an RFC1918 IPv4 subnet with prefix length 24..29")
    if not isinstance(p["dns_servers"], list) or len(p["dns_servers"]) != 2:
        raise ValueError("dns_servers must contain two IPv4 DNS addresses")
    for value in p["dns_servers"]:
        address = ipaddress.IPv4Address(value)
        if not isinstance(value, str) or address.is_multicast or address.is_unspecified or address.is_loopback:
            raise ValueError("Invalid IPv4 DNS address")
    if type(p["data_mtu"]) is not int or not 576 <= p["data_mtu"] <= 1500:
        raise ValueError("data_mtu must be an integer in 576..1500")
    if not isinstance(p["operator_name"], str) or not re.fullmatch(r"[A-Za-z0-9 _-]{1,16}", p["operator_name"]):
        raise ValueError("operator_name must contain 1-16 ASCII letters, digits, spaces, _ or -")
    if not isinstance(p["device_args"], str) or any(c in p["device_args"] for c in "\n\r\0"):
        raise ValueError("device_args must be a single line")
    if p["tx_path"] not in ("BAND1", "BAND2") or p["rx_path"] not in ("LNAL","LNAH","LNAW"):
        raise ValueError("Unsupported LimeSDR antenna path")
    overrides = p["ports"]
    if not isinstance(overrides, dict) or set(overrides) - set(DEFAULT_PORTS):
        raise ValueError("Unknown port override")
    ports = {**DEFAULT_PORTS, **overrides}
    if any(type(v) is not int or not 1024 <= v <= 65532 for v in ports.values()):
        raise ValueError("Ports must be integers in 1024..65532")
    # Enforce uniqueness including the derived TRX control/data UDP ports.
    # GSUP and the IPA OML/RSL listeners are fixed by this single-host profile.
    tcp = [4222, 3002, 3003] + [v for k,v in ports.items() if k.endswith(("_vty","_ctrl")) or k == "smpp"]
    udp_keys = ["stp_encap","bsc_encap","msc_encap","mgw_bsc_mgcp","mgw_msc_mgcp","bsc_mgcp_client","msc_mgcp_client"]
    udp = [ports[k] for k in udp_keys] + [ports[k]+i for k in ("trx_base","bts_base") for i in range(3)]
    if p["gprs_enabled"]:
        udp += [2123, 2152, 3386, ports["sgsn_ns"], ports["pcu_ns"]]
    if len(tcp) != len(set(tcp)) or len(udp) != len(set(udp)):
        raise ValueError("Conflicting management or UDP ports")
    ranges = [(ports[f"mgw_{s}_rtp_first"], ports[f"mgw_{s}_rtp_last"]) for s in ("bsc","msc")]
    for first,last in ranges:
        if first % 2 or last <= first or any(first <= v <= last for v in udp):
            raise ValueError("Invalid/overlapping RTP range")
    if max(ranges[0][0],ranges[1][0]) <= min(ranges[0][1],ranges[1][1]):
        raise ValueError("Media gateway RTP ranges overlap")
    p["ports"] = ports
    return p


def brew_prefix():
    return Path(subprocess.check_output(["brew", "--prefix"], text=True).strip())


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
