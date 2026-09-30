"""The peers file: [Peer] blocks, each preceded by a "# name = <client>" line.

The server's own awg0.conf carries [Interface] only, and PostUp loads this file with `awg addconf`.
The bot owns the file: it writes it whole and atomically, and applies every change to the running
interface with `awg set`. A block somebody added by hand without a name line is kept as it is.
"""

import fcntl
import ipaddress
import os
import re
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
_NAME_LINE = re.compile(r"^#\s*name\s*=\s*(\S+)\s*$")
_KEY_LINE = re.compile(r"^([A-Za-z0-9]+)\s*=\s*(.*?)\s*$")

HEADER = (
    "# The clients of the AmneziaWG interface, loaded by PostUp with awg addconf.\n"
    '# Kept by awg31-telegram-bot: every block is preceded by "# name = <client>".\n'
)


@dataclass
class Peer:
    name: str | None
    public_key: str
    address: ipaddress.IPv4Address
    preshared_key: str | None = None
    # Any other key of the block, kept in its order
    extra: dict[str, str] = field(default_factory=dict)


def parse(text: str) -> list[Peer]:
    peers: list[Peer] = []
    pending_name: str | None = None
    block: dict[str, str] | None = None
    block_name: str | None = None

    def close() -> None:
        if block is None:
            return
        allowed = block.pop("AllowedIPs", "")
        address = allowed.split(",")[0].strip().removesuffix("/32")
        peers.append(
            Peer(
                name=block_name,
                public_key=block.pop("PublicKey"),
                address=ipaddress.IPv4Address(address),
                preshared_key=block.pop("PresharedKey", None),
                extra=block,
            )
        )

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if m := _NAME_LINE.match(line):
            pending_name = m.group(1)
            continue
        if line.startswith("#"):
            continue
        if line == "[Peer]":
            close()
            block, block_name, pending_name = {}, pending_name, None
            continue
        if block is not None and (m := _KEY_LINE.match(line)):
            block[m.group(1)] = m.group(2)
    close()
    return peers


def render(peers: list[Peer]) -> str:
    out = [HEADER]
    for p in peers:
        out.append("")
        if p.name:
            out.append(f"# name = {p.name}")
        out.append("[Peer]")
        out.append(f"PublicKey = {p.public_key}")
        if p.preshared_key:
            out.append(f"PresharedKey = {p.preshared_key}")
        out.append(f"AllowedIPs = {p.address}/32")
        out.extend(f"{k} = {v}" for k, v in p.extra.items())
    return "\n".join(out) + "\n"


def read(path: Path) -> list[Peer]:
    try:
        return parse(path.read_text())
    except FileNotFoundError:
        return []


def write(path: Path, peers: list[Peer]) -> None:
    """Replace the file whole: a reader, PostUp included, sees the old file or the new one."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(render(peers))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


@contextmanager
def locked(path: Path):
    """Serialise writers of the file across processes, the bot and a person with a script alike."""
    lock = path.with_name(f".{path.name}.lock")
    with lock.open("a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
