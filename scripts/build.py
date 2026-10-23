"""Fetch pinned public sources and reproduce the four validated library prefixes."""
from pathlib import Path
import concurrent.futures
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import time

from common import ROOT, brew_prefix, prefix, state_dir, write_json

CORE_OPTIONS = ["--disable-doxygen", "--disable-pcsc", "--disable-systemd-logging", "--disable-uring", "--disable-libmnl"]
STAGES = [
    ("core-radio","libosmocore","trx",[],CORE_OPTIONS+["--disable-libsctp"],None),
    ("trx","osmo-trx","trx",[],["--with-lms","--without-uhd","--without-usrp1","--without-bladerf","--without-ipc","--without-mstrx","--without-neon","--without-neon-vfpv4","--without-sse"],[]),
    ("netif-radio","libosmo-netif","bts",["trx"],["--disable-doxygen","--disable-static","--disable-libsctp"],["TESTSUITEFLAGS=2-6"]),
    ("abis-radio","libosmo-abis","bts",["trx"],["--disable-static","--disable-dahdi","--disable-ortp","--disable-e1d"],[]),
    ("bts","osmo-bts","bts",["trx"],["--enable-trx","--disable-ortp"],[]),
    ("sctp","libsctp-compat","bsc",[],[],[]),
    ("core-cn","libosmocore","bsc",[],CORE_OPTIONS+["--enable-libsctp"],None),
    ("netif-cn","libosmo-netif","bsc",[],["--disable-doxygen","--disable-static","--enable-libsctp"],["TESTSUITEFLAGS=2-6"]),
    ("abis-cn","libosmo-abis","bsc",[],["--disable-static","--disable-dahdi","--disable-ortp","--disable-e1d"],[]),
    ("sigtran","libosmo-sigtran","bsc",[],["--disable-doxygen"],[]),
    ("mgw","osmo-mgw","bsc",[],[],[]),
    ("bsc","osmo-bsc","bsc",[],[],[]),
    ("smpp","libsmpp34","core",["bsc"],[],[]),
    ("hlr","osmo-hlr","core",["bsc"],["--with-systemdsystemunitdir=no"],[]),
    ("msc","osmo-msc","core",["bsc"],["--enable-smpp","--disable-iu","--with-systemdsystemunitdir=no"],[]),
]


def fetch_sources(lock):
    source_root = state_dir() / "sources"
    source_root.mkdir(parents=True, exist_ok=True)

    def fetch(item):
        name, entry = item
        if not re.fullmatch(r"[0-9a-f]{40}", entry["commit"]):
            raise ValueError("Source pin is not a full commit: " + name)
        dest = source_root / name
        if not dest.exists():
            subprocess.run(["git","init","-q",str(dest)], check=True)
            subprocess.run(["git","-C",str(dest),"remote","add","origin",entry["url"]], check=True)
            subprocess.run(["git","-C",str(dest),"fetch","--quiet","--depth","1","origin",entry["commit"]], check=True)
            subprocess.run(["git","-C",str(dest),"checkout","--quiet","--detach","FETCH_HEAD"], check=True)
        actual = subprocess.check_output(["git","-C",str(dest),"rev-parse","HEAD"],text=True).strip()
        if actual != entry["commit"]:
            raise ValueError("Existing source has another revision: " + str(dest))
        print("Pinned " + name + " " + actual[:12], flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        # Inspect every future: a failed fetch must fail the complete build.
        futures = [pool.submit(fetch, item) for item in lock["sources"].items()]
        for future in futures:
            future.result()


def prepare_core(lock):
    source_root = state_dir() / "sources"
    src, port = source_root / "libosmocore", source_root / "libosmocore-port"
    expected = {k:lock["sources"][k]["commit"] for k in ("libosmocore","libosmocore-port")}
    marker = src / ".darwin-port-applied"
    if marker.exists():
        if json.loads(marker.read_text()) != expected:
            raise ValueError("Darwin preparation marker has different source pins")
        return
    p = src / "src/core/exec.c"
    text = p.read_text()
    for old,new,field in (("setresgid","setregid","pw_gid"),("setresuid","setreuid","pw_uid")):
        before = f"{old}(pw->{field}, pw->{field}, pw->{field})"
        if text.count(before) != 1:
            raise ValueError("Unexpected privilege-drop code in pinned libosmocore")
        text = text.replace(before, f"{new}(pw->{field}, pw->{field})")
    p.write_text(text)
    for p in sorted((src / "src").glob("*/*.c")):
        if p.relative_to(src).as_posix() in ("src/core/tun.c","src/core/netdev.c"):
            continue
        text = p.read_text()
        if re.search(r"^#include <linux/", text, re.M):
            p.write_text("#ifdef __linux__\n" + text + "\n#endif\n")
    for name,dest in [("darwin_stubs.c","src/core/darwin_stubs.c"),
                      ("darwin_timerfd.c","src/core/darwin_timerfd.c"),
                      ("darwin_netdev.c","src/core/darwin_netdev.c"),
                      ("darwin_netdev.h","src/core/darwin_netdev.h"),
                      ("darwin_timerfd.h","include/sys/timerfd.h"),
                      ("darwin_compat.h","darwin_compat.h")]:
        target = src / dest
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(port / name,target)
    p = src / "src/core/Makefile.am"
    text = p.read_text()
    anchor = "\tstats_tcp.c \\\n"
    if text.count(anchor) != 1:
        raise ValueError("Unexpected libosmocore Makefile")
    p.write_text(text.replace(anchor,anchor+"\tdarwin_stubs.c \\\n\tdarwin_timerfd.c \\\n\tdarwin_netdev.c \\\n"))
    for patch in sorted((port / "patches").glob("*.patch")):
        subprocess.run(["patch","-p1","--batch","--forward","-i",str(patch)],cwd=src,check=True,stdout=subprocess.DEVNULL)
    (src / ".tarball-version").write_text("1.14.2.4-2a26b-darwin-v0.2.8\n")
    write_json(marker, expected)


def require_timer_callbacks(text):
    # The port's callback test prints its count but always exits zero.
    if not re.search(r"^ticks=6 in 2\.6 s \(expected 1 \+ 5\)$", text, re.M):
        raise RuntimeError("Timerfd event-loop test did not report the expected six callbacks")


def build_all(jobs=4, start_from=None, skip_raw_timer_test=False):
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        raise ValueError("The native build requires Apple Silicon macOS")
    state = state_dir()
    if any(c.isspace() for c in str(state)):
        raise ValueError("Autotools needs a build path without whitespace; set OSMO_LAB_STATE to one")
    state.mkdir(parents=True,exist_ok=True)
    lock = json.loads((ROOT / "sources.lock.json").read_text())
    fetch_sources(lock)
    prepare_core(lock)
    brew = brew_prefix()
    logs = state / "logs/build"
    logs.mkdir(parents=True,exist_ok=True)
    report_path = state / "build-report.json"
    report = {"sources":lock["sources"], "steps":[], "skipped_checks":[], "complete":False}
    if report_path.exists():
        old = json.loads(report_path.read_text())
        if old["sources"] != report["sources"]:
            raise ValueError("Build provenance has different source pins; use a separate state directory")
        report["steps"] = old["steps"]
    source_root = state / "sources"
    names = [s[0] for s in STAGES]
    if start_from and start_from not in names:
        raise ValueError("Unknown --from stage; choose " + ", ".join(names))
    first = names.index(start_from) if start_from else 0

    def run(label, args, cwd, env, timeout=900):
        log = logs / (label + ".log")
        print("START " + label,flush=True)
        started = time.monotonic()
        with log.open("wb") as stream:
            stream.write(("cwd="+str(cwd)+"\n"+shlex.join(map(str,args))+"\n").encode())
            stream.flush()
            proc = subprocess.Popen(list(map(str,args)),cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                code = proc.wait(timeout=timeout)
            except BaseException:
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait()
                raise
        report["steps"].append({"name":label,"command":list(map(str,args)),"cwd":str(cwd),
                                "exit_code":code,"seconds":round(time.monotonic()-started,2)})
        write_json(report_path,report)
        print(f"END {label}: {code}",flush=True)
        if code:
            print(log.read_text(errors="replace")[-7000:])
            raise RuntimeError("Build/check failed; see " + str(log))

    for label,name,group,dependencies,options,tests in STAGES[first:]:
        target = prefix(group)
        target.mkdir(parents=True,exist_ok=True)
        includes = [target,*[prefix(d) for d in dependencies]]
        env = os.environ.copy()
        env.update({"CC":"/usr/bin/clang","CXX":"/usr/bin/clang++",
                    "PATH":f"{brew}/bin:{brew}/opt/make/libexec/gnubin:"+env["PATH"],
                    "PKG_CONFIG_PATH":":".join([str(brew / "opt/sqlite/lib/pkgconfig"),*[str(p / "lib/pkgconfig") for p in includes],str(brew / "lib/pkgconfig"),str(brew / "share/pkgconfig")]),
                    "ACLOCAL_PATH":":".join([*[str(p / "share/aclocal") for p in includes],str(brew / "share/aclocal")]),
                    "CFLAGS":"-O2 -g "+" ".join("-I"+str(p / "include") for p in includes),
                    "CXXFLAGS":"-O2 -g "+" ".join("-I"+str(p / "include") for p in includes),
                    "LDFLAGS":" ".join("-L"+str(p / "lib") for p in includes),
                    "LIBSCTP_COMPAT_UDP_ENCAPS_PORT":"39897"})
        src = source_root / name
        dest = state / "build" / label
        dest.mkdir(parents=True,exist_ok=True)
        if name == "libsctp-compat":
            usrsctp = Path(subprocess.check_output([str(brew / "bin/pkg-config"),"--variable=prefix","usrsctp"],env=env,text=True).strip())
            if not (usrsctp / "include/usrsctp.h").is_file():
                raise ValueError("Cannot find the Homebrew usrsctp header")
            run(label+"-configure",["cmake","-S",src,"-B",dest,"-DCMAKE_BUILD_TYPE=Release",f"-DCMAKE_INSTALL_PREFIX={target}","-DCMAKE_C_FLAGS="+env["CFLAGS"]+" -I"+str(usrsctp / "include")],state,env)
            run(label+"-build",["cmake","--build",dest,"--parallel",str(jobs)],state,env)
            run(label+"-check",["ctest","--test-dir",dest,"--output-on-failure","--timeout","45"],state,env)
            run(label+"-install",["cmake","--install",dest],state,env)
            continue
        if name == "libosmocore":
            env["CFLAGS"] += f" -include {src}/darwin_compat.h -I{src}/include"
        if name == "libosmo-netif": env["ac_cv_header_pcap_h"] = "no"
        if name != "libosmocore":
            run(label+"-refresh",["git","update-index","-q","--refresh"],src,env)
        run(label+"-autoreconf",["autoreconf","-fi"],src,env)
        run(label+"-configure",[src / "configure","--prefix="+str(target),*options],dest,env)
        make_flags = ["LDFLAGS="+env["LDFLAGS"]+" -Wl,-undefined,dynamic_lookup"] if name == "libosmocore" else []
        run(label+"-build",["make",f"-j{jobs}",*make_flags],dest,env)
        if tests is not None: run(label+"-check",["make","check",*tests],dest,env)
        if name == "libsmpp34":
            for test in sorted((dest / "binaries").glob("*_test")):
                if test.is_file() and os.access(test,os.X_OK):
                    run("pdu-"+test.name,[test],test.parent,env,30)
        run(label+"-install",["make","install",*make_flags],dest,env)
        if name == "libosmocore":
            (target / "include/sys").mkdir(exist_ok=True)
            shutil.copyfile(src / "include/sys/timerfd.h",target / "include/sys/timerfd.h")
            port = source_root / "libosmocore-port"
            for test in ("darwin_timerfd_test","darwin_timerfd_osmo_fd_test","sockaddr_cmp_test"):
                binary = dest / test
                flags = shlex.split(subprocess.check_output(["pkg-config","--cflags","--libs","libosmocore"],env=env,text=True))
                run(label+"-"+test+"-build",["clang","-O2","-I"+str(target / "include"),str(port / "tests" / (test+".c")),"-o",binary,*flags],dest,env)
                if skip_raw_timer_test and test == "darwin_timerfd_test":
                    skipped = {"name":label+"-"+test,
                               "reason":"Standalone raw timerfd test explicitly omitted; VM timing is not validated"}
                    report["skipped_checks"].append(skipped)
                    write_json(report_path, report)
                    print("SKIP " + skipped["name"] + ": " + skipped["reason"], flush=True)
                else:
                    run(label+"-"+test,[binary],dest,env,30)
                    if test == "darwin_timerfd_osmo_fd_test":
                        require_timer_callbacks((logs / (label+"-"+test+".log")).read_text())
    report["complete"] = True
    write_json(report_path,report)
    print("Native build complete. Reports and private build logs: " + str(state),flush=True)
