"""The promises the commands keep towards the server: the interface and the peers file agree."""

import asyncio
import dataclasses
import ipaddress

import pytest

from awg31_bot import peers
from awg31_bot.awg import AwgError, PeerState
from awg31_bot.service import ClientError, ClientInfo, Service, describe

from .conftest import parse_conf


@pytest.fixture
def svc(config, iface):
    return Service(config, iface)


def on_file(config):
    return {p.name: p for p in peers.read(config.peers_file)}


def on_interface(iface):
    return {public: address for public, (_, address) in iface.peers.items()}


def agree(config, iface) -> bool:
    """Every client of the file is on the interface with its key and address, and nobody else is."""
    file = {p.public_key: str(p.address) for p in peers.read(config.peers_file)}
    return file == on_interface(iface)


async def test_every_command_leaves_the_file_and_the_interface_in_agreement(svc, config, iface):
    await svc.add("a")
    assert agree(config, iface)
    await svc.add("b")
    await svc.reissue("a")
    assert agree(config, iface)
    await svc.delete("b")
    assert agree(config, iface)


async def test_two_devices_added_at_once_get_different_addresses(svc, config, iface):
    first, second = await asyncio.gather(svc.add("a"), svc.add("b"))
    assert first.address != second.address
    assert sorted(on_file(config)) == ["a", "b"]
    assert agree(config, iface)


async def test_a_freed_address_goes_to_the_next_device(svc):
    await svc.add("a")
    await svc.add("b")
    await svc.delete("a")
    assert (await svc.add("c")).address == "10.66.66.2"


async def test_a_full_network_is_refused_in_words(config, iface):
    svc = Service(dataclasses.replace(config, network=ipaddress.IPv4Network("10.66.66.0/30")), iface)
    await svc.add("a")
    with pytest.raises(ClientError):
        await svc.add("b")
    assert len(iface.peers) == 1


async def test_a_client_added_by_hand_survives_the_bot(svc, config, iface):
    manual = peers.Peer(
        None, "HANDMADE0000000000000000000000000000000000=", ipaddress.IPv4Address("10.66.66.2")
    )
    peers.write(config.peers_file, [manual])
    iface.peers[manual.public_key] = (None, "10.66.66.2")

    added = await svc.add("phone")
    await svc.delete("phone")

    assert added.address == "10.66.66.3", "the hand-made client's address is not given away"
    assert peers.read(config.peers_file) == [manual]
    assert manual.public_key in iface.peers


async def test_a_config_issued_after_a_regeneration_carries_the_new_set(svc, iface):
    before = parse_conf((await svc.add("a")).conf)["Interface"]
    iface.set.update(S1="77", Jc="5", HeaderProtectionKey="NEWKEY" + "0" * 38)
    after = parse_conf((await svc.add("b")).conf)["Interface"]
    assert before["S1"] == "18"
    assert (after["S1"], after["Jc"], after["HeaderProtectionKey"]) == ("77", "5", "NEWKEY" + "0" * 38)


async def test_the_issued_key_is_not_kept_anywhere(svc, config, iface):
    issued = await svc.add("phone")
    private = parse_conf(issued.conf)["Interface"]["PrivateKey"]
    assert private not in config.peers_file.read_text()
    assert private not in await iface.showconf()


# When the file cannot be written the server is put back as it was


@pytest.fixture
def unwritable(monkeypatch):
    def refuse(path, updated):
        raise OSError("read-only file system")

    monkeypatch.setattr(peers, "write", refuse)


async def test_add_that_cannot_be_saved_lets_nobody_in(svc, config, iface, unwritable):
    with pytest.raises(OSError):
        await svc.add("phone")
    assert iface.peers == {}


async def test_reissue_that_cannot_be_saved_keeps_the_old_config_working(svc, config, iface, monkeypatch):
    await svc.add("phone")
    before = dict(iface.peers)

    def refuse(path, updated):
        raise OSError("read-only file system")

    monkeypatch.setattr(peers, "write", refuse)
    with pytest.raises(OSError):
        await svc.reissue("phone")
    assert iface.peers == before


async def test_reissue_the_server_refuses_keeps_the_old_config_working(svc, config, iface):
    await svc.add("phone")
    before = dict(iface.peers)
    iface.refuse_new_keys = True
    with pytest.raises(AwgError):
        await svc.reissue("phone")
    assert iface.peers == before
    assert agree(config, iface)


async def test_delete_that_cannot_be_saved_keeps_the_device_working(svc, config, iface, monkeypatch):
    await svc.add("phone")
    before = dict(iface.peers)

    def refuse(path, updated):
        raise OSError("read-only file system")

    monkeypatch.setattr(peers, "write", refuse)
    with pytest.raises(OSError):
        await svc.delete("phone")
    assert iface.peers == before


# /list


async def test_list_tells_a_device_that_never_connected_from_one_seen_recently(svc, iface):
    await svc.add("fresh")
    await svc.add("seen")
    seen_key = next(k for k, (_, a) in iface.peers.items() if a == "10.66.66.3")
    iface.handshakes[seen_key] = PeerState(seen_key, "203.0.113.5:4000", 1_000_000, 2048, 5 * 1024 * 1024)

    lines = {c.name: describe(c, now=1_000_000 + 40) for c in await svc.list()}
    assert "never connected" in lines["fresh"]
    assert "40 s ago" in lines["seen"]
    assert "5.0 MiB" in lines["seen"]


async def test_list_shows_a_client_of_the_file_missing_from_the_interface(svc, iface):
    await svc.add("phone")
    iface.peers.clear()
    (info,) = await svc.list()
    assert "not on the interface" in describe(info)


def test_list_speaks_in_units_a_person_reads():
    def line(ago, traffic):
        return describe(ClientInfo("a", "x", PeerState("K", None, 1000, traffic, traffic)), now=1000 + ago)

    assert "5 min" in line(300, 100)
    assert "3 h" in line(3 * 3600, 100)
    assert "2 d" in line(2 * 86400, 100)
    assert "100 B" in line(1, 100)
    # Binary units, named so: the figures match those of awg show
    assert "2.0 KiB" in line(1, 2048)
    assert "3.0 GiB" in line(1, 3 * 1024**3)
