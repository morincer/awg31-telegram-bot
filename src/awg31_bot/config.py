"""Settings of the bot, read from a TOML file that belongs to root or to the bot's own account."""

import ipaddress
import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    token: str
    # Telegram user ids the bot answers; every other message is ignored
    admin_ids: frozenset[int]
    interface: str
    peers_file: Path
    # What clients are told: the name they connect to, their DNS and MTU
    endpoint: str
    dns: tuple[str, ...]
    mtu: int
    # The tunnel network and the server's address in it; clients get the free addresses
    network: ipaddress.IPv4Network
    server_ip: ipaddress.IPv4Address
    # Shown in AmneziaVPN as the server's name: "<name> - <client>"
    name: str
    awg: str = "awg"


def load(path: Path) -> Config:
    with path.open("rb") as f:
        raw = tomllib.load(f)
    network = ipaddress.IPv4Network(raw["network"])
    server_ip = ipaddress.IPv4Address(raw["server_ip"])
    if server_ip not in network:
        raise ValueError(f"server_ip {server_ip} is outside network {network}")
    admin_ids = frozenset(int(i) for i in raw["admin_ids"])
    if not admin_ids:
        raise ValueError("admin_ids is empty: the bot would answer nobody")
    dns = raw.get("dns", [])
    return Config(
        token=raw["token"],
        admin_ids=admin_ids,
        interface=raw["interface"],
        peers_file=Path(raw["peers_file"]),
        endpoint=raw["endpoint"],
        dns=tuple([dns] if isinstance(dns, str) else dns),
        mtu=int(raw.get("mtu", 1280)),
        network=network,
        server_ip=server_ip,
        name=raw.get("name", raw["endpoint"]),
        awg=raw.get("awg", "awg"),
    )
