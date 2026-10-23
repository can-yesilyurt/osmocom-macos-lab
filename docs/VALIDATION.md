# Validation snapshot — 23 October 2026

The native macOS bring-up used two real iPhones with a LimeSDR-USB. Both phones
registered, and the operator confirmed that calls and SMS between them worked.
The portable recipe in this repository subsequently rebuilt the same pinned
component versions in a fresh directory. Its automated runtime test covers
core links and BTS configuration parsing without opening the radio.

These are separate observations: the handset session used the original staged
native build; the fresh portable build has passed software/core checks but has
not had a second over-the-air handset acceptance run. No CI result is inferred
from the local build. The machine-readable snapshot is
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
| Repository orchestration | 11 tests passed | Profile rejection, locked rendering, ownership, VTY framing and publication checks |

The skips are not passing tests. In particular, the MSC suite's wrapped
call/VLR/SMS-queue cases were not exercised by that suite on Darwin.

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
