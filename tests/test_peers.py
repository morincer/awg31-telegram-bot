import ipaddress
import os
import stat
import threading

import pytest

from awg31_bot import peers

SAMPLE = """\
# The clients of awg0

# name = phone
[Peer]
PublicKey = AAA=
PresharedKey = BBB=
AllowedIPs = 10.66.66.3/32

# a block somebody added by hand
[Peer]
PublicKey = CCC=
AllowedIPs = 10.66.66.2/32
PersistentKeepalive = 25
"""


def test_parse_named_and_unnamed_blocks():
    phone, manual = peers.parse(SAMPLE)
    assert phone.name == "phone"
    assert phone.public_key == "AAA="
    assert phone.preshared_key == "BBB="
    assert phone.address == ipaddress.IPv4Address("10.66.66.3")
    assert manual.name is None
    assert manual.preshared_key is None
    assert manual.extra == {"PersistentKeepalive": "25"}


def test_render_parses_back_to_the_same_peers():
    original = peers.parse(SAMPLE)
    assert peers.parse(peers.render(original)) == original


def test_render_keeps_the_block_without_a_name_line_nameless():
    text = peers.render(peers.parse(SAMPLE))
    assert text.count("\n# name = ") == 1
    assert text.startswith(peers.HEADER)


def test_a_name_line_belongs_to_the_next_block_only():
    text = (
        "# name = first\n[Peer]\nPublicKey = A=\nAllowedIPs = 10.0.0.2/32\n"
        "[Peer]\nPublicKey = B=\nAllowedIPs = 10.0.0.3/32\n"
    )
    first, second = peers.parse(text)
    assert (first.name, second.name) == ("first", None)


def test_empty_and_comment_only_text_has_no_peers():
    assert peers.parse("") == []
    assert peers.parse(peers.HEADER) == []


def test_read_of_a_missing_file_is_empty(tmp_path):
    assert peers.read(tmp_path / "none.conf") == []


def test_write_replaces_the_file_whole_with_mode_0600(tmp_path):
    path = tmp_path / "awg0.peers.conf"
    path.write_text("old")
    path.chmod(0o644)
    peers.write(path, peers.parse(SAMPLE))
    assert peers.read(path) == peers.parse(SAMPLE)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == ["awg0.peers.conf"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root writes into a read-only directory")
def test_a_write_that_fails_leaves_the_previous_file_whole(tmp_path):
    path = tmp_path / "awg0.peers.conf"
    peers.write(path, peers.parse(SAMPLE))
    before = sorted(tmp_path.iterdir())
    tmp_path.chmod(0o500)
    try:
        with pytest.raises(OSError):
            peers.write(path, [])
    finally:
        tmp_path.chmod(0o700)
    assert peers.read(path) == peers.parse(SAMPLE)
    assert sorted(tmp_path.iterdir()) == before


def test_a_second_writer_waits_for_the_first(tmp_path):
    path = tmp_path / "awg0.peers.conf"
    entered = threading.Event()

    def second():
        with peers.locked(path):
            entered.set()

    with peers.locked(path):
        thread = threading.Thread(target=second)
        thread.start()
        assert not entered.wait(0.3), "the second writer got in while the first held the file"
    assert entered.wait(2)
    thread.join()


def test_names():
    assert peers.NAME_RE.match("mama-phone.2")
    assert not peers.NAME_RE.match("-lead")
    assert not peers.NAME_RE.match("имя")
    assert not peers.NAME_RE.match("a" * 33)
