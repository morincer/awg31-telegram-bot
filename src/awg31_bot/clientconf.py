"""A client's config, built from what the server interface runs.

The 3.x set — junk, padding, headers, header protection, the I packets, the timings — has to be
the same on both ends. It is read from `awg showconf` of the running interface rather than from a
settings file of the bot, so a regenerated server set reaches every client issued afterwards.
"""

import ipaddress
import re
from dataclasses import dataclass

# The keys of [Interface] a client takes over from the server, in the order awg0.conf has them
OBFUSCATION_KEYS = (
    "Jc",
    "Jmin",
    "Jmax",
    "S1",
    "S2",
    "S3",
    "S4",
    "H1",
    "H2",
    "H3",
    "H4",
    "I1",
    "I2",
    "I3",
    "I4",
    "I5",
    "HeaderProtectionKey",
    "ContentPaddingAddition",
    "RandomTrailers",
    "DisableCookies",
    "RekeyAfterTime",
    "RekeyTimeout",
    "RejectAfterTime",
    "KeepaliveTimeout",
    "MaxHandshakeAttempts",
)

_KEY_LINE = re.compile(r"^([A-Za-z0-9]+)\s*=\s*(.*?)\s*$")


@dataclass(frozen=True)
class Server:
    public_key: str
    port: int
    # OBFUSCATION_KEYS the server runs with, the empty and the "off" ones left out
    obfuscation: dict[str, str]


def parse_server(showconf: str, public_key: str) -> Server:
    interface: dict[str, str] = {}
    section = None
    for raw in showconf.splitlines():
        line = raw.strip()
        if line.startswith("["):
            section = line
            continue
        if section == "[Interface]" and (m := _KEY_LINE.match(line)):
            interface[m.group(1)] = m.group(2)
    obfuscation = {k: interface[k] for k in OBFUSCATION_KEYS if interface.get(k) and interface[k] != "off"}
    return Server(public_key=public_key, port=int(interface["ListenPort"]), obfuscation=obfuscation)


@dataclass(frozen=True)
class Client:
    name: str
    private_key: str
    public_key: str
    preshared_key: str
    address: ipaddress.IPv4Address


def render(
    server: Server,
    client: Client,
    endpoint: str,
    dns: tuple[str, ...],
    mtu: int,
) -> str:
    lines = [
        "[Interface]",
        f"PrivateKey = {client.private_key}",
        f"Address = {client.address}/32",
    ]
    if dns:
        lines.append(f"DNS = {', '.join(dns)}")
    lines.append(f"MTU = {mtu}")
    lines.extend(f"{k} = {v}" for k, v in server.obfuscation.items())
    lines += [
        "",
        "[Peer]",
        f"PublicKey = {server.public_key}",
        f"PresharedKey = {client.preshared_key}",
        "AllowedIPs = 0.0.0.0/0, ::/0",
        f"Endpoint = {endpoint}:{server.port}",
        "PersistentKeepalive = 25",
    ]
    return "\n".join(lines) + "\n"
