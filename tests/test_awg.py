"""The Awg wrapper against a stand-in `awg`: a shell script that logs its arguments and stdin."""

import stat

import pytest

from awg31_bot import awg as awg_module
from awg31_bot.awg import Awg, AwgError

SCRIPT = r"""#!/bin/sh
log="$(dirname "$0")/calls.log"
stdin=""
[ ! -t 0 ] && stdin="$(cat)"
echo "$* | $stdin" >> "$log"
case "$1" in
  genkey) echo "PRIVATE=" ;;
  pubkey) echo "PUBLIC-OF-$stdin" ;;
  genpsk) echo "PSK=" ;;
  show)
    case "$3" in
      public-key) echo "SERVERPUB=" ;;
      dump)
        printf 'PRIV=\tPUB=\t443\toff\n'
        printf 'K1=\tPSK1=\t1.2.3.4:5000\t10.66.66.2/32\t1700000000\t100\t200\t25\n'
        printf 'K2=\t(none)\t(none)\t10.66.66.3/32\t0\t0\t0\toff\n'
        ;;
    esac ;;
  showconf) echo "[Interface]" ;;
  set) if [ "$4" = "BAD=" ]; then echo "Unable to modify interface: Invalid argument" >&2; exit 1; fi ;;
  slow) sleep 5 ;;
  silent-fail) exit 3 ;;
esac
"""


@pytest.fixture
def tool(tmp_path):
    path = tmp_path / "awg"
    path.write_text(SCRIPT)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return Awg(str(path), "awg0"), tmp_path / "calls.log"


async def test_keypair_derives_the_public_key_through_stdin(tool):
    awg, log = tool
    keys = await awg.keypair()
    assert keys.private == "PRIVATE="
    assert keys.public == "PUBLIC-OF-PRIVATE="
    assert "pubkey | PRIVATE=" in log.read_text()


async def test_psk_and_public_key_and_showconf(tool):
    awg, _ = tool
    assert await awg.psk() == "PSK="
    assert await awg.public_key() == "SERVERPUB="
    assert (await awg.showconf()).strip() == "[Interface]"


def logged(log):
    """(arguments, stdin) of each call the stand-in awg received."""
    return [tuple(part.strip() for part in line.split(" | ", 1)) for line in log.read_text().splitlines()]


async def test_a_preshared_key_never_appears_in_the_arguments_of_awg(tool):
    # Any process on the node can read another's arguments in /proc; stdin it cannot
    awg, log = tool
    await awg.add_peer("K=", "SECRET=", "10.66.66.2")
    ((args, stdin),) = logged(log)
    assert "SECRET=" not in args
    assert stdin == "SECRET="
    assert "K=" in args and "10.66.66.2/32" in args


async def test_a_peer_without_a_preshared_key_is_added_without_one(tool):
    awg, log = tool
    await awg.add_peer("K=", None, "10.66.66.2")
    ((args, stdin),) = logged(log)
    assert "preshared-key" not in args
    assert stdin == ""


async def test_remove_peer_names_the_key_it_removes(tool):
    awg, log = tool
    await awg.remove_peer("K=")
    ((args, _),) = logged(log)
    assert "K=" in args.split() and "remove" in args.split()


async def test_a_failure_carries_the_message_of_awg(tool):
    awg, _ = tool
    with pytest.raises(AwgError, match="Invalid argument"):
        await awg.remove_peer("BAD=")


async def test_a_silent_failure_carries_the_exit_code(tool):
    awg, _ = tool
    with pytest.raises(AwgError, match="exit 3"):
        await awg.run("silent-fail")


async def test_a_hung_call_times_out(tool, monkeypatch):
    awg, _ = tool
    monkeypatch.setattr(awg_module, "TIMEOUT", 0.2)
    with pytest.raises(AwgError, match="timed out"):
        await awg.run("slow")


async def test_dump_reads_the_peers_and_skips_the_interface_line(tool):
    awg, _ = tool
    states = await awg.dump()
    assert set(states) == {"K1=", "K2="}
    assert states["K1="].endpoint == "1.2.3.4:5000"
    assert (states["K1="].latest_handshake, states["K1="].rx, states["K1="].tx) == (1700000000, 100, 200)
    assert states["K2="].endpoint is None
