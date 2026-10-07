"""Relocatable python-config entry point for the native OHOS package."""
import shlex
import sys
import sysconfig

options = {
    "--prefix", "--exec-prefix", "--includes", "--libs", "--cflags",
    "--ldflags", "--extension-suffix", "--help", "--abiflags", "--configdir",
    "--embed",
}
args = sys.argv[1:]
if not args or set(args) - options or "--help" in args:
    print("Usage: python3-config " + " | ".join(sorted(options)))
    sys.exit(0 if "--help" in args else 1)

getvar = sysconfig.get_config_var
for option in args:
    if option in ("--prefix", "--exec-prefix"):
        print(sys.base_prefix if option == "--prefix" else sys.base_exec_prefix)
    elif option in ("--includes", "--cflags"):
        flags = ["-I" + sysconfig.get_path("include"),
                 "-I" + sysconfig.get_path("platinclude")]
        if option == "--cflags":
            flags += shlex.split(getvar("CFLAGS") or "")
        print(shlex.join(flags))
    elif option in ("--libs", "--ldflags"):
        flags = ["-lpython" + getvar("LDVERSION")]
        flags += shlex.split((getvar("LIBS") or "") + " " + (getvar("SYSLIBS") or ""))
        if option == "--ldflags":
            flags.insert(0, "-L" + getvar("LIBDIR"))
            # Consumers choose their relative installed loader layout explicitly.
        print(shlex.join(flags))
    elif option == "--extension-suffix":
        print(getvar("EXT_SUFFIX"))
    elif option == "--abiflags":
        print(sys.abiflags)
    elif option == "--configdir":
        print(getvar("LIBPL"))
