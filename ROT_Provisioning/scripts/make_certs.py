#!/usr/bin/env python3
"""Build the DA certificate chain from keys/, or --check an existing chain. Format: README.md."""

import argparse
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature

HERE = Path(__file__).resolve().parent

# Every debug level, secure and non-secure, plus both regressions (RM0481 Table 716)
PERMISSIONS = 0x5077

CERT_LEN = 220
TLV_TYPE = 0x0201
SIGNED_BYTES = slice(0x08, 0x9C)
PERMISSION_BYTES = slice(0x2C, 0x3C)
PUBLIC_KEY_BYTES = slice(0x3C, 0x7C)
SIGNATURE_BYTES = slice(0x9C, 0xDC)

# Only the values ST's samples and this script use; anything else prints as unknown
ROLES = {1: "root", 2: "intermediate", 3: "leaf"}
SIGNATURE_TYPES = {1: "ECDSA P-256, SHA-256"}
KEY_TYPES = {1: "ECDSA P-256"}
TLV_TYPES = {TLV_TYPE: "ADAC certificate"}
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

# (offset, size, field, how to decode, what it's for); layout from Arm PSA ADAC certificate_p256_p256_t
FIELDS = [
    (0x00, 2, "tlv.reserved", "int", "must be 0"),
    (0x02, 2, "tlv.type", TLV_TYPES, "what this blob is"),
    (0x04, 4, "tlv.length", "int", "bytes after this 8-byte TLV header"),
    (0x08, 1, "format_version.major", "int", "certificate format version"),
    (0x09, 1, "format_version.minor", "int", ""),
    (0x0A, 1, "signature_type", SIGNATURE_TYPES, "how the issuer signed this certificate"),
    (0x0B, 1, "key_type", KEY_TYPES, "type of the public key below"),
    (0x0C, 1, "role", ROLES, "place in the chain"),
    (0x0D, 1, "usage", "int", "1 in ST's samples"),
    (0x0E, 2, "reserved", "int", "must be 0"),
    (0x10, 2, "lifecycle", "int", "0 = no life-cycle restriction"),
    (0x12, 2, "oem_constraint", "int", "0 = no OEM restriction"),
    (0x14, 4, "extensions_bytes", "int", "size of extensions; 0 = none"),
    (0x18, 4, "soc_class", "int", "0 = any SoC class"),
    (0x1C, 16, "soc_id", "bytes", "all zero = any chip"),
    (0x2C, 16, "permissions_mask", "permissions", "what this certificate may grant (RM0481 Table 716)"),
    (0x3C, 32, "public_key.x", "bytes", "this certificate's public key, big-endian"),
    (0x5C, 32, "public_key.y", "bytes", ""),
    (0x7C, 32, "extensions_hash", "bytes", "SHA-256 of the extensions; zero = none"),
    (0x9C, 32, "signature.r", "bytes", "issuer's signature over 0x08-0x9B, big-endian"),
    (0xBC, 32, "signature.s", "bytes", ""),
]


def raw_public_key(public_key):
    return public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)[1:]


def load_private_key(level):
    return serialization.load_pem_private_key((HERE / "keys" / f"{level}_private_key.pem").read_bytes(), password=None)


def build(role, public_key, issuer_private_key):
    cert = bytearray(CERT_LEN)
    cert[0x02:0x04] = TLV_TYPE.to_bytes(2, "little")
    cert[0x04:0x08] = (CERT_LEN - 8).to_bytes(4, "little")
    # version 1.0, ECDSA P-256 with SHA-256, P-256 public key, role, usage
    cert[0x08:0x0E] = bytes([1, 0, 1, 1, role, 1])
    cert[PERMISSION_BYTES] = PERMISSIONS.to_bytes(16, "little")
    cert[PUBLIC_KEY_BYTES] = raw_public_key(public_key)
    r, s = decode_dss_signature(issuer_private_key.sign(bytes(cert[SIGNED_BYTES]), ec.ECDSA(hashes.SHA256())))
    cert[SIGNATURE_BYTES] = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return bytes(cert)


def decode(raw, how):
    if how == "bytes":
        return "zero" if not any(raw) else ""
    value = int.from_bytes(raw, "little")
    if how == "int":
        return f"{value}" if value < 10 else f"0x{value:x} ({value})"
    if how == "permissions":
        return f"0x{value:x}"
    shown = f"{value}" if value < 10 else f"0x{value:04x}"
    return f"{shown} = {how.get(value, 'unknown')}"


def print_certificate(cert):
    print(f"  {'addr':<6} {'hex (as stored)':<32}  {'decoded':<28} field: description")
    for offset, size, field, how, description in FIELDS:
        raw = cert[offset:offset + size]
        hex_lines = [raw[i:i + 16].hex() for i in range(0, size, 16)]
        print(f"  0x{offset:03x}  {hex_lines[0]:<32}  {decode(raw, how):<28} {field}{': ' + description if description else ''}")
        for line in hex_lines[1:]:
            print(f"  {'':<6} {line:<32}")
        if how == "permissions":
            value = int.from_bytes(raw, "little")
            for bit, meaning in PERMISSION_BITS.items():
                print(f"  {'':<6} {'':<32}  {'bit ' + str(bit) + (' set' if value >> bit & 1 else ' clear'):<28} {meaning}")


def check_chain(chain):
    """Print every field of every certificate and verify the signatures; True if the whole chain is good."""
    ok = len(chain) == 3 * CERT_LEN
    print(f"chain: {len(chain)} bytes, expected {3 * CERT_LEN} (root + intermediate + leaf) -> {'ok' if ok else 'BAD'}")
    issuer_public_key = None
    issuer_name = None
    for index in range(len(chain) // CERT_LEN):
        cert = chain[index * CERT_LEN:(index + 1) * CERT_LEN]
        role = cert[0x0C]
        name = ROLES.get(role, f"role {role}")
        print(f"\n{name} certificate, chain bytes 0x{index * CERT_LEN:03x}-0x{(index + 1) * CERT_LEN - 1:03x}")
        print_certificate(cert)

        public_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), b"\x04" + cert[PUBLIC_KEY_BYTES])
        # The root signs itself
        if issuer_public_key is None:
            issuer_public_key, issuer_name = public_key, name
        signature = cert[SIGNATURE_BYTES]
        der = encode_dss_signature(int.from_bytes(signature[:32], "big"), int.from_bytes(signature[32:], "big"))
        try:
            issuer_public_key.verify(der, cert[SIGNED_BYTES], ec.ECDSA(hashes.SHA256()))
            signature_ok = True
        except Exception:
            signature_ok = False
        header_ok = (int.from_bytes(cert[0x02:0x04], "little") == TLV_TYPE
                     and int.from_bytes(cert[0x04:0x08], "little") == CERT_LEN - 8
                     and role == index + 1)
        print(f"  header: {'ok' if header_ok else 'BAD'} (TLV type, length, and role {index + 1} in position {index + 1})")
        print(f"  signature: {'ok' if signature_ok else 'BAD'} (checked with the {issuer_name} public key)")
        ok = ok and header_ok and signature_ok
        issuer_public_key, issuer_name = public_key, name
    print(f"\nchain {'ok' if ok else 'BAD'}")
    return ok


def make_chain():
    root = load_private_key("root")
    intermediate = load_private_key("intermediate")
    leaf = load_private_key("leaf")
    certs = {
        "root": build(1, root.public_key(), root),
        "intermediate": build(2, intermediate.public_key(), root),
        "leaf": build(3, leaf.public_key(), intermediate),
    }
    (HERE / "certs").mkdir(exist_ok=True)
    for name, cert in certs.items():
        (HERE / "certs" / f"{name}.b64").write_text(base64.b64encode(cert).decode())
    chain = certs["root"] + certs["intermediate"] + certs["leaf"]
    # The programmer's cert= argument takes the whole chain, root first
    (HERE / "certs" / "leaf_chain.b64").write_text(base64.b64encode(chain).decode())
    print("wrote certs/root.b64, intermediate.b64, leaf.b64, leaf_chain.b64")
    return chain


parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--check", metavar="CHAIN_B64",
                    help="only parse and verify this chain, e.g. ST's DA/Certificates/cert_leaf_chain.b64")
args = parser.parse_args()

if args.check:
    chain = base64.b64decode(Path(args.check).read_text())
else:
    chain = make_chain()

sys.exit(0 if check_chain(chain) else 1)
