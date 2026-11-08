#!/usr/bin/env python3
"""Read the option bytes and stop the launch if any of them don't match what this
build needs. Report every mismatch, not just the first, and print how to fix. """

import argparse
import re
import subprocess
import sys

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--pre-provision", action="store_true",
    help="check an Open board has TrustZone & watermarks set, ready for provision.sh, "
         "instead of checking a provisioned board is ready to flash")
args = parser.parse_args()

# Add rows here. Name must match the `STM32_Programmer_CLI -c port=SWD mode=UR -ob displ` output.
EXPECTED = {
    "PRODUCT_STATE": ("0xED|0x2E|0xC6|0x72", "Open, or iROT-Provisioned/TZ-Closed/Closed with debug auth (ROT_Provisioning/scripts)"),
    "TZEN":          ("0xB4", "TrustZone on, so the 0x0C000000 secure alias exists"),
    "SECWM1_STRT":   ("0x0",  "bank 1 secure from sector 0..."),
    "SECWM1_END":    ("0x2",  "...through 2: the secure app plus the NSC veneers (0x0C000000-0x0C005FFF)"),
    "SECWM2_STRT":   ("0x1",  "start above end means an empty secure region 2, so..."),
    "SECWM2_END":    ("0x0",  "...bank 2 is non-secure; the NS image fetches from bank 1 sector 3 (0x08006000)"),
    "SECBOOTADD":    ("0xC0004", "direct boot to the app vectors at 0x0C000400, past the unused 0x400 header. Only used in an open state; ignored in a closed state."),
}

try:
    # Copy-pastable command:
    #   STM32_Programmer_CLI -c port=SWD mode=UR -ob displ
    probe = subprocess.run(
        ["STM32_Programmer_CLI", "-c", "port=SWD", "mode=UR", "-ob", "displ"],
        capture_output=True,
        text=True,
    )
    # A provisioned board keeps debug shut through the boot ROM (HDPL0), so connect-under-reset can't reach it
    if "Cannot connect to access port" in probe.stdout + probe.stderr:
        # Copy-pastable command:
        #   STM32_Programmer_CLI -c port=SWD mode=HotPlug ap=1 -ob displ
        probe = subprocess.run(
            ["STM32_Programmer_CLI", "-c", "port=SWD", "mode=HotPlug", "ap=1", "-ob", "displ"],
            capture_output=True,
            text=True,
        )
except FileNotFoundError:
    sys.exit("ERROR: STM32_Programmer_CLI not on PATH. Source the STM32CubeCLT environment.")

# The programmer colors its output; strip the escapes so the patterns below match.
report = re.sub(r"\x1b\[[0-9;]*m", "", probe.stdout + probe.stderr)

if "No ST-Link detected" in report:
    sys.exit("ERROR: no ST-LINK detected. Plug in the Nucleo.")

# VS Code re-runs this task when you hit restart, while the session it is restarting
# still holds the probe. Nothing has changed since that session checked, so skip.
if "DEV_CONNECT_ERR" in report:
    print("ST-LINK busy (debug session already running) - option bytes not re-checked.")
    sys.exit(0)

m = re.search(r"^\s*PRODUCT_STATE\s*:\s*(0x[0-9A-Fa-f]+)", report, re.MULTILINE)
is_open = m is not None and int(m.group(1), 16) == 0xED # 0xED = Open

if args.pre_provision and not is_open:
    sys.exit("ERROR: product is not Open (provisioned). --pre-provision arg "
             "only works if board is not yet provisioned.")
if not args.pre_provision and is_open:
    sys.exit("ERROR: product is in the Open state. This demo must be run in a "
             "non-open state (provisioned or closed).")

wrong = []
for name, (want, why) in EXPECTED.items():
    if name == "SECBOOTADD" and not is_open:
        # SECBOOTADD is only used in an open state. In a non-open state, SECBOOTADD is ignored
        print(f"  {name:<14} -      n/a (Open only)")
        continue
    found = re.search(rf"^\s*{name}\s*:\s*(0x[0-9A-Fa-f]+)", report, re.MULTILINE)
    got = found.group(1) if found else "?"
    ok = found is not None and int(got, 16) in [int(w, 16) for w in want.split("|")]
    print(f"  {name:<14} {got:<6} {'ok' if ok else 'EXPECTED ' + want}")
    if not ok:
        wrong.append((name, want, why))

if not wrong:
    print("Option bytes match - safe to flash.")
    sys.exit(0)

sys.stdout.flush()   # keep the table above the error block
print("\nERROR: device's option bytes do not match this build:", file=sys.stderr)
for name, want, why in wrong:
    print(f"  {name} needs {want} - {why}", file=sys.stderr)

# TZEN must go in its own command: enabling it mass-erases and resets the
# watermarks back to fully-secure, silently undoing anything set alongside it.
print("\nFix, in this order:", file=sys.stderr)
for group in ([w for w in wrong if w[0] == "TZEN"], [w for w in wrong if w[0] != "TZEN"]):
    if group:
        settings = " ".join(f"{name}={want}" for name, want, _ in group)
        print(f"  STM32_Programmer_CLI -c port=SWD mode=UR -ob {settings}", file=sys.stderr)
sys.exit(1)
