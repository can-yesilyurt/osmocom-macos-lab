# Operating the lab

All commands below run from the checkout root. Use the same `OSMO_LAB_STATE`
and `OSMO_LAB_PROFILE` in every terminal, or leave both unset for the defaults
`.lab/` and `lab.json`. The voice services, management ports and media interfaces
bind to loopback. The optional PCU's NS UDP listener uses a wildcard address;
see [GPRS bindings and setup](GPRS.md). This recipe runs all components on one Mac.

## Local profile

Copy `lab.example.json` to the ignored `lab.json` and edit it before rendering.
MCC and MNC are strings so that leading zeros and two/three-digit MNC length are
preserved. For example, `"mcc": "520", "mnc": "999"` reproduces the identifier
used during bring-up; it does not document an assigned network identity.

Choose the frequency, identifiers and RF arrangement for your authorized or
contained test. ARFCN 25 in GSM900 corresponds to 895 MHz uplink and 940 MHz
downlink. A quiet spectrum scan alone does not establish permission to transmit.
Other P-GSM900 channels in this release use `uplink_MHz = 890 + 0.2 * ARFCN`
and `downlink_MHz = uplink_MHz + 45` for ARFCNs 1–124.

`tx_path: "BAND1"` and `rx_path: "LNAL"` matched the validated cabling. Verify
the board's actual connector markings; an enclosure label such as RX1/TX1 does
not identify the internal LNA path. `device_args` selects a particular board
when needed. Keep its serial in the private profile.

`max_power_reduction_db` is an even integer from 0 to 24. The validated setting
was 20, with `osmotrx tx-attenuation oml` in the BTS. BSC's nominal-power display
and LimeSuite's estimated output are different quantities. Neither is a
calibrated antenna-port power measurement; do not infer range or containment
from those numbers. `rx_gain` defaults to 30.

After profile changes, stop the running lab, run `./lab configure`, and restart
it. The launcher refuses a profile whose rendered manifest is out of date.
Configure always writes a locked BSC startup file. Keep persistent changes in
`lab.json`; a later render replaces the generated `.cfg` files.

## Startup, scan and shutdown

1. If a VM previously owned the LimeSDR, disconnect the USB device from that VM
   and connect it to macOS. Only one program can own the radio at a time.
2. In three terminals run `./lab core`, `./lab trx`, and `./lab bts`.
3. In a fourth terminal use `./lab status`. After the core, TRX and BTS links
   settle, explicitly run `./lab rf on`.
4. Run a fresh manual network scan on each 2G-capable test handset. Carrier
   selection policy and SIM/operator restrictions can affect what it displays.
5. Finish with `./lab rf off`, then Ctrl+C in BTS, TRX and core terminals.

`rf on` checks BSSMAP, RSL and MSC/HLR GSUP before unlocking the carrier. It
changes the current VTY state only. `rf off` locks/mutes RF while streams may
remain allocated. Stopping the three foreground groups closes the processes and
radio. The core launcher stops only the children it started. It refuses occupied
ports, and RF commands check that this checkout owns the BSC process.

`./lab core --check` is a temporary core startup test, with no radio or BTS. It
exits after the links connect and children stop. If a core is already running,
use `./lab status`; a second launcher intentionally fails without replacing it.

If the core crashes while TRX/BTS remain alive, stop the radio groups before
restarting. RF locking is an administrative control, not a physical RF isolation
mechanism or a calibrated guarantee of zero leakage.

## Subscribers, numbers and test communication

The default HLR policy is `subscriber-create-on-demand 5 cs`: unknown SIMs may
register and receive a local five-digit MSISDN. Authentication is optional and
air-interface encryption is A5/0. This admits test phones without loading SIM
keys. It does not establish a subscriber's real operator phone number. The local
number is used only inside this lab.

To inspect/change local numbers, open the HLR VTY from a new shell. If Telnet is
not installed, install it with `brew install telnet`.

```sh
telnet 127.0.0.1 4258
```

Then use the VTY commands below. Substitute a **local** IMSI printed by your HLR;
the angle-bracket placeholder is not literal input.

```text
enable
show subscribers all
subscriber imsi <LOCAL_IMSI> update msisdn 1001
```

Use a different local number such as `1002` for the second phone. Avoid duplicate
MSISDNs. If the MSC still holds an older value, have the affected phone detach
and register again, then check the MSC VTY at port 4254:

```text
enable
show subscriber cache
```

Call or send SMS between the two lab MSISDNs. The internal MNCC handler routes
local calls; there is no public telephone network or emergency calling service
in this profile. `OSMO-TEST` is the configured network name, not an alphanumeric
voice caller ID. SMPP is compiled in but its listener uses a closed policy and
has no configured external clients.

Set `auto_create_subscribers` to `false` before configuring if you intend to
provision subscribers explicitly. This only disables automatic creation; it
does not enable authentication or encryption. A private/authenticated network
requires a separately designed and validated SIM provisioning configuration.

## Ports and diagnostics

| Interface | Default |
| --- | --- |
| TRX VTY / CTRL | TCP 4237 / 4236 |
| BTS VTY / CTRL | TCP 4241 / 4238 |
| BSC VTY / CTRL | TCP 4242 / 4249 |
| STP VTY / M3UA | TCP 4239 / SCTP 2905 |
| MSC VTY / CTRL | TCP 4254 / 4255 |
| HLR VTY / CTRL / GSUP | TCP 4258 / 4259 / 4222 |
| IPA OML / RSL | TCP 3002 / 3003 |
| BSC-side MGW VTY / CTRL / MGCP | TCP 4243 / 4267 / UDP 2427 |
| MSC-side MGW VTY / CTRL / MGCP | TCP 4244 / 4268 / UDP 2428 |
| BSC / MSC MGCP client | UDP 2727 / 2728 |
| BSC / MSC / STP SCTP encapsulation | UDP 9897 / 9898 / 9899 |
| TRX / BTS base | UDP 15700 / 15800; each also uses base+1 and base+2 |
| BSC / MSC MGW RTP ranges | UDP 4002–5999 / 6002–7999 |
| SMPP | TCP 2775, closed policy |

Most ports can be overridden through `ports` in the local profile; see
`scripts/common.py` for the exact keys. GSUP and IPA ports remain fixed in this
single-host recipe, so port overrides do not support multiple simultaneous labs.

Core logs are in `logs/runtime/<timestamp>/` under the state directory. TRX and
BTS write to their terminals. Subscriber data is in `data/hlr.db`; the MSC SMS
store is `data/sms.db`. Preserve those private files when continuity matters.
Back them up only after stopping their processes. Do not publish them.

For a startup problem, record which command failed and inspect the corresponding
private log. A TRX that is active but has no BTS `POWERON` is not yet advertising a
cell. Check BSSMAP, RSL, carrier administrative state and OML attenuation. Do not
solve a missing network by repeatedly raising transmit power.
