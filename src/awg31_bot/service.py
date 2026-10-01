"""What the commands do, apart from Telegram: add, reissue, delete and list clients.

Every change goes to the running interface first and to the peers file second. If the file cannot
be written, the interface change is rolled back, so the two never disagree for longer than one
command. The private key of a client lives only in the Issued it returns: nothing stores it, and a
lost config is reissued with new keys.

Every change that took effect is logged with the device's name and address, and no key: the journal
says who was let in or shut out, and when.
"""

import asyncio
import logging
import time
from dataclasses import dataclass

from . import amnezia, clientconf, ipam, peers
from .awg import Awg, PeerState
from .config import Config

log = logging.getLogger(__name__)


class ClientError(Exception):
    """A request the bot refuses, with a message fit for the chat."""


@dataclass(frozen=True)
class Issued:
    name: str
    address: str
    conf: str
    link: str
    qr_chunks: list[str]


@dataclass(frozen=True)
class ClientInfo:
    name: str
    address: str
    state: PeerState | None


class Service:
    def __init__(self, config: Config, awg: Awg):
        self.config = config
        self.awg = awg
        self._lock = asyncio.Lock()

    @staticmethod
    def check_name(name: str) -> str:
        if not peers.NAME_RE.match(name):
            raise ClientError(
                "A name is Latin letters, digits, '.', '-' or '_', up to 32 characters, "
                "starting with a letter or a digit"
            )
        return name

    def _find(self, current: list[peers.Peer], name: str) -> peers.Peer | None:
        return next((p for p in current if p.name == name), None)

    async def _issue(self, name: str, address) -> tuple[peers.Peer, Issued]:
        keys = await self.awg.keypair()
        psk = await self.awg.psk()
        server = clientconf.parse_server(await self.awg.showconf(), await self.awg.public_key())
        client = clientconf.Client(name, keys.private, keys.public, psk, address)
        c = self.config
        conf = clientconf.render(server, client, c.endpoint, c.dns, c.mtu)
        payload = amnezia.config_json(
            server,
            client,
            endpoint=c.endpoint,
            dns=c.dns,
            mtu=c.mtu,
            server_ip=str(c.server_ip),
            prefix=c.network.prefixlen,
            # The name AmneziaVPN gives the connection; a phone shows a dozen characters of it
            description=c.name,
        )
        issued = Issued(name, str(address), conf, amnezia.vpn_link(payload), amnezia.qr_chunks(payload))
        return peers.Peer(name, keys.public, address, psk), issued

    async def _commit(self, updated: list[peers.Peer], undo) -> None:
        try:
            peers.write(self.config.peers_file, updated)
        except OSError:
            await undo()
            raise

    async def add(self, name: str) -> Issued:
        self.check_name(name)
        async with self._lock:
            with peers.locked(self.config.peers_file):
                current = peers.read(self.config.peers_file)
                if self._find(current, name):
                    raise ClientError(f"{name} exists already; a new config for it is /reissue {name}")
                try:
                    address = ipam.next_free(
                        self.config.network, self.config.server_ip, (p.address for p in current)
                    )
                except ipam.NetworkFull as e:
                    raise ClientError(str(e)) from None
                peer, issued = await self._issue(name, address)
                await self.awg.add_peer(peer.public_key, peer.preshared_key, str(address))
                await self._commit(current + [peer], lambda: self.awg.remove_peer(peer.public_key))
                log.info("added %s at %s", name, address)
                return issued

    async def reissue(self, name: str) -> Issued:
        self.check_name(name)
        async with self._lock:
            with peers.locked(self.config.peers_file):
                current = peers.read(self.config.peers_file)
                old = self._find(current, name)
                if not old:
                    raise ClientError(f"There is no {name}; a new device is /add {name}")
                peer, issued = await self._issue(name, old.address)
                # One address, one peer: the old key goes before the new one takes the address
                await self.awg.remove_peer(old.public_key)
                try:
                    await self.awg.add_peer(peer.public_key, peer.preshared_key, str(old.address))
                except Exception:
                    await self._restore(old)
                    raise

                async def undo():
                    await self.awg.remove_peer(peer.public_key)
                    await self._restore(old)

                await self._commit([peer if p is old else p for p in current], undo)
                log.info("reissued %s at %s; the old key is out", name, old.address)
                return issued

    async def _restore(self, old: peers.Peer) -> None:
        await self.awg.add_peer(old.public_key, old.preshared_key, str(old.address))

    async def delete(self, name: str) -> str:
        self.check_name(name)
        async with self._lock:
            with peers.locked(self.config.peers_file):
                current = peers.read(self.config.peers_file)
                old = self._find(current, name)
                if not old:
                    raise ClientError(f"There is no {name}")
                await self.awg.remove_peer(old.public_key)
                await self._commit([p for p in current if p is not old], lambda: self._restore(old))
                log.info("deleted %s; %s is free", name, old.address)
                return str(old.address)

    async def list(self) -> list[ClientInfo]:
        current = peers.read(self.config.peers_file)
        states = await self.awg.dump()
        return [
            ClientInfo(p.name or f"(no name) {p.public_key[:8]}…", str(p.address), states.get(p.public_key))
            for p in sorted(current, key=lambda p: p.address)
        ]


def describe(info: ClientInfo, now: float | None = None) -> str:
    """One line of /list: name, address, the latest handshake and the traffic."""
    now = time.time() if now is None else now
    s = info.state
    if s is None:
        return f"{info.name} — {info.address}, not on the interface"
    if s.latest_handshake == 0:
        seen = "never connected"
    else:
        ago = int(now - s.latest_handshake)
        seen = f"handshake {_duration(ago)} ago"
    return f"{info.name} — {info.address}, {seen}, ↓{_bytes(s.tx)} ↑{_bytes(s.rx)}"


def _duration(seconds: int) -> str:
    if seconds < 90:
        return f"{seconds} s"
    if seconds < 90 * 60:
        return f"{seconds // 60} min"
    if seconds < 36 * 3600:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


# Binary units, named as such: the numbers then match what awg show prints
def _bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    value = n / 1024
    for unit in ("KiB", "MiB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GiB"
