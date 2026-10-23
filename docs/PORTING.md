# Porting history and credits

The [Osmocom community](https://osmocom.org/) developed the cellular stack and
its libraries. The native Darwin support used here comes primarily from
[Andrei Gosman's macOS ARM64 port series](https://github.com/AndreiGosman).
The full repository URLs and commit hashes are in the source lock file.

That series already documents native component tests and a
[BTS hardware check](https://github.com/AndreiGosman/osmo-bts-macos-arm64/blob/bbcf5102754fffacbf550d054df87a61b282514c/README.md#hardware-check)
using a LibreSDR B220 Mini with dummy loads. Its pinned report did not include a
mobile station. The pinned
[MSC report](https://github.com/AndreiGosman/osmo-msc-macos-arm64/blob/6b44a1ec306115fb08d6de4b6f9ea4e8f22176df/README.md#not-covered)
also distinguishes startup/link checks from end-to-end voice and location
updates. This project extends that foundation with a LimeSDR-USB integration,
a repeatable four-prefix build and a documented two-iPhone lab session.
It makes no claim of historical priority.

## Platform choices

- **Darwin timerfd:** the pinned libosmocore port provides timerfd emulation and
  event-loop integration. Both installed core variants run its raw timer and
  callback regression programs.
- **SCTP:** macOS has no kernel SCTP socket implementation for these daemons.
  `libsctp-compat-macos-arm64` adapts the Linux-facing API to usrsctp. This lab
  uses UDP encapsulation over loopback, with a different local port for BSC,
  MSC and STP, avoiding raw sockets and root access.
- **Separate dependency prefixes:** radio-side libraries have SCTP disabled.
  BSC/core-side libraries link to the compatibility layer. MSC and HLR use the
  BSC dependency prefix; the radio path does not inherit its interposition.
- **BTS PCU socket:** the pinned BTS port falls back to `SOCK_STREAM` on Darwin
  and restores fixed-length PCUIF message framing. This lab disables GPRS and
  does not start a PCU.
- **LimeSDR output control:** the BTS uses `osmotrx tx-attenuation oml`, allowing
  BSC's configured power reduction to reach the TRX. A previous fixed BTS
  attenuation setting contributed to a carrier that the test phones did not
  initially list. The validated configuration uses OML attenuation and 20 dB
  maximum power reduction. This was functional bring-up, not a calibrated RF
  measurement.

This repository's additions are the pinned build orchestration, portable
configuration templates, foreground lifecycle and ownership checks, explicit
runtime RF controls, offline tests and validation documentation. Upstream
compatibility implementations and patches remain in their source repositories
and retain their authorship and licenses.
