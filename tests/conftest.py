import base64
import hashlib
import ipaddress
import os
from pathlib import Path

import pytest

from awg31_bot.awg import AwgError, KeyPair, PeerState
from awg31_bot.config import Config


def b64key() -> str:
    return base64.b64encode(os.urandom(32)).decode()


def fake_public(private: str) -> str:
    """The fake interface's own key derivation: one public key per private key, like X25519."""
    return base64.b64encode(hashlib.sha256(private.encode()).digest()).decode()


# Three I packets the length of real QUIC Initials, 556 bytes each; random, so the repository
# carries nobody's server name
I_PACKETS = {f"I{n}": "<b 0x" + os.urandom(556).hex() + ">" for n in (1, 2, 3)}


def server_set(**changes) -> dict[str, str]:
    """A 3.x set as `awg showconf` prints it."""
    base = {
        "Jc": "3", "Jmin": "88", "Jmax": "140",
        "S1": "18", "S2": "98", "S3": "51", "S4": "20",
        "H1": "1", "H2": "2", "H3": "3", "H4": "4",
        **I_PACKETS,
        "HeaderProtectionKey": b64key(),
        "ContentPaddingAddition": "32-128",
        "RekeyAfterTime": "100-120", "RekeyTimeout": "3-7", "RejectAfterTime": "150-180",
        "KeepaliveTimeout": "5-15", "MaxHandshakeAttempts": "15-20",
        "RandomTrailers": "on",
    }  # fmt: skip
    return {**base, **changes}


class FakeInterface:
    """An AmneziaWG interface as the bot sees it through `awg`.

    Peers come and go; the server's key, port and 3.x set are what `showconf` reports and can be
    changed, as a regeneration would change them. `broken` names the operations that fail.
    """

    def __init__(self):
        self.interface = "awg0"
        self.private_key = b64key()
        self.port = 443
        self.set = server_set()
        # public key → (preshared key, address)
        self.peers: dict[str, tuple[str | None, str]] = {}
        self.handshakes: dict[str, PeerState] = {}
        self.broken: set[str] = set()
        # Keys the interface has held once and takes back; with refuse_new_keys, no other key
        self.known: set[str] = set()
        self.refuse_new_keys = False

    def _check(self, op: str) -> None:
        if op in self.broken:
            raise AwgError(f"awg {op}: Unable to modify interface")

    async def keypair(self) -> KeyPair:
        private = b64key()
        return KeyPair(private, fake_public(private))

    async def psk(self) -> str:
        return b64key()

    async def add_peer(self, public_key, preshared_key, address) -> None:
        self._check("set")
        if self.refuse_new_keys and public_key not in self.known:
            raise AwgError("awg set: Unable to modify interface: Invalid argument")
        self.known.add(public_key)
        self.peers[public_key] = (preshared_key, address)

    async def remove_peer(self, public_key) -> None:
        self._check("set")
        self.peers.pop(public_key, None)

    async def showconf(self) -> str:
        self._check("showconf")
        lines = ["[Interface]", f"ListenPort = {self.port}", f"PrivateKey = {self.private_key}"]
        lines += [f"{k} = {v}" for k, v in self.set.items()]
        for public, (psk, address) in self.peers.items():
            lines += ["", "[Peer]", f"PublicKey = {public}", f"AllowedIPs = {address}/32"]
            if psk:
                lines.append(f"PresharedKey = {psk}")
        return "\n".join(lines) + "\n"

    async def public_key(self) -> str:
        return fake_public(self.private_key)

    async def dump(self) -> dict[str, PeerState]:
        return {k: self.handshakes.get(k, PeerState(k, None, 0, 0, 0)) for k in self.peers}


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        token="123:abc",
        admin_ids=frozenset({42}),
        interface="awg0",
        peers_file=tmp_path / "awg0.peers.conf",
        endpoint="vpn.example.org",
        dns=("9.9.9.9",),
        mtu=1280,
        network=ipaddress.IPv4Network("10.66.66.0/24"),
        server_ip=ipaddress.IPv4Address("10.66.66.1"),
        name="example",
    )


@pytest.fixture
def iface() -> FakeInterface:
    return FakeInterface()


def parse_conf(text: str) -> dict[str, dict[str, str]]:
    """A .conf read the way wg-quick reads it: sections of Key = Value, the value after the first =."""
    sections: dict[str, dict[str, str]] = {}
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1], {})
        elif "=" in line and current is not None:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    return sections
