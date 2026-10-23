# Optional native GPRS internet gateway

This adds OsmoPCU, a 2G-only OsmoSGSN and OsmoGGSN to the voice/SMS lab. Start
with IPv4 GPRS. EDGE remains disabled in the transceiver. A build or emulator
probe does not establish real-handset data service; validate radio attachment,
PDP activation, DNS and HTTP separately.

## Build and profile

```sh
brew bundle --file=Brewfile
./lab build --with-gprs --jobs 6
```

If the existing voice stack is already built at the same pinned revisions:

```sh
./lab build --with-gprs --from ggsn --jobs 6
```

The three extra sources are pinned in `sources.lock.json`. GGSN, PCU and SGSN
install under `local/packet` in the state directory. PCU uses the SCTP-disabled
radio core; SGSN/GGSN use the core-side dependencies. SGSN is built with Iu/3G
disabled. The optional additional dependency is Homebrew `c-ares`.

Set `gprs_enabled` to `true` in the private profile and run `./lab configure`.
The default APN is `osmo`, address pool `10.45.0.0/24`, gateway `10.45.0.1`, DNS
`1.1.1.1` / `8.8.8.8`, and announced MTU 1280. Configure the test handset to use
the same APN with no username or password. Phones may hide the APN menu under
their SIM's carrier settings, so handset provisioning must also be checked.

If the SIM hides the APN menu, `accept_any_apn: true` adds an SGSN wildcard
mapping. GGSN's configured default APN then routes other requested APN names
into the lab's `osmo` gateway. The phone can retain its existing APN for this
test. This option defaults to `false`; subscription/PS access still comes from
the lab HLR. A phone requesting an unsupported PDP address family may still
need an IPv4 APN profile. Apple documents both
[carrier-dependent APN menus](https://support.apple.com/en-ie/102483) and the
[manually installable Cellular profile](https://developer.apple.com/documentation/devicemanagement/cellular).

Timeslots 6 and 7 become PDCH, leaving four full-rate voice traffic slots. New
on-demand HLR subscribers receive CS+PS access. Existing subscribers retain
their stored access flags; enable CS+PS explicitly in the HLR VTY if needed:

```text
subscriber imsi <LOCAL_IMSI> update network-access-mode cs+ps
```

The SGSN uses HLR subscription data, optional authentication and GEA0, matching
the lab's keyless test-SIM policy. This does not turn it into an authenticated
private network. Generated BSC configurations still start with RF locked.

## Temporary Mac network setup

Run from the checkout, with the same profile/state environment in every shell.
Root access is required for the GGSN tunnel and host forwarding/NAT:

```sh
sudo ./lab network start
./lab network status
```

For a custom state/profile, pass `OSMO_LAB_STATE` / `OSMO_LAB_PROFILE` explicitly
through `sudo env`, or invoke the root helper with an environment that preserves
them. Otherwise it uses this checkout's `.lab/` and `lab.json`.

The helper checks subnet overlap and existing ports, creates loopback alias
`127.0.0.2` only if absent, launches its own GGSN, discovers the actual `utunN`,
and enables IPv4 forwarding. It applies NAT only to the configured data subnet
through the Mac's current default IPv4 interface. If that is a VPN tunnel, that
tunnel is used; the helper does not select a physical interface to bypass it.
If the default route or VPN changes, `network status` reports `needs_refresh`.
Follow the new default interface without restarting GGSN, SGSN or the radio:

```sh
sudo ./lab network refresh
./lab network status
```

Refresh replaces only the lab anchor's rules and resets connection states
originating in the lab's data pool, so retry the page afterward. It preserves
the GGSN tunnel, phone PDP sessions, PF reference and forwarding recovery record.

PF rules live only in `com.apple/osmocom-lab`, referenced through the existing
Apple wildcard hooks. The helper never loads, flushes or disables the main PF
ruleset. It acquires its own PF enable-reference token and releases only that
token on stop. TCP MSS normalization applies only to the new GGSN tunnel.
No persistent PF file, launch daemon, sudoers rule or default route is installed.
The previous forwarding setting is saved for restoration. Startup failures
attempt rollback; the private `run/network.json` is the recovery record.

Before starting the real SGSN, test the gateway with the upstream SGSN emulator:

```sh
./lab packet-probe --http
```

The HTTP check compiles the included small native `libgtp` probe. It creates a
synthetic PDP context, queries `example.com` through the profile's first DNS
server, then attempts a TCP/HTTP exchange with `neverssl.com`. The Mac resolves
the HTTP destination separately. Use `--host example.com` to select another HTTP
destination. The result separates transport reachability from the HTTP status:
a server error such as 403 proves a response path but does not count as a
successful page status. This is a small exchange, not a full TCP, TLS or
throughput test. It does not test PCU, the real SGSN, radio or a handset.

The upstream SGSN emulator's ICMP test is also available:

```sh
./lab packet-probe --target 1.1.1.1
```

An ICMP failure alone is insufficient to diagnose internet access. In the local
bring-up, ICMP received no replies while DNS and TCP/HTTP exchanges succeeded
with the original Mac firewall settings. No firewall-mode change is made by
either probe. Raw probe logs stay private.

## Bring up the radio

With the gateway running, use separate terminals:

```sh
./lab core
./lab trx
./lab bts
./lab pcu
```

The core starts the SGSN after the voice components and checks both MSC/HLR and
SGSN/HLR connections. PCU connects to the BTS's private Unix socket, uses NSEI
101 / BVCI 2, and exchanges NS/BSSGP traffic with SGSN on `127.0.0.2:23000`.
The PCU's local NS UDP port is 23001.
The pinned PCU derives a wildcard `0.0.0.0:23001` local bind from the BTS
information; its configured SGSN peer is loopback. Management VTY listeners and
the SGSN NS listener remain loopback-bound. The packet profile therefore does
not make the same all-loopback binding claim as the voice profile.

```sh
./lab packet-status
./lab rf on
```

On the phone, disable Wi-Fi, enable cellular data and, when required by the
test SIM/network selection, data roaming. Re-register the phone, then verify a
PDP context and a small internet request. Calls/SMS and data need independent
acceptance checks. GPRS offers limited shared capacity; do not use a modern
page's slow load as the sole connectivity test.

## Shutdown and recovery

Run `./lab rf off`. Stop PCU, BTS, TRX and core with Ctrl+C, then:

```sh
sudo ./lab network stop
```

Stop removes only the owned GGSN, its PF anchor, its PF reference, and a loopback
alias it created. It restores the previous forwarding value. It refuses to
remove the gateway while SGSN is still active. Network stop works from its
saved recovery record even if the local profile has subsequently changed.

Keep `lab.json`, all databases, logs and network recovery records private.
VTY ports are PCU 4240, SGSN 4245 and GGSN 4260. GTP-C/U bind to the configured
loopback peers (`127.0.0.2` for SGSN, `127.0.0.1` for GGSN). The GGSN and SGSN
use separate restart-counter directories under `data/ggsn` and `data/sgsn`.
