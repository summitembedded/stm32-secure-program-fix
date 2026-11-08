#!/usr/bin/env python3
"""Build DA_Config.obk from DA_Config.xml with ST's TPC CLI, or --check an existing .obk. Format: README.md."""

import argparse
import hashlib
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization

HERE = Path(__file__).resolve().parent
HEADER_LEN = 12
INTEGRITY_HASH_BYTES = slice(0x0C, 0x2C)
ROOT_HASH_BYTES = slice(0x2C, 0x4C)

PERMISSION_BITS = {
    0: "debug from HDPL1, non-secure",
    1: "debug from HDPL2, non-secure",
    2: "debug from HDPL3, non-secure",
    4: "debug from HDPL1, secure",
    5: "debug from HDPL2, secure",
    6: "debug from HDPL3, secure",
    12: "regression to TZ-Closed",
    14: "full regression to Open",
}

# (offset, size, field, how to decode, what it's for); layout read from TPC's output, checked against ST's .obk
FIELDS = [
    (0x00, 4, "destination", "address", "where ST's ROM writes it: start of the HDPL1 OBK area"),
    (0x04, 4, "size", "int", "bytes after this 12-byte header"),
    (0x08, 4, "encrypt", {0: "no", 1: "yes"}, "ROM encrypts it with the HDPL1 DHUK on the way into OBK"),
    (0x0C, 32, "integrity_hash", "bytes", "SHA-256 of 0x2C to the end"),
    (0x2C, 32, "root_public_key_hash", "bytes", "SHA-256 of the root public key, X then Y"),
    (0x4C, 16, "permissions_mask", "permissions", "most the board will ever grant (RM0481 Table 716)"),
    (0x5C, 16, "reserved", "bytes", "zero in ST's .obk and ours"),
]


def decode(raw, how):
    if how == "bytes":
        return "zero" if not any(raw) else ""
    value = int.from_bytes(raw, "little")
    if how == "address":
        return f"0x{value:08X}"
    if how == "int":
        return f"{value}" if value < 10 else f"0x{value:x} ({value})"
    if how == "permissions":
        return f"0x{value:x}"
    return f"{value} = {how.get(value, 'unknown')}"


def root_public_key_xy(pem_path):
    public_key = serialization.load_pem_public_key(pem_path.read_bytes())
    point = public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return point[1:]


def root_public_key_hash(pem_path):
    return hashlib.sha256(root_public_key_xy(pem_path)).digest()


def write_password_file(pem_path, out_path):
    """
    ## If you accidentally provisioned with TrustZone off

    If `./da.sh` prompts `please enter your password file path` instead of using
    the certificate fies, it means you didn't enable Trustzone (TZEN) before
    changing PRODUCT_STATE to provisioned or closed. Our `provisioned.sh` script
    should prevent this, but if you didn't use the `provision.sh` script and
    provisioned manually instead, it's possible to end up in this state.

    This note is just to mention that your device is not bricked. There is still
    a password and you have it! It's the SHA-256 has of the root public key's X
    and Y at offset `0x2C` in `DA_Config.obk`. Password mode compares
    `SHA-256(password)` against it, so the password that unlocks the board is
    that 64-byte X‖Y point. This function builds the password file (ST wants an
    8-byte header, then the bytes) from root_public_key_xy() above, and every
    make_obk.py run writes it next to the .obk, e.g. ../DA/Binary/password.bin.

    Then regress, giving that file when prompted:

    ``` bash
    cd ROT_Provisioning/scripts
    ./da.sh regress    # at the password prompt, enter ../DA/Binary/password.bin
    ```

    Once the board is back in Open, re-enable TrustZone before anything else
    (`STM32_Programmer_CLI -c port=SWD mode=UR -ob TZEN=0xB4`), restore the
    watermarks (run `check_option_bytes.py` to see what's wrong), reflash, and
    re-provision.
    """
    xy = root_public_key_xy(pem_path)
    # ST's password file: type 0x80000000, length, then the password bytes
    out_path.write_bytes(struct.pack("<II", 0x80000000, len(xy)) + xy)
    print(f"wrote {out_path} ({len(xy)}-byte password for a board provisioned with TZEN off)")


def check_obk(obk, root_public_key_pem):
    """Print every field and check both hashes; True if the .obk is good."""
    print(f"{len(obk)} bytes")
    print(f"  {'addr':<6} {'hex (as stored)':<32}  {'decoded':<28} field: description")
    for offset, size, field, how, description in FIELDS:
        raw = obk[offset:offset + size]
        hex_lines = [raw[i:i + 16].hex() for i in range(0, size, 16)]
        print(f"  0x{offset:03x}  {hex_lines[0]:<32}  {decode(raw, how):<28} {field}: {description}")
        for line in hex_lines[1:]:
            print(f"  {'':<6} {line:<32}")
        if how == "permissions":
            value = int.from_bytes(raw, "little")
            for bit, meaning in PERMISSION_BITS.items():
                print(f"  {'':<6} {'':<32}  {'bit ' + str(bit) + (' set' if value >> bit & 1 else ' clear'):<28} {meaning}")

    size_ok = len(obk) == HEADER_LEN + int.from_bytes(obk[0x04:0x08], "little")
    integrity_ok = hashlib.sha256(obk[0x2C:]).digest() == obk[INTEGRITY_HASH_BYTES]
    print(f"  size: {'ok' if size_ok else 'BAD'} (header + size field = file length)")
    print(f"  integrity_hash: {'ok' if integrity_ok else 'BAD'} (matches SHA-256 of 0x2C to the end)")
    ok = size_ok and integrity_ok
    if root_public_key_pem.exists():
        root_ok = root_public_key_hash(root_public_key_pem) == obk[ROOT_HASH_BYTES]
        print(f"  root_public_key_hash: {'ok' if root_ok else 'BAD'} (matches {root_public_key_pem})")
        ok = ok and root_ok
    else:
        print(f"  root_public_key_hash: not checked, {root_public_key_pem} not found")
    print(f"\nobk {'ok' if ok else 'BAD'}")
    return ok


def make_obk():
    # TPC rejects relative paths in the XML ("Wrong Path ... for Debug Authentication root key")
    xml = (HERE / "DA_Config.xml").read_text().replace("@HERE@", str(HERE))
    with tempfile.NamedTemporaryFile("w", suffix=".xml") as temp_xml:
        temp_xml.write(xml)
        temp_xml.flush()
        result = subprocess.run(["STM32TrustedPackageCreator_CLI", "-obk", temp_xml.name],
                                capture_output=True, text=True)
    output = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout + result.stderr)
    if "SUCCESS OBKey File Generation" not in output:
        print(output)
        sys.exit("STM32TrustedPackageCreator_CLI failed")
    print("wrote DA_Config.obk")
    return HERE / "DA_Config.obk"


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", metavar="OBK",
                    help="only parse and verify this .obk, e.g. ST's DA/Binary/DA_Config.obk")
parser.add_argument("--root-public-key", metavar="PEM", type=Path, default=HERE / "keys" / "root_public_key.pem",
                    help="root public key the .obk should hold (default: keys/root_public_key.pem)")
args = parser.parse_args()

if args.check:
    obk_path = Path(args.check)
else:
    obk_path = make_obk()

if not check_obk(obk_path.read_bytes(), args.root_public_key):
    sys.exit(1)
write_password_file(args.root_public_key, obk_path.parent / "password.bin")
