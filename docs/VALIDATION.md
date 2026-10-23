# Validation snapshot — 23 October 2026

The native macOS bring-up used two real iPhones with a LimeSDR-USB. Both phones
registered, and the operator confirmed that calls and SMS between them worked.
The portable recipe in this repository subsequently rebuilt the same pinned
component versions in a fresh directory. Its automated runtime test covers
core links and BTS configuration parsing without opening the radio.

These are separate observations: the successful call/SMS session used the
original staged native build. The portable packet profile subsequently reached
confirmed circuit-switched registration for both phones, but call/SMS regression
and handset packet-data acceptance on that profile remain pending. No CI result
is inferred from the local build. The machine-readable voice snapshot is
[`summary.json`](../validation/2026-10-23/summary.json).

## Host and dependencies

| Item | Observed value |
| --- | --- |
| CPU / platform | Apple M5 Max / Darwin ARM64 |
| macOS | 27.0.1 |
| Compiler / SDK | Apple clang 21.0.0 / macOS 27.0 |
| talloc | 2.5.0 |
| GnuTLS | 3.8.13 |
| FFTW single precision | 3.3.11 |
| LimeSuite | 23.11.0-release |
| usrsctp | 0.9.5.0 |
| SQLite | 3.53.4 |

All seven installed daemon entry points report Mach-O ARM64 and the versions
recorded in the source lock. The fresh builder recorded **123 commands, all with
exit code 0**, and a completed build report. This is the number of build/check
commands, not the number of individual test assertions.

## Component regression results

| Component | Fresh build result | Scope or skip reason |
| --- | --- | --- |
| libosmocore, both prefixes | Three regression programs passed in each | Six raw timer scenarios; six event-loop callbacks; sockaddr comparison. Full upstream suite not run |
| OsmoTRX | 8 passed | Includes fake-discovery LMS device test; not an RF measurement |
| libosmo-netif, both prefixes | 5 selected groups passed in each | Linux-specific stream group 1 excluded; pcap helper disabled |
| libosmo-abis, both prefixes | 45 passed, 1 skipped in each | oRTP disabled |
| OsmoBTS | 12 passed | Component suite |
| libsctp-compat | 7 CTest cases passed | Unprivileged UDP encapsulation |
| libosmo-sigtran | 3 passed, 1 skipped | Optional TCAP test disabled |
| OsmoMGW | 2 passed | Component suite |
| OsmoBSC | 7 passed, 2 skipped | GNU linker wrapping unavailable on Darwin |
| libsmpp34 | 27 PDU executables passed | Run individually; `make check` alone has no tests |
| OsmoHLR | 8 passed, 1 skipped | Default mDNS client fixture skipped |
| OsmoMSC | 5 passed, 13 skipped | GNU linker wrapping unavailable on Darwin |
| Repository orchestration | 12 tests passed | Profile rejection, locked rendering, ownership, VTY framing and publication checks |

The skips are not passing tests. In particular, the MSC suite's wrapped
call/VLR/SMS-queue cases were not exercised by that suite on Darwin.

## Hosted ARM64 software check

The [native build run](https://github.com/can-yesilyurt/osmocom-macos-lab/actions/runs/39931415240)
passed on a clean GitHub-hosted ARM64 Mac running macOS **26.6.2**, at repository
commit `e5b6ab1aaa95d3ab01b72bbd1aef18e4f67ed755`. It recorded **119 successful
build/check commands**, then passed the same core and BTS-configuration smoke
check listed below. No SDR was opened and RF was not enabled. The dependency
versions matched the physical-host table except for SQLite, which was 3.54.0.
The [offline checks](https://github.com/can-yesilyurt/osmocom-macos-lab/actions/runs/39931386177)
also passed all 12 orchestration tests and publication checks on Linux and macOS.

The cloud build used `--timer-checks functional`: the original raw timerfd and
fixed-window callback programs were replaced by the functional delivery/disarm
probe in each core prefix. Those **four program omissions** are explicit in the
build report. The probe passed in both prefixes and checks event delivery and
disarming without claiming scheduling latency. The physical-host build's 123
commands above used the original strict programs instead.

The first [cloud build](https://github.com/can-yesilyurt/osmocom-macos-lab/actions/runs/39929138390)
exposed a separate hosted-runner timing limit: the strict
raw timerfd program observed initial 100 ms intervals at 175 ms and 166 ms,
and a 150 ms shot at 223 ms. Its other checks completed, but the program failed.
The next [cloud run](https://github.com/can-yesilyurt/osmocom-macos-lab/actions/runs/39929886888)
also failed the fixed-window callback-count check in the SCTP-enabled prefix.
The cloud workflow uses a functional timer delivery/disarm probe in place of
both timing-sensitive programs in each prefix and records the four omissions.
The physical-host results above include both original programs. A cloud build
does not establish the omitted assertions or real-time behavior; its functional
timer probe has a separate, explicitly documented scope.

## Automated runtime checks

`./lab core --check` and `scripts/smoke_native.py` passed on the fresh build:

- STP, both MGWs, HLR, MSC and BSC became responsive on their VTY ports.
- MSC's M3UA ASP was active, MSC/HLR GSUP connected, and BSC/MSC BSSMAP connected.
- Status reported a locked carrier with BTS absent.
- A duplicate core launcher failed without stopping or changing the owned core.
- The core group shut down cleanly. MGWs use SIGTERM; other daemons use SIGINT.
- The real BTS parsed the generated configuration in `--vty-test` mode and
  retained OML-controlled attenuation, without connecting to BSC.

No transceiver was launched and RF was not enabled during these checks. They
do not replace a radio/handset acceptance test on the portable build.

## Native radio and handset session

The earlier native session used GSM900 ARFCN 25, MCC/MNC `520/999`, LAC 1, cell ID
1, BSIC 63, internal reference clock, FR voice, no GPRS, optional authentication
and A5/0. `OSMO-TEST` was the configured network name. The LimeSDR path was
`BAND1` TX / `LNAL` RX, with 20 dB power reduction delivered through OML.
These describe that session; they are not an allocation or a calibrated power
measurement. The public template uses a different example PLMN (`001/01`).

| Observation | Evidence and qualification |
| --- | --- |
| Two iPhones found the cell | Operator report after fresh handset scans |
| Both phones registered | HLR subscriber count 2; MSC reported radio contact, HLR data and location confirmation, attached and reachable for both |
| Carrier operational | BSC reported Enabled / Unlocked / OK with RSL connected |
| Voice between phones | Operator confirmed successful calls; no independent audio capture or quality scoring |
| SMS between phones | Operator confirmed successful delivery; no message contents retained in this repository |
| Broadcast generation | SCH/BCCH decoded with passing CRC from the local BTS-to-TRX stream; not a separate over-the-air receiver |
| Short radio monitoring | Roughly 30 seconds: no RX overruns or TX underruns; one startup TX drop stayed unchanged |

The explicit operator confirmation was: “Yes, both calls and SMS worked.”
Subscriber IMSIs, temporary identities, assigned numbers, SDR serial, raw
captures, SMS store and subscriber databases are intentionally omitted.

## Limits and follow-up validation

This was a functional lab session, not a stability soak or a conformance test.
One USB transfer-timeout warning during initial radio opening recovered; the
short successful session does not establish sustained USB reliability.
No independent spectrum/power measurements or clock accuracy calibration were
performed. SCTP AUTH/ASCONF capability probes were unsupported, while the local
M3UA links worked; extended SCTP features and external kernel-SCTP peer
interoperability were not tested.

No claim is made for other LimeSDR models, Intel Macs, other macOS versions,
other bands, multiple carriers, GPRS/EDGE, AMR, roaming, external telephone
interconnect, emergency services or long-term call/SMS reliability. A useful next
hardware acceptance run would repeat registration, calls and SMS using the
portable build and record duration, counters and any USB warnings without
publishing subscriber data.

## Optional packet-data bring-up

The optional GPRS stages were subsequently built on the same physical host,
using three additional source pins without changing the original thirteen.
The combined build report contains **141 commands with exit code 0**, including
18 commands for GGSN, PCU and SGSN. Selected component results were GGSN 6 passed;
PCU 11 passed / 3 skipped; SGSN 5 passed / 2 skipped. The skips require GNU linker
wrapping, and external Python/TTCN integration suites were not run.
Repository orchestration checks passed 19 tests after the packet extension.
Four additional checks for VPN-route refresh, scoped state removal, rollback
and stale ownership brought the local suite to 23 passing tests.

The clean ARM64 [native GPRS CI run](https://github.com/can-yesilyurt/osmocom-macos-lab/actions/runs/39952921147)
also passed at commit `3aa0a20f0e906410b4afe7ce24dc1b86df759e9f`.
It built the optional components, ran selected component checks, smoke-tested
the base voice core/BTS configuration and compiled the gateway protocol probe.
That hosted-VM run used the documented functional timer checks; it did not
open an SDR, configure NAT or test a handset. The subsequent route-refresh
change is covered by local unit checks, not this earlier native CI run.

All three packet daemons parsed their rendered configurations. The seven-child
core connected both MSC and SGSN to HLR. With the LimeSDR open and RF locked,
PCU connected to BTS; both NS peers reported ALIVE / UNBLOCKED, and SGSN's BSSGP
cell BVC reported UNBLOCKED. The temporary Mac gateway created its `utun`
interface, enabled forwarding and installed NAT in its own PF anchor while
preserving the default route.

The carrier subsequently reached Enabled / Unlocked / OK with the PHY connected,
and PCU reported PDCH enabled on timeslots 6 and 7. Both test phones appeared in
MSC's subscriber cache with radio contact confirmed, HLR data confirmed and an
attached use. This verifies circuit-switched registration on the portable
profile; calls/SMS have not yet been repeated on this profile.

Initially, SGSN received handset routing-area updates without matching mobility
contexts and sent cause 10 (implicitly detached) rejections. After the phone
retry, both handsets reached GMM Registered.NORMAL and one had an active IPv4
PDP context with user-data counters advancing in both directions. Its requested
APN was `internet`, mapped by the configured fallback to the lab's `osmo` APN.
The operator reported a G indicator but no page load. This verifies real-handset
packet registration and PDP activation, while internet page acceptance remains
incomplete. Subscriber identifiers and raw signaling remain private.

Disabling the host VPN changed the default interface from a VPN tunnel to
Wi-Fi while the running NAT still referenced the previous interface. Gateway
status detected the mismatch. The new `network refresh` command retargets only
the lab NAT and clears connection states originating in the lab pool without
restarting GGSN or phone PDP contexts. Its live application and a subsequent
phone page test are pending at this snapshot.

A synthetic native `libgtp` probe subsequently obtained a PDP address, received
a DNS A response, completed a TCP handshake and received an HTTP 403 response
from the chosen HTTP destination. This establishes a DNS and TCP/HTTP return
path through the gateway; the 403 is not a successful page status. It does not
exercise the real SGSN, PCU, radio, handset, TLS or a sustained transfer.

The upstream SGSN-emulator ICMP probe received no replies. The original Mac
firewall block-all and stealth settings were in place during the successful
DNS/TCP/HTTP exchange. Two brief block-all comparisons restored the prior
setting and did not resolve the initial ICMP/TCP probe failures. No permanent
firewall-mode change was needed. See [GPRS operation](GPRS.md) for the separate
gateway and handset acceptance steps.
