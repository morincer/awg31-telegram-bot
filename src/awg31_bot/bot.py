"""Telegram side: the commands, the admin filter, and how an issued config goes out.

Long polling: the bot opens no port. It answers the Telegram ids of admin_ids only; a message from
anybody else gets no answer at all, so the bot does not even confirm it exists.

An issued config goes out as three messages: the status with the vpn:// link, then the .conf file
and the QR series as replies to it. Telegram takes no file with a text message and no more than 1024
characters under a file, and the link is about 4000. Every message carries #awg and the device's own
tag, so the history of a device is one search in the chat.
"""

import html
import logging
import re

from aiogram import Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import BufferedInputFile, Message, ReplyParameters

from . import amnezia
from .awg import AwgError
from .service import ClientError, Issued, Service, describe

log = logging.getLogger(__name__)

# Telegram's limit for one text message, counted without the markup
MESSAGE_LIMIT = 4096

HELP = (
    "/add <name> — a new device\n"
    "/reissue <name> — new keys for a device; its old config stops working\n"
    "/del <name> — remove a device\n"
    "/list — devices, their handshakes and traffic\n\n"
    "A config comes three ways: a vpn:// link to paste into AmneziaVPN, a .conf file, and the "
    "QR series as one animation to show on another screen and scan."
)


class Admin(Filter):
    def __init__(self, admin_ids: frozenset[int]):
        self.admin_ids = admin_ids

    async def __call__(self, message: Message) -> bool:
        allowed = message.from_user is not None and message.from_user.id in self.admin_ids
        if not allowed:
            who = message.from_user.id if message.from_user else "unknown"
            log.warning("ignored a message from %s", who)
        return allowed


def build_router(admin_ids: frozenset[int]) -> Router:
    router = Router()
    router.message.filter(Admin(admin_ids))
    router.message.register(help_, Command("start", "help"))
    router.message.register(add, Command("add"))
    router.message.register(reissue, Command("reissue"))
    router.message.register(delete, Command("del"))
    router.message.register(list_, Command("list"))
    return router


def tags(name: str) -> str:
    """#awg and the device's tag. A hashtag stops at '-' and '.', and one of digits alone is none."""
    own = re.sub(r"\W", "_", name, flags=re.ASCII)
    if own.isdigit():
        own = f"awg_{own}"
    return f"#awg #{own}"


def _header(name: str, status: str) -> str:
    return f"<b>{html.escape(name)}</b> · {html.escape(status)}\n{tags(name)}"


async def help_(message: Message) -> None:
    await message.answer(HELP)


def _name(command: CommandObject) -> str:
    name = (command.args or "").strip()
    if not name or " " in name:
        raise ClientError(f"One name, please: /{command.command} <name>")
    return name


async def _guarded(message: Message, action) -> None:
    try:
        await action()
    except ClientError as e:
        log.info("refused %r: %s", message.text, e)
        await message.answer(str(e))
    except (AwgError, OSError) as e:
        log.exception("failed %r", message.text)
        await message.answer(f"Failed: {e}")


async def send_issued(message: Message, issued: Issued, status: str) -> None:
    status = f"{status} · {issued.address}"
    header = _header(issued.name, status)
    visible = f"{issued.name} · {status}\n{tags(issued.name)}\n\n{issued.link}"
    link_fits = len(visible) <= MESSAGE_LIMIT
    if link_fits:
        # A code block: Telegram copies it whole with one tap
        text = f"{header}\n\n<pre>{html.escape(issued.link)}</pre>"
    else:
        text = f"{header}\n\nThe vpn:// link is longer than a Telegram message: it is in the file below."
    first = await message.answer(text, parse_mode=ParseMode.HTML)
    reply = ReplyParameters(message_id=first.message_id)

    if not link_fits:
        await message.answer_document(
            BufferedInputFile(issued.link.encode(), filename=f"{issued.name}.vpn.txt"),
            caption=f"{tags(issued.name)}\nThe vpn:// link, to paste into AmneziaVPN",
            reply_parameters=reply,
        )
    await message.answer_document(
        BufferedInputFile(issued.conf.encode(), filename=f"{issued.name}.conf"),
        caption=f"{tags(issued.name)}\nThe config file, for AmneziaVPN or AmneziaWG",
        reply_parameters=reply,
    )
    total = len(issued.qr_chunks)
    if total == 1:
        await message.answer_photo(
            BufferedInputFile(amnezia.qr_png(issued.qr_chunks[0]), filename=f"{issued.name}-qr.png"),
            caption=f"{tags(issued.name)}\nThe QR code: scan it in AmneziaVPN from another screen",
            reply_parameters=reply,
        )
    else:
        await message.answer_animation(
            BufferedInputFile(amnezia.qr_gif(issued.qr_chunks), filename=f"{issued.name}-qr.gif"),
            caption=(
                f"{tags(issued.name)}\nThe QR series, {total} codes in a loop: show it on another screen "
                "and hold AmneziaVPN's scanner on it until it has them all"
            ),
            reply_parameters=reply,
        )


async def add(message: Message, command: CommandObject, service: Service) -> None:
    async def action():
        issued = await service.add(_name(command))
        await send_issued(message, issued, "new device")

    await _guarded(message, action)


async def reissue(message: Message, command: CommandObject, service: Service) -> None:
    async def action():
        issued = await service.reissue(_name(command))
        await send_issued(message, issued, "new keys, the old config no longer works")

    await _guarded(message, action)


async def delete(message: Message, command: CommandObject, service: Service) -> None:
    async def action():
        name = _name(command)
        address = await service.delete(name)
        await message.answer(_header(name, f"removed, {address} is free"), parse_mode=ParseMode.HTML)

    await _guarded(message, action)


async def list_(message: Message, service: Service) -> None:
    async def action():
        clients = await service.list()
        await message.answer("\n".join(describe(c) for c in clients) if clients else "No devices yet")

    await _guarded(message, action)
