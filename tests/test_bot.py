"""What an admin and a stranger get from the bot, seen from Telegram and from the server."""

import json
import logging
import os

import pytest
from aiogram.methods import SendAnimation, SendMessage

from awg31_bot import peers
from awg31_bot.service import Service

from . import app_reader
from .conftest import fake_public, parse_conf
from .telegram import code_blocks, dispatcher, documents, everything_shown, images, scan, send, texts


@pytest.fixture
def dp(config, iface):
    return dispatcher(Service(config, iface))


def on_file(config):
    return {p.name: p for p in peers.read(config.peers_file)}


def issued_config(bot):
    """The three forms of one issued config, each read the way its reader would read it."""
    files = documents(bot)
    conf_name, conf_bytes = next((n, b) for n, b in files.items() if n.endswith(".conf"))
    # The link comes as a code block, or as a file when it outgrows a message
    links = [b for b in code_blocks(bot) if b.startswith("vpn://")]
    links += [b.decode() for n, b in files.items() if n.endswith(".vpn.txt")]
    assert len(links) == 1, "one vpn:// link in the chat"
    return (
        conf_name,
        parse_conf(conf_bytes.decode()),
        app_reader.read_link(links[0]),
        app_reader.read_qr_series([code for picture in images(bot) for code in scan(picture)]),
    )


async def test_a_stranger_gets_no_answer_and_changes_nothing(dp, config, iface):
    bot = await send(dp, "/add intruder", user_id=7)
    assert bot.calls == []
    assert iface.peers == {}
    assert peers.read(config.peers_file) == []


async def test_add_issues_a_config_that_matches_the_server_in_all_three_forms(dp, config, iface):
    bot = await send(dp, "/add phone")
    name, conf, link, qr = issued_config(bot)
    peer = on_file(config)["phone"]

    assert name == "phone.conf"
    # The config's key is the one the server now lets in, at the address the file gives it
    public = fake_public(conf["Interface"]["PrivateKey"])
    assert public == peer.public_key
    assert iface.peers[public] == (conf["Peer"]["PresharedKey"], str(peer.address))
    assert conf["Interface"]["Address"] == f"{peer.address}/32"
    assert conf["Peer"]["PublicKey"] == fake_public(iface.private_key)
    assert conf["Peer"]["Endpoint"] == "vpn.example.org:443"
    # The client runs the server's 3.x set
    for key, value in iface.set.items():
        assert conf["Interface"][key] == value

    # The link and the QR series carry the same client for the app
    assert qr == link
    awg = link["containers"][0]["awg"]
    assert awg["protocol_version"] == "3.1"
    last = json.loads(awg["last_config"])
    assert last["client_priv_key"] == conf["Interface"]["PrivateKey"]
    assert last["psk_key"] == conf["Peer"]["PresharedKey"]
    assert last["client_ip"] == str(peer.address)
    assert last["I1"] == iface.set["I1"]


async def test_the_app_names_the_connection_after_the_server_alone(dp, config):
    # A phone shows a dozen characters of the name; the device's name is in the chat already
    _, _, link, _ = issued_config(await send(dp, "/add phone"))
    assert link["description"] == config.name


async def test_the_status_and_the_link_come_first_and_the_rest_answers_them(dp):
    bot = await send(dp, "/add phone")
    first = bot.calls[0]
    assert isinstance(first, SendMessage)
    assert "phone" in texts(bot)[0] and "new device" in texts(bot)[0] and "10.66.66.2" in texts(bot)[0]
    assert code_blocks(bot)[0].startswith("vpn://")
    rest = bot.calls[1:]
    assert rest, "the file and the QR series follow"
    assert all(c.reply_parameters.message_id == bot.message_id(first) for c in rest)


@pytest.mark.parametrize(
    ("command", "tag"),
    [("/add phone", "#phone"), ("/add my-phone.2", "#my_phone_2"), ("/add 2024", "#awg_2024")],
)
async def test_every_message_of_a_device_carries_tags_a_chat_search_finds(dp, command, tag):
    # A hashtag ends at '-' and '.', and digits alone make none
    for shown in everything_shown(await send(dp, command)):
        assert "#awg" in shown.split()
        assert tag in shown.split()


async def test_reissue_and_del_are_tagged_like_the_device(dp):
    await send(dp, "/add phone")
    for command in ("/reissue phone", "/del phone"):
        for shown in everything_shown(await send(dp, command)):
            assert "#phone" in shown.split()


async def test_a_qr_series_comes_as_one_animation_the_app_reads(dp):
    bot = await send(dp, "/add phone")
    animations = [c for c in bot.calls if isinstance(c, SendAnimation)]
    assert len(animations) == 1
    frames = scan(animations[0].animation.data)
    assert len(frames) > 1
    _, _, link, _ = issued_config(bot)
    assert app_reader.read_qr_series(frames) == link


async def test_a_link_that_outgrows_a_message_still_reaches_the_app_as_a_file(dp, iface):
    iface.set.update({f"I{n}": "<b 0x" + os.urandom(556).hex() + ">" for n in (4, 5)})
    bot = await send(dp, "/add phone")
    assert code_blocks(bot) == []
    assert all(len(t) <= 4096 for t in texts(bot))
    _, conf, link, qr = issued_config(bot)
    assert link == qr
    assert json.loads(link["containers"][0]["awg"]["last_config"])["I5"] == iface.set["I5"]


async def test_a_small_config_goes_as_one_qr_code_that_imports(dp, iface):
    for n in (1, 2, 3):
        del iface.set[f"I{n}"]
    bot = await send(dp, "/add phone")
    assert len(images(bot)) == 1
    _, conf, link, qr = issued_config(bot)
    assert qr == link
    assert "I1" not in conf["Interface"]


async def test_a_second_device_gets_its_own_address_and_the_first_keeps_its(dp, config):
    await send(dp, "/add phone")
    await send(dp, "/add laptop")
    held = on_file(config)
    assert held["phone"].address != held["laptop"].address


async def test_reissue_lets_the_new_config_in_and_shuts_the_old_one_out(dp, config, iface):
    first = await send(dp, "/add phone")
    _, old_conf, _, _ = issued_config(first)
    old_key = fake_public(old_conf["Interface"]["PrivateKey"])

    second = await send(dp, "/reissue phone")
    _, new_conf, _, _ = issued_config(second)
    new_key = fake_public(new_conf["Interface"]["PrivateKey"])

    assert old_key not in iface.peers
    assert new_key in iface.peers
    assert new_conf["Interface"]["Address"] == old_conf["Interface"]["Address"]
    assert on_file(config)["phone"].public_key == new_key
    # The owner is told the old config is gone
    assert "old config no longer works" in texts(second)[0]


async def test_del_shuts_the_device_out_and_frees_its_address(dp, config, iface):
    await send(dp, "/add phone")
    bot = await send(dp, "/del phone")
    assert iface.peers == {}
    assert "phone" not in on_file(config)
    assert "10.66.66.2" in texts(bot)[0]
    again = await send(dp, "/add tablet")
    _, conf, _, _ = issued_config(again)
    assert conf["Interface"]["Address"] == "10.66.66.2/32"


async def test_list_names_every_device_with_its_address(dp):
    empty = await send(dp, "/list")
    assert len(texts(empty)) == 1, "an empty list is still answered"
    await send(dp, "/add phone")
    await send(dp, "/add laptop")
    listed = texts(await send(dp, "/list"))[0]
    assert "phone — 10.66.66.2" in listed
    assert "laptop — 10.66.66.3" in listed


async def test_the_journal_says_what_each_command_did_and_holds_no_key(dp, iface, caplog):
    caplog.set_level(logging.INFO, logger="awg31_bot")
    added = await send(dp, "/add phone")
    await send(dp, "/reissue phone")
    await send(dp, "/del phone")
    await send(dp, "/del phone")
    journal = "\n".join(r.getMessage() for r in caplog.records)

    for line in ("added phone at 10.66.66.2", "reissued phone at 10.66.66.2", "deleted phone"):
        assert line in journal
    # The second /del is refused, and the journal says so
    assert "refused '/del phone'" in journal
    _, conf, _, _ = issued_config(added)
    for secret in (conf["Interface"]["PrivateKey"], conf["Peer"]["PresharedKey"], iface.private_key):
        assert secret not in journal


@pytest.mark.parametrize(
    "text", ["/add", "/add two words", "/add ../../etc", "/add имя", "/reissue ghost", "/del ghost"]
)
async def test_a_request_the_bot_refuses_is_explained_and_changes_nothing(dp, config, iface, text):
    bot = await send(dp, text)
    assert len(texts(bot)) == 1
    assert documents(bot) == {} and images(bot) == []
    assert iface.peers == {}
    assert peers.read(config.peers_file) == []


async def test_adding_a_name_that_exists_points_to_reissue(dp, iface):
    await send(dp, "/add phone")
    before = dict(iface.peers)
    bot = await send(dp, "/add phone")
    assert "/reissue phone" in texts(bot)[0]
    assert iface.peers == before


async def test_a_server_that_refuses_the_change_is_reported_and_nothing_is_half_done(dp, config, iface):
    iface.broken.add("set")
    bot = await send(dp, "/add phone")
    # What awg said reaches the owner
    assert "Unable to modify interface" in texts(bot)[0]
    assert documents(bot) == {}
    assert peers.read(config.peers_file) == []


async def test_start_and_help_explain_the_commands(dp):
    for command in ("/start", "/help"):
        text = texts(await send(dp, command))[0]
        for name in ("/add", "/reissue", "/del", "/list"):
            assert name in text
