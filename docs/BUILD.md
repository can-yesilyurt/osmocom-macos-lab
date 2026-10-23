# Build and reproducibility

The builder fetches the full commits in [`sources.lock.json`](../sources.lock.json).
It downloads public sources into the private state directory and builds them
there. This repository does not contain the upstream source trees or binaries.
No previous lab directory or preinstalled Osmocom build is required.

## Prerequisites

Use Apple Silicon macOS, a native ARM64 Homebrew installation, Xcode Command
Line Tools and Python 3. The orchestration uses Python's standard library only.
Install the build/runtime dependencies with `brew bundle --file=Brewfile`, then
run `./lab doctor`. There is no `sudo` step in the build or launchers.

The validated machine used macOS 27.0.1 and Apple clang 21.0.0. The manual native
CI job targets GitHub's ARM64 `macos-26` runner. Defining that job does not establish
a passing CI result; consult its actual run before claiming that host works.

Paths must have no whitespace. Configuration additionally checks that the PCU
socket path fits in Darwin's 103 usable Unix socket path bytes. A short absolute
state path such as `export OSMO_LAB_STATE="$HOME/osmolab-state"` is convenient.
Use a separate state directory for another checkout or source revision.

## Prefixes and build order

| Prefix under state | Components | Purpose |
| --- | --- | --- |
| `local/trx` | SCTP-disabled libosmocore, OsmoTRX-LMS | Radio side without SCTP interposition |
| `local/bts` | SCTP-disabled libosmo-netif, libosmo-abis, OsmoBTS | Links to the radio core prefix |
| `local/bsc` | SCTP shim, SCTP-enabled core/netif/Abis, SIGTRAN/STP, MGW, BSC | Core transport and BSC media |
| `local/core` | libsmpp34, HLR, MSC | Links to the BSC dependency prefix |

The source ports supply Darwin timerfd emulation, SCTP compatibility and other
platform changes. `scripts/build.py` applies the pinned libosmocore port files and
patches to the pinned upstream core. Other components use the already patched
port checkouts. Out-of-tree build directories are used even when a downloaded
fork happens to contain historical build output.

TRX uses the LimeSuite backend, 4 samples per symbol and the scalar processing
path. UHD, USRP1, BladeRF, IPC, multi-TRX and optional NEON/SSE paths are disabled
in this recipe. BTS uses the TRX backend without oRTP, DAHDI or E1D. MSC has SMPP
enabled and Iu disabled. The profile has GPRS disabled.

## Interrupted builds

The build writes `build-report.json` and per-step logs under the state directory.
A report with `complete: true` and zero exit codes records a completed run. The
normal command reruns all stages. To resume after a failed stage, inspect its log
and use the stage's base name:

```sh
./lab build --jobs 6 --from msc
```

Stage names in order are `core-radio`, `trx`, `netif-radio`, `abis-radio`, `bts`,
`sctp`, `core-cn`, `netif-cn`, `abis-cn`, `sigtran`, `mgw`, `bsc`, `smpp`, `hlr`,
`msc`. Resume assumes earlier dependencies were built successfully. Keep logs
local: they include absolute machine paths.

The state directory is **not relocatable** after installation because Mach-O
dylib install names include the prefix. Rebuild in a new state directory after
moving to another path. Changing a pin also requires a separate state directory.
Homebrew libraries are not vendored and may change after `brew upgrade`; rerun
doctor, the relevant build checks and the native smoke check after such changes.

The optional `--skip-raw-timer-test` switch omits the standalone timerfd program
in both prefixes and records those omissions in `skipped_checks`. The default
build runs it. The cloud workflow uses this switch because the hosted VM missed
the program's strict 80–120 ms interval bounds in an observed run. The omitted
program also contains coalescing, close/reuse and fork checks, so none of its
checks count as CI passes. Event-loop timer callbacks and sockaddr checks still
run, followed by all supported component suites and the native core smoke test.
The callback test's printed count is checked explicitly because its upstream
program returns zero even when the count is wrong. Use the default build on the
physical host to retain the full selected regression set.

## Checks run by the builder

The build fails on a nonzero check result. Most component suites use `make check`;
SCTP uses CTest. The SMPP library's 27 PDU executables are run individually because
its `make check` does not run them. Both libosmocore prefixes run Darwin timerfd,
event-loop timerfd and sockaddr comparison regression programs.

This is a selected platform-compatible test set. The full libosmocore suite and
external Osmocom TTCN/Python integration suites are not run. Linux-specific netif
stream test 1 is excluded; the remaining five groups run. Tests requiring GNU
linker wrapping, disabled oRTP, optional TCAP support or default mDNS fixtures may
be skipped by the pinned component suites. Exact observed counts and reasons
are in [the validation report](VALIDATION.md).

## Source versions

The authoritative URLs and full hashes are in [`sources.lock.json`](../sources.lock.json).
The version labels describe the pinned source versions, not a promise of latest
upstream releases. Patches and compatibility code remain credited to their
original projects in [NOTICE](../NOTICE) and [porting notes](PORTING.md).

| Source | Version label | Pinned commit |
| --- | --- | --- |
| [libosmocore](https://github.com/osmocom/libosmocore) | 1.14.2.4 | [`2a26b47cb6c6`](https://github.com/osmocom/libosmocore/tree/2a26b47cb6c6590fde8cb953f1caeaf498d5568b) |
| [libosmocore-port](https://github.com/AndreiGosman/libosmocore-macos-arm64) | 0.2.8 | [`2e50312c3f7a`](https://github.com/AndreiGosman/libosmocore-macos-arm64/tree/2e50312c3f7a49135b5dec33d03187a09dd224b0) |
| [osmo-trx](https://github.com/AndreiGosman/osmo-trx-macos-arm64) | 1.8.0 | [`2142e7a301f4`](https://github.com/AndreiGosman/osmo-trx-macos-arm64/tree/2142e7a301f44db956746d47cb3f22c77667d410) |
| [libosmo-netif](https://github.com/AndreiGosman/libosmo-netif-macos-arm64) | 1.8.0 | [`c8bb993434cd`](https://github.com/AndreiGosman/libosmo-netif-macos-arm64/tree/c8bb993434cde5271e8844108dc0d2dcad1139ee) |
| [libosmo-abis](https://github.com/AndreiGosman/libosmo-abis-macos-arm64) | 2.2.0 | [`60a9eb67f1d9`](https://github.com/AndreiGosman/libosmo-abis-macos-arm64/tree/60a9eb67f1d9257c75392fcaf733b5e5699ce465) |
| [osmo-bts](https://github.com/AndreiGosman/osmo-bts-macos-arm64) | 1.12.0 | [`bbcf5102754f`](https://github.com/AndreiGosman/osmo-bts-macos-arm64/tree/bbcf5102754fffacbf550d054df87a61b282514c) |
| [libsctp-compat](https://github.com/AndreiGosman/libsctp-compat-macos-arm64) | 0.4.1 | [`3a25a3813cdc`](https://github.com/AndreiGosman/libsctp-compat-macos-arm64/tree/3a25a3813cdc6a569b27cb0bbc7e63c854bbaa2d) |
| [libosmo-sigtran](https://github.com/AndreiGosman/libosmo-sigtran-macos-arm64) | 2.3.0 | [`b2038fb8ce51`](https://github.com/AndreiGosman/libosmo-sigtran-macos-arm64/tree/b2038fb8ce513729d48691eae957fbee10158c36) |
| [osmo-mgw](https://github.com/AndreiGosman/osmo-mgw-macos-arm64) | 1.16.1 | [`6ba1ca4b93c3`](https://github.com/AndreiGosman/osmo-mgw-macos-arm64/tree/6ba1ca4b93c3b41c88b3267f3ab057d7a0a92115) |
| [osmo-bsc](https://github.com/AndreiGosman/osmo-bsc-macos-arm64) | 1.15.0 | [`1bfb14109cdb`](https://github.com/AndreiGosman/osmo-bsc-macos-arm64/tree/1bfb14109cdbb0c0254b1d45994cec3fbec1b48b) |
| [libsmpp34](https://github.com/AndreiGosman/libsmpp34-macos-arm64) | 1.14.6 | [`004e7ab62665`](https://github.com/AndreiGosman/libsmpp34-macos-arm64/tree/004e7ab626652d21373c21d4fe2aaa251d032de7) |
| [osmo-hlr](https://github.com/AndreiGosman/osmo-hlr-macos-arm64) | 1.9.4 | [`b7b745e661ae`](https://github.com/AndreiGosman/osmo-hlr-macos-arm64/tree/b7b745e661ae388c8a7429b8fbf6b6ae5df7eb79) |
| [osmo-msc](https://github.com/AndreiGosman/osmo-msc-macos-arm64) | 1.16.0 | [`6b44a1ec3061`](https://github.com/AndreiGosman/osmo-msc-macos-arm64/tree/6b44a1ec306115fb08d6de4b6f9ea4e8f22176df) |
