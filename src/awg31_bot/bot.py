"""Telegram side: the commands, the admin filter, and how an issued config goes out.

Long polling: the bot opens no port. It answers the Telegram ids of admin_ids only; a message from
anybody else gets no answer at all, so the bot does not even confirm it exists.
"""

import logging

from aiogram import Router
from aiogram.filters import Command, CommandObject, Filter
from aiogram.types import BufferedInputFile, InputMediaPhoto, Message

from . import amnezia
from .awg import AwgError
from .service import ClientError, Issued, Service, describe

log = logging.getLogger(__name__)

# Telegram's limit for one text message
MESSAGE_LIMIT = 4096

HELP = (
    "/add <name> — a new device\n"
    "/reissue <name> — new keys for a device; its old config stops working\n"
    "/del <name> — remove a device\n"
    "/list — devices, their handshakes and traffic\n\n"
    "A config comes three ways: a .conf file, a vpn:// link and a series of QR codes. "
    "AmneziaVPN scans the series one code at a time, in any order."
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
        await message.answer(str(e))
    except (AwgError, OSError) as e:
        log.exception("command failed")
        await message.answer(f"Failed: {e}")


async def send_issued(message: Message, issued: Issued, verb: str) -> None:
    await message.answer_document(
        BufferedInputFile(issued.conf.encode(), filename=f"{issued.name}.conf"),
        caption=(
            f"{issued.name}: {verb}, address {issued.address}. Import the file into AmneziaVPN or AmneziaWG."
        ),
    )
    if len(issued.link) <= MESSAGE_LIMIT:
        await message.answer(issued.link)
    else:
        await message.answer_document(
            BufferedInputFile(issued.link.encode(), filename=f"{issued.name}.vpn.txt"),
            caption="The vpn:// link is longer than a Telegram message, so it is in this file.",
        )
    total = len(issued.qr_chunks)
    photos = [
        InputMediaPhoto(
            media=BufferedInputFile(amnezia.qr_png(chunk), filename=f"{issued.name}-qr-{n}.png"),
            caption=f"QR {n} of {total}" if n > 1 else f"QR {n} of {total}: scan them all, one by one",
        )
        for n, chunk in enumerate(issued.qr_chunks, start=1)
    ]
    # A media group takes 2 to 10 items; a longer series goes out in albums of ten
    for i in range(0, total, 10):
        batch = photos[i : i + 10]
        if len(batch) == 1:
            await message.answer_photo(batch[0].media, caption=batch[0].caption)
        else:
            await message.answer_media_group(batch)


async def add(message: Message, command: CommandObject, service: Service) -> None:
    async def action():
        issued = await service.add(_name(command))
        await send_issued(message, issued, "a new device")

    await _guarded(message, action)


async def reissue(message: Message, command: CommandObject, service: Service) -> None:
    async def action():
        issued = await service.reissue(_name(command))
        await send_issued(message, issued, "new keys; the old config no longer works")

    await _guarded(message, action)


async def delete(message: Message, command: CommandObject, service: Service) -> None:
    async def action():
        name = _name(command)
        address = await service.delete(name)
        await message.answer(f"{name} removed; address {address} is free")

    await _guarded(message, action)


async def list_(message: Message, service: Service) -> None:
    async def action():
        clients = await service.list()
        await message.answer("\n".join(describe(c) for c in clients) if clients else "No devices yet")

    await _guarded(message, action)
