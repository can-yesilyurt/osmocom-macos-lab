#!/usr/bin/env python3
"""Build and run the native Apple Silicon Osmocom lab."""
import argparse
import os
import platform
import shutil
import subprocess
import sys

sys.dont_write_bytecode = True
from common import ROOT, brew_prefix, state_dir


def doctor():
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("The native build targets Apple Silicon macOS")
    missing = [name for name in ["git","brew","clang","make","cmake","autoreconf","pkg-config"] if not shutil.which(name)]
    if missing:
        raise ValueError("Missing tools: " + ", ".join(missing) + ". Install Xcode tools and run brew bundle.")
    env = os.environ.copy()
    brew = brew_prefix()
    env["PKG_CONFIG_PATH"] = f"{brew}/opt/sqlite/lib/pkgconfig:{brew}/lib/pkgconfig:{brew}/share/pkgconfig"
    print("Host: " + platform.system() + " " + platform.machine())
    print(subprocess.check_output(["sw_vers","-productVersion"],text=True).strip())
    for module in ["talloc","gnutls","fftw3f","LimeSuite","usrsctp","sqlite3"]:
        r = subprocess.run(["pkg-config","--modversion",module],env=env,capture_output=True,text=True)
        if r.returncode:
            raise ValueError("Missing dependency: " + module + ". Run brew bundle.")
        print(module + " " + r.stdout.strip())
    print("Local state: " + str(state_dir()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command",required=True)
    sub.add_parser("doctor",help="check build tools and Homebrew dependencies")
    build = sub.add_parser("build",help="fetch pinned sources, build and run component regression checks")
    build.add_argument("--jobs",type=int,default=4)
    build.add_argument("--from",dest="start_from")
    build.add_argument("--timer-checks", choices=["strict", "functional"], default="strict",
                       help="strict port tests (default), or functional timer checks without latency assertions for VMs")
    sub.add_parser("configure",help="render local configurations with RF locked")
    core = sub.add_parser("core",help="run STP, two MGWs, HLR, MSC and BSC in the foreground")
    core.add_argument("--check",action="store_true",help="start a temporary core, check links, then stop")
    sub.add_parser("trx",help="run the LimeSuite transceiver in the foreground")
    sub.add_parser("bts",help="run the BTS in the foreground")
    sub.add_parser("status",help="read the existing network and RF state")
    rf = sub.add_parser("rf",help="explicit RF control on the already running lab")
    rf.add_argument("action",choices=["on","off"])
    args = parser.parse_args()
    try:
        if args.command == "doctor":
            doctor()
        elif args.command == "build":
            if not 1 <= args.jobs <= 64: raise ValueError("--jobs must be in 1..64")
            from build import build_all
            build_all(args.jobs,args.start_from,args.timer_checks)
        elif args.command == "configure":
            from configure import render
            render()
        else:
            from runtime import run_core, run_radio, show_status, set_rf
            if args.command == "core": return run_core(args.check)
            if args.command in ("trx","bts"): return run_radio(args.command)
            if args.command == "status": return show_status()
            if args.command == "rf": return set_rf(args.action)
        return 0
    except (OSError,ValueError,RuntimeError,subprocess.CalledProcessError) as exc:
        print(str(exc),file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
