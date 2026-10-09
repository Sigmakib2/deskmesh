"""Pairing by ephemeral Diffie-Hellman with a human-checked four-digit code.

Neither PC needs a pre-shared secret. The exchange leaves both sides holding the
same secret, and each shows a four-digit code derived from it. Someone merely
listening on the LAN learns nothing. Someone sitting in the middle ends up with
a different secret on each side, so the two codes disagree and the person doing
the pairing declines.

The long-term 32-byte input/audio key is then handed over wrapped under a pad
derived from that shared secret, so it never crosses the network in the clear.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

# RFC 3526 group 14 (2048-bit MODP). Fixed, public parameters.
MODP_2048 = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD1"
    "29024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245"
    "E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3D"
    "C2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D"
    "670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9"
    "DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AACAA68FFFFFFFFFFFFFFFF",
    16,
)
GENERATOR = 2
EXPONENT_BITS = 256
PUBLIC_BYTES = 256  # 2048 bits, so every public value is a fixed width.
KEY_BYTES = 32
MAC_BYTES = 32
PUBLIC_HEX_LENGTH = PUBLIC_BYTES * 2


def _derive(shared: bytes, label: bytes, size: int) -> bytes:
    return hashlib.sha256(label + b"\0" + shared).digest()[:size]


class Exchange:
    """One side's ephemeral key pair. Never reuse an instance across pairings."""

    def __init__(self) -> None:
        self.private = secrets.randbits(EXPONENT_BITS) | 1
        self.public = pow(GENERATOR, self.private, MODP_2048)

    @property
    def public_hex(self) -> str:
        return self.public.to_bytes(PUBLIC_BYTES, "big").hex()

    def shared(self, peer_hex: str) -> bytes:
        if not isinstance(peer_hex, str) or len(peer_hex) != PUBLIC_HEX_LENGTH:
            raise ValueError("Pairing public value has the wrong size")
        try:
            peer = int(peer_hex, 16)
        except ValueError as exc:
            raise ValueError("Pairing public value is not hexadecimal") from exc
        # Reject 0, 1 and p-1, which would force a known or tiny-order secret.
        if not 2 <= peer <= MODP_2048 - 2:
            raise ValueError("Pairing public value is out of range")
        secret = pow(peer, self.private, MODP_2048)
        if secret in (1, MODP_2048 - 1):
            raise ValueError("Pairing produced a degenerate shared secret")
        return secret.to_bytes(PUBLIC_BYTES, "big")


def short_code(shared: bytes, initiator_hex: str, responder_hex: str) -> str:
    """The four digits a person compares on both screens.

    Binding both public values in means a man in the middle cannot steer the two
    sides towards the same code.
    """
    digest = hashlib.sha256(b"deskmesh-sas\0" + bytes.fromhex(initiator_hex) + bytes.fromhex(responder_hex) + shared).digest()
    return f"{int.from_bytes(digest[:4], 'big') % 10000:04d}"


def wrap_key(shared: bytes, key: bytes) -> tuple[str, str]:
    if len(key) != KEY_BYTES:
        raise ValueError("Key must contain 32 bytes")
    pad = _derive(shared, b"deskmesh-wrap", KEY_BYTES)
    wrapped = bytes(left ^ right for left, right in zip(key, pad))
    tag = hmac.new(_derive(shared, b"deskmesh-mac", MAC_BYTES), wrapped, hashlib.sha256).hexdigest()
    return wrapped.hex(), tag


def unwrap_key(shared: bytes, wrapped_hex: str, tag: str) -> bytes:
    try:
        wrapped = bytes.fromhex(wrapped_hex)
    except ValueError as exc:
        raise ValueError("Wrapped pairing key is not hexadecimal") from exc
    if len(wrapped) != KEY_BYTES:
        raise ValueError("Wrapped pairing key has the wrong size")
    expected = hmac.new(_derive(shared, b"deskmesh-mac", MAC_BYTES), wrapped, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Pairing key failed its integrity check")
    pad = _derive(shared, b"deskmesh-wrap", KEY_BYTES)
    return bytes(left ^ right for left, right in zip(wrapped, pad))
