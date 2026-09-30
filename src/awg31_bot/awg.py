"""The `awg` tool of amneziawg-tools, run as a subprocess against one interface.

Keys are made by `awg genkey`, `awg pubkey` and `awg genpsk`, so the bot has no crypto of its own.
Changes to the running interface go through `awg set`: the kernel module applies them on the fly,
and the other clients keep their sessions. Needs CAP_NET_ADMIN.
"""

import asyncio
from dataclasses import dataclass

TIMEOUT = 10


class AwgError(Exception):
    pass


@dataclass(frozen=True)
class KeyPair:
    private: str
    public: str


@dataclass(frozen=True)
class PeerState:
    public_key: str
    endpoint: str | None
    latest_handshake: int
    rx: int
    tx: int


class Awg:
    def __init__(self, binary: str, interface: str):
        self.binary = binary
        self.interface = interface

    async def run(self, *args: str, stdin: str | None = None) -> str:
        proc = await asyncio.create_subprocess_exec(
            self.binary,
            *args,
            stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(stdin.encode() if stdin is not None else None), TIMEOUT
            )
        except TimeoutError:
            proc.kill()
            await proc.wait()
            raise AwgError(f"awg {args[0]} timed out") from None
        if proc.returncode != 0:
            # The arguments never carry a key: keys go through stdin
            detail = err.decode().strip() or f"exit {proc.returncode}"
            raise AwgError(f"awg {' '.join(args)}: {detail}")
        return out.decode()

    async def keypair(self) -> KeyPair:
        private = (await self.run("genkey")).strip()
        public = (await self.run("pubkey", stdin=private + "\n")).strip()
        return KeyPair(private, public)

    async def psk(self) -> str:
        return (await self.run("genpsk")).strip()

    async def add_peer(self, public_key: str, preshared_key: str | None, address: str) -> None:
        # /dev/stdin: the key never appears on a command line another process could read.
        # A peer added by hand may have no preshared key, and gets none back on a rollback.
        psk_args = ("preshared-key", "/dev/stdin") if preshared_key else ()
        await self.run(
            "set",
            self.interface,
            "peer",
            public_key,
            *psk_args,
            "allowed-ips",
            f"{address}/32",
            stdin=preshared_key + "\n" if preshared_key else None,
        )

    async def remove_peer(self, public_key: str) -> None:
        await self.run("set", self.interface, "peer", public_key, "remove")

    async def showconf(self) -> str:
        """The running configuration: the server's [Interface] with its 3.x set, and the clients."""
        return await self.run("showconf", self.interface)

    async def public_key(self) -> str:
        return (await self.run("show", self.interface, "public-key")).strip()

    async def dump(self) -> dict[str, PeerState]:
        """`awg show <if> dump`: the interface on the first line, then one tab-separated line per peer.

        The peer columns start with public key, preshared key, endpoint, allowed ips, latest
        handshake, rx, tx; amneziawg-tools may add columns after them.
        """
        lines = (await self.run("show", self.interface, "dump")).splitlines()[1:]
        states = {}
        for line in lines:
            cols = line.split("\t")
            states[cols[0]] = PeerState(
                public_key=cols[0],
                endpoint=None if cols[2] == "(none)" else cols[2],
                latest_handshake=int(cols[4]),
                rx=int(cols[5]),
                tx=int(cols[6]),
            )
        return states
