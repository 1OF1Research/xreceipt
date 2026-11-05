#!/usr/bin/env python3
"""XReceipt verifier — check a signed receipt with zero dependencies.

Usage:  python3 verify.py [receipt_id]
        python3 verify.py emb_afea0952fd1f47c68c04643a

What it proves, in order:
  1. the crypto works (offline self-test, before any network)
  2. the signer document is fetched and its scheme matches this code
  3. the receipt page exists and names a receipt hash + signature
  4. the canonical bytes served by the machine hash to that receipt hash
  5. the canonical bytes name the requested receipt id
  6. the ECDSA signature recovers to a published receipt signer
     (current or retired — the signer document lists both)
  7. the linked settlement transaction is confirmed on-chain (Base)

Nothing here is secret: the signer key is published by the machine itself,
and every check reads public bytes. The private signing key never leaves
the machine — that is the whole point.
"""
import hashlib
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://aemb.pro"
SAMPLE_RECEIPT = "emb_afea0952fd1f47c68c04643a"
EXPECTED_SCHEME = "eip191-personal-sign"
EXPECTED_DIGEST = "sha256"
MAX_BODY = 1000001  # 1MB read cap; receipts are ~3KB
FETCH_TIMEOUT = 30  # measured: a single live fetch took 15.3s; do not shorten

# Offline self-test vectors (public constants, pinned to the live sample).
_KECCAK_EMPTY = (
    "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
)
_VEC_DIGEST = (
    "f40c1ee20e67f512e64eb73e47d48fe9090681bfb623449e7f4f60815a22b3cf"
)
_VEC_SIG = (
    "f1f4a2e9d12c9f89dd657084879fae10a3f337280b899328e72e238460e3c21a"
    "123f15b060f8568053dac5c5917eed96f2ee144ca06a3ed1620ec4e753e74fbb1b"
)
_VEC_ADDR = "0x0c3704A744A594175e1A31F1B1e4AeEc5a96289F"

# ---------------------------------------------------------------- keccak-256
# Original Keccak padding (0x01), NOT NIST SHA3 (0x06). hashlib.sha3_256
# is the wrong function for Ethereum; this is the right one, in ~40 lines.

_KECCAK_RNDC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A,
    0x8000000080008000, 0x000000000000808B, 0x0000000080000001,
    0x8000000080008081, 0x8000000000008009, 0x000000000000008A,
    0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089,
    0x8000000000008003, 0x8000000000008002, 0x8000000000000080,
    0x000000000000800A, 0x800000008000000A, 0x8000000080008081,
    0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]
_KECCAK_PILN = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4, 15,
                23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1]
_KECCAK_ROTC = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14, 27, 41,
                56, 8, 25, 43, 62, 18, 39, 61, 20, 44]
_MASK64 = (1 << 64) - 1


def _rol(x, n):
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _MASK64


def keccak_256(data: bytes) -> bytes:
    suffix, rate = b"\x01", 136  # keccak-256: rate 1088 bits
    s = [0] * 25
    # absorb, byte-oriented
    blocks = len(data) // rate
    off = 0
    for _ in range(blocks):
        for i in range(rate // 8):
            s[i] ^= int.from_bytes(data[off + i * 8:off + i * 8 + 8], "little")
        off += rate
        s = _keccak_f(s)
    tail = bytearray(data[off:]) + suffix
    while len(tail) < rate:
        tail += b"\x00"
    tail[-1] |= 0x80
    for i in range(rate // 8):
        s[i] ^= int.from_bytes(tail[i * 8:i * 8 + 8], "little")
    s = _keccak_f(s)
    return b"".join(v.to_bytes(8, "little") for v in s[:4])


def _keccak_f(s):
    # theta · rho/pi · chi · iota — the five Keccak-f[1600] steps, in order
    for rnd in range(24):
        bc = [s[i] ^ s[i + 5] ^ s[i + 10] ^ s[i + 15] ^ s[i + 20]
              for i in range(5)]
        for i in range(5):
            t = bc[(i + 4) % 5] ^ _rol(bc[(i + 1) % 5], 1)
            for j in range(0, 25, 5):
                s[j + i] ^= t
        t = s[1]
        for i in range(24):
            j = _KECCAK_PILN[i]
            bc[0] = s[j]
            s[j] = _rol(t, _KECCAK_ROTC[i])
            t = bc[0]
        for j in range(0, 25, 5):
            for i in range(5):
                bc[i] = s[j + i]
            for i in range(5):
                s[j + i] ^= (~bc[(i + 1) % 5]) & bc[(i + 2) % 5]
        s[0] ^= _KECCAK_RNDC[rnd]
    return [v & _MASK64 for v in s]


# ------------------------------------------------------- secp256k1 ecrecover
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_GX = 0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798
_GY = 0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8
_INF = None


def _padd(p1, p2):
    if p1 is _INF:
        return p2
    if p2 is _INF:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2:
        if (y1 + y2) % _P == 0:
            return _INF
        lam = (3 * x1 * x1) * pow(2 * y1, _P - 2, _P) % _P  # doubling
    else:
        lam = ((y2 - y1) * pow(x2 - x1, _P - 2, _P)) % _P
    x3 = (lam * lam - x1 - x2) % _P
    return (x3, (lam * (x1 - x3) - y1) % _P)


def _pmul(k, pt):
    r = _INF
    while k:
        if k & 1:
            r = _padd(r, pt)
        pt = _padd(pt, pt)
        k >>= 1
    return r


def ecrecover_address(digest32: bytes, sig65: bytes) -> str:
    """Recover the signer address from a 32-byte digest + 65-byte sig.

    Raises ValueError on any malformed input — the caller turns it into
    a clean FAIL, never a traceback.
    """
    r = int.from_bytes(sig65[:32], "big")
    s = int.from_bytes(sig65[32:64], "big")
    v = sig65[64]
    if v not in (27, 28):
        raise ValueError(f"unsupported recovery id v={v}")
    if not (1 <= r < _N and 1 <= s < _N):
        raise ValueError("malformed signature")
    recid = v - 27
    e = int.from_bytes(
        keccak_256(b"\x19Ethereum Signed Message:\n32" + digest32), "big")
    x = r  # r < N < P, so x = r is the valid field element
    y2 = (pow(x, 3, _P) + 7) % _P
    y = pow(y2, (_P + 1) // 4, _P)
    if (y * y) % _P != y2:
        raise ValueError("r is not a curve x-coordinate")
    if (y & 1) != recid:
        y = _P - y
    R = (x, y)
    r_inv = pow(r, _N - 2, _N)
    # Q = r^-1 * (s*R - e*G)
    eG = _pmul(e, (_GX, _GY))
    sR = _pmul(s, R)
    neg_eG = (eG[0], _P - eG[1]) if eG is not _INF else _INF
    Q = _pmul(r_inv, _padd(sR, neg_eG))
    if Q is _INF:
        raise ValueError("recovery produced infinity")
    pub = Q[0].to_bytes(32, "big") + Q[1].to_bytes(32, "big")
    return "0x" + keccak_256(pub)[-20:].hex()


# ------------------------------------------------------------------ verifier
def _self_test():
    """Prove the instrument before phoning home. Returns an error string."""
    if keccak_256(b"").hex() != _KECCAK_EMPTY:
        return "keccak-256 vector mismatch"
    try:
        addr = ecrecover_address(bytes.fromhex(_VEC_DIGEST),
                                 bytes.fromhex(_VEC_SIG))
    except ValueError as e:
        return f"ecrecover raised on known vector: {e}"
    if addr.lower() != _VEC_ADDR.lower():
        return "ecrecover known-answer mismatch"
    return None


def fetch(url: str):
    """Fetch a URL. Returns (body, status) where status is one of
    'ok', 'not-found' (HTTP 404), 'too-large', 'redirected', 'unreachable'.
    Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": "xreceipt-verifier/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as r:
            body = r.read(MAX_BODY + 1)
            if len(body) > MAX_BODY:
                return None, "too-large"
            host = urllib.parse.urlparse(url).hostname
            if urllib.parse.urlparse(r.geturl()).hostname != host:
                return None, "redirected"
            return body, "ok"
    except urllib.error.HTTPError as e:
        return None, "not-found" if e.code == 404 else "unreachable"
    except Exception:
        return None, "unreachable"


def _json(raw: bytes):
    """Parse JSON object bytes; None on any failure. Never raises."""
    try:
        doc = json.loads(raw)
    except Exception:
        return None
    return doc if isinstance(doc, dict) else None


def verify(receipt_id: str) -> bool:
    passed = 1  # the offline crypto self-test, printed by main

    def good(name):
        nonlocal passed
        passed += 1
        print("PASS " + name)

    def bad(msg):
        print("FAIL " + msg)
        return False

    # --- trust root first: the signer document names the scheme and the keys
    raw, status = fetch(f"{BASE}/.well-known/embassy-signer.json")
    if status == "unreachable":
        return bad("signer document unreachable (network failure)")
    if raw is None:
        return bad(f"signer document fetch failed ({status})")
    doc = _json(raw)
    if doc is None:
        return bad("signer document is not JSON")
    if doc.get("scheme") != EXPECTED_SCHEME:
        return bad(f"signer scheme changed: {doc.get('scheme')!r}")
    if doc.get("canonicalization", {}).get("digest") != EXPECTED_DIGEST:
        return bad("signer digest changed")
    key_id = doc.get("key_id", "?")
    allowed = {doc["signer"].lower(): f"current ({key_id})"} \
        if doc.get("signer") else {}
    for rk in doc.get("retired_keys") or []:
        if rk.get("signer"):
            allowed.setdefault(rk["signer"].lower(),
                               f"retired ({rk.get('key_id', '?')})")
    if not allowed:
        return bad("signer document names no keys")
    print(f"trust root: {BASE} · key {key_id}")
    good("signer document names a known scheme")

    # --- the receipt page
    raw, status = fetch(f"{BASE}/verify/{receipt_id}")
    if status == "not-found":
        return bad("receipt not found")
    if status == "unreachable":
        return bad("receipt page unreachable (network failure)")
    if raw is None:
        return bad(f"receipt page fetch failed ({status})")
    page = raw.decode("utf-8", "replace")
    m_sig = re.search(r"Signature</th><td[^>]*>\s*0x([0-9a-fA-F]{130})", page)
    m_hash = re.search(r"Receipt hash</th><td[^>]*>\s*([0-9a-fA-F]{64})", page)
    m_tx = re.search(r"basescan\.org/tx/(0x[0-9a-fA-F]{64})", page)
    if not (m_sig and m_hash):
        return bad("receipt page has no signature block")
    sig = bytes.fromhex(m_sig.group(1))
    claimed_hash = m_hash.group(1).lower()
    good("receipt page carries signature + hash")

    # --- the canonical bytes
    raw, status = fetch(f"{BASE}/verify/{receipt_id}/canonical")
    if status == "unreachable":
        return bad("canonical bytes unreachable (network failure)")
    if raw is None:
        return bad(f"canonical bytes fetch failed ({status})")
    digest = hashlib.sha256(raw).hexdigest()
    if digest != claimed_hash:
        return bad(f"sha256(canonical)={digest} != {claimed_hash}")
    good("canonical bytes hash to receipt hash")
    canon = _json(raw)
    if canon is None or canon.get("receipt_id") != receipt_id:
        return bad("canonical bytes do not name this receipt id")
    good("canonical bytes name the requested receipt id")

    # --- the signature
    try:
        recovered = ecrecover_address(bytes.fromhex(digest), sig)
    except ValueError as e:
        return bad(f"signature invalid: {e}")
    label = allowed.get(recovered.lower())
    if label is None:
        return bad(f"recovered {recovered} is not a published signer")
    good(f"signature recovers to published signer ({label})")

    # --- the chain: the LINKED tx is confirmed (not "this receipt settled")
    if not m_tx:
        return bad("receipt names no settlement transaction")
    tx = m_tx.group(1)
    raw, status = fetch(f"https://base.blockscout.com/api/v2/transactions/{tx}")
    if raw is None:
        return bad(f"settlement lookup failed ({status})")
    info = _json(raw)
    if not info or info.get("status") != "ok":
        return bad(f"settlement {tx[:10]}… not confirmed on-chain")
    good(f"linked settlement {tx[:10]}… confirmed on-chain (Base)")

    print(f"{passed}/7 checks passed — receipt verified")
    return True


if __name__ == "__main__":
    err = _self_test()
    if err:
        print("FAIL self-test: " + err)
        sys.exit(1)
    print("PASS crypto self-test (offline)")
    rid = sys.argv[1] if len(sys.argv) > 1 else SAMPLE_RECEIPT
    if not re.fullmatch(r"emb_[0-9a-f]{24}", rid):
        sys.exit(f"usage: python3 {sys.argv[0]} emb_<24 hex chars>")
    print(f"verifying {rid} …")
    sys.exit(0 if verify(rid) else 1)
