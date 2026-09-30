"""Promises of the issued config that the end-to-end tests of the bot do not already pin."""

import random

import pytest

from awg31_bot.service import Service

from . import app_reader
from .conftest import parse_conf


@pytest.fixture
def svc(config, iface):
    return Service(config, iface)


async def test_a_config_with_three_real_size_i_packets_fits_one_telegram_message(svc):
    issued = await svc.add("phone")
    assert len(issued.link) <= 4096


async def test_the_app_joins_the_qr_series_in_whatever_order_it_is_scanned(svc):
    issued = await svc.add("phone")
    shuffled = issued.qr_chunks[:]
    random.Random(1).shuffle(shuffled)
    assert app_reader.read_qr_series(shuffled) == app_reader.read_link(issued.link)


async def test_the_app_does_not_import_a_series_with_a_code_missing(svc):
    issued = await svc.add("phone")
    with pytest.raises(app_reader.ImportError_):
        app_reader.read_qr_series(issued.qr_chunks[:-1])


async def test_the_server_private_key_never_reaches_a_client(svc, iface):
    issued = await svc.add("phone")
    assert iface.private_key not in issued.conf
    assert iface.private_key not in str(app_reader.read_link(issued.link))


async def test_the_client_config_names_no_server_port_or_key_of_its_own(svc):
    interface = parse_conf((await svc.add("phone")).conf)["Interface"]
    # A client listens on no fixed port and has one private key, its own
    assert "ListenPort" not in interface
    assert list(interface).count("PrivateKey") == 1
