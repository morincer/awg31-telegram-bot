"""Addresses of the tunnel network: the lowest one nobody holds goes to a new client."""

import ipaddress
from collections.abc import Iterable


class NetworkFull(Exception):
    pass


def next_free(
    network: ipaddress.IPv4Network,
    server_ip: ipaddress.IPv4Address,
    taken: Iterable[ipaddress.IPv4Address],
) -> ipaddress.IPv4Address:
    used = set(taken) | {server_ip}
    for host in network.hosts():
        if host not in used:
            return host
    raise NetworkFull(f"no free address left in {network}")
