"""Whether an issued config actually connects: the bot against the amneziawg kernel module.

Needs root, the amneziawg module and amneziawg-tools; skipped anywhere else, CI included. Every
interface lives in throwaway network namespaces — a server and two clients joined by veth pairs —
so the host's own interfaces, firewall and routes are not touched, and everything goes on exit.

    sudo -E uv run pytest -m integration --no-cov tests/integration
"""

import base64
import dataclasses
import ipaddress
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from awg31_bot.awg import Awg
from awg31_bot.service import Service

from ..conftest import I_PACKETS

pytestmark = pytest.mark.integration

SRV, CLI1, CLI2 = "awgt-srv", "awgt-cli1", "awgt-cli2"
SERVER_IF = "awgt0"
PORT = 51990
# veth: the server end is .1, a client end .2, one /30 per client
LINKS = {CLI1: ("10.197.1.1", "10.197.1.2"), CLI2: ("10.197.2.1", "10.197.2.2")}


def sh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(args, check=check, capture_output=True, text=True)


def ns(name: str, *args: str, check: bool = True, stdin: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["ip", "netns", "exec", name, *args], check=check, capture_output=True, text=True, input=stdin
    )


def module_available() -> bool:
    if os.geteuid() != 0 or not shutil.which("awg"):
        return False
    probe = "awgt-probe"
    if sh("ip", "netns", "add", probe, check=False).returncode:
        return False
    try:
        return ns(probe, "ip", "link", "add", "awgt-p", "type", "amneziawg", check=False).returncode == 0
    finally:
        sh("ip", "netns", "del", probe, check=False)


if not module_available():
    pytest.skip("needs root, the amneziawg kernel module and awg", allow_module_level=True)


@pytest.fixture
def node(tmp_path: Path, config):
    for name in (SRV, CLI1, CLI2):
        sh("ip", "netns", "add", name)
        ns(name, "ip", "link", "set", "lo", "up")
    try:
        for cli, (srv_ip, cli_ip) in LINKS.items():
            a, b = f"v{cli[-4:]}s", f"v{cli[-4:]}c"
            sh("ip", "link", "add", a, "netns", SRV, "type", "veth", "peer", "name", b, "netns", cli)
            ns(SRV, "ip", "addr", "add", f"{srv_ip}/30", "dev", a)
            ns(SRV, "ip", "link", "set", a, "up")
            ns(cli, "ip", "addr", "add", f"{cli_ip}/30", "dev", b)
            ns(cli, "ip", "link", "set", b, "up")

        # The server as the amneziawg role leaves it: [Interface] only, the 3.x set, no clients
        key = sh("awg", "genkey").stdout.strip()
        hpk = base64.b64encode(os.urandom(32)).decode()
        interface = [
            "[Interface]", f"PrivateKey = {key}", f"ListenPort = {PORT}",
            "Jc = 3", "Jmin = 40", "Jmax = 120", "S1 = 18", "S2 = 98", "S3 = 51", "S4 = 20",
            "H1 = 1", "H2 = 2", "H3 = 3", "H4 = 4", f"HeaderProtectionKey = {hpk}", "RandomTrailers = on",
            *(f"{k} = {v}" for k, v in I_PACKETS.items()),
        ]  # fmt: skip
        conf = tmp_path / "server.conf"
        conf.write_text("\n".join(interface) + "\n")
        ns(SRV, "ip", "link", "add", SERVER_IF, "type", "amneziawg")
        ns(SRV, "awg", "setconf", SERVER_IF, str(conf))
        ns(SRV, "ip", "addr", "add", f"{config.server_ip}/{config.network.prefixlen}", "dev", SERVER_IF)
        ns(SRV, "ip", "link", "set", SERVER_IF, "mtu", "1280", "up")

        # The bot runs awg inside the server's namespace
        wrapper = tmp_path / "awg-in-srv"
        wrapper.write_text(f'#!/bin/sh\nexec ip netns exec {SRV} awg "$@"\n')
        wrapper.chmod(0o755)
        bot_config = dataclasses.replace(config, endpoint=LINKS[CLI1][0])
        yield Service(bot_config, Awg(str(wrapper), SERVER_IF))
    finally:
        for name in (CLI2, CLI1, SRV):
            sh("ip", "netns", "del", name, check=False)


def connects(client_ns: str, conf_text: str, endpoint: str) -> bool:
    """Bring the config up in a client namespace, as a phone would, and ping the server over it."""
    kept = [
        line.replace(f"Endpoint = {LINKS[CLI1][0]}", f"Endpoint = {endpoint}")
        for line in conf_text.splitlines()
        if line.split("=", 1)[0].strip() not in ("Address", "DNS", "MTU")
    ]
    address = next(
        line.split("=", 1)[1].strip() for line in conf_text.splitlines() if line.startswith("Address")
    )
    ns(client_ns, "ip", "link", "del", "awgc0", check=False)
    ns(client_ns, "ip", "link", "add", "awgc0", "type", "amneziawg")
    try:
        ns(client_ns, "awg", "setconf", "awgc0", "/dev/stdin", stdin="\n".join(kept) + "\n")
        ns(client_ns, "ip", "addr", "add", address, "dev", "awgc0")
        ns(client_ns, "ip", "link", "set", "awgc0", "mtu", "1280", "up")
        ns(client_ns, "ip", "route", "add", "10.66.66.0/24", "dev", "awgc0")
        return ns(client_ns, "ping", "-c", "2", "-W", "2", "10.66.66.1", check=False).returncode == 0
    finally:
        ns(client_ns, "ip", "link", "del", "awgc0", check=False)


async def test_an_issued_config_connects(node):
    issued = await node.add("phone")
    assert connects(CLI1, issued.conf, LINKS[CLI1][0])


async def test_after_reissue_the_new_config_connects_and_the_old_one_does_not(node):
    old = await node.add("phone")
    new = await node.reissue("phone")
    assert connects(CLI1, new.conf, LINKS[CLI1][0])
    assert not connects(CLI1, old.conf, LINKS[CLI1][0])


async def test_after_del_the_config_no_longer_connects(node):
    issued = await node.add("phone")
    await node.delete("phone")
    assert not connects(CLI1, issued.conf, LINKS[CLI1][0])


async def test_adding_a_device_leaves_another_one_connected(node):
    first = await node.add("phone")
    ns(CLI2, "ip", "link", "add", "awgk0", "type", "amneziawg")
    try:
        kept = [
            line.replace(LINKS[CLI1][0], LINKS[CLI2][0])
            for line in first.conf.splitlines()
            if line.split("=", 1)[0].strip() not in ("Address", "DNS", "MTU")
        ]
        ns(CLI2, "awg", "setconf", "awgk0", "/dev/stdin", stdin="\n".join(kept) + "\n")
        ns(CLI2, "ip", "addr", "add", str(ipaddress.IPv4Interface(first.address + "/32")), "dev", "awgk0")
        ns(CLI2, "ip", "link", "set", "awgk0", "mtu", "1280", "up")
        ns(CLI2, "ip", "route", "add", "10.66.66.0/24", "dev", "awgk0")
        assert ns(CLI2, "ping", "-c", "1", "-W", "2", "10.66.66.1", check=False).returncode == 0

        second = await node.add("laptop")
        assert connects(CLI1, second.conf, LINKS[CLI1][0])
        # The first device's session survived the change, with no new handshake needed
        assert ns(CLI2, "ping", "-c", "2", "-W", "2", "10.66.66.1", check=False).returncode == 0
    finally:
        ns(CLI2, "ip", "link", "del", "awgk0", check=False)
