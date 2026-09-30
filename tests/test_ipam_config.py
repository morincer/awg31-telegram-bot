import ipaddress

import pytest

from awg31_bot import config, ipam

NET = ipaddress.IPv4Network("10.66.66.0/29")
SERVER = ipaddress.IPv4Address("10.66.66.1")


def test_next_free_skips_the_server_and_taken_addresses():
    taken = [ipaddress.IPv4Address("10.66.66.2"), ipaddress.IPv4Address("10.66.66.4")]
    assert ipam.next_free(NET, SERVER, taken) == ipaddress.IPv4Address("10.66.66.3")


def test_next_free_reuses_a_freed_address():
    assert ipam.next_free(NET, SERVER, []) == ipaddress.IPv4Address("10.66.66.2")


def test_a_full_network_says_so():
    taken = list(NET.hosts())
    with pytest.raises(ipam.NetworkFull):
        ipam.next_free(NET, SERVER, taken)


GOOD = """
token = "123:abc"
admin_ids = [42, "43"]
interface = "awg0"
peers_file = "/var/lib/awg31-bot/awg0.peers.conf"
endpoint = "vpn.example.org"
dns = "9.9.9.9"
network = "10.66.66.0/24"
server_ip = "10.66.66.1"
"""


def test_load_reads_the_file_and_fills_defaults(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(GOOD)
    c = config.load(path)
    assert c.admin_ids == frozenset({42, 43})
    assert c.dns == ("9.9.9.9",)
    assert c.mtu == 1280
    assert c.name == "vpn.example.org"
    assert c.awg == "awg"
    assert c.network.prefixlen == 24


def test_load_takes_a_list_of_dns_and_a_name(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        GOOD.replace('dns = "9.9.9.9"', 'dns = ["9.9.9.9", "1.1.1.1"]\nname = "home"\nmtu = 1420')
    )
    c = config.load(path)
    assert c.dns == ("9.9.9.9", "1.1.1.1")
    assert (c.name, c.mtu) == ("home", 1420)


def test_load_refuses_a_server_outside_its_network(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(GOOD.replace('server_ip = "10.66.66.1"', 'server_ip = "10.0.0.1"'))
    with pytest.raises(ValueError, match="outside"):
        config.load(path)


def test_load_refuses_an_empty_admin_list(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(GOOD.replace('admin_ids = [42, "43"]', "admin_ids = []"))
    with pytest.raises(ValueError, match="nobody"):
        config.load(path)
