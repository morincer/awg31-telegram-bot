"""Telegram side: the commands, the buttons, the admin filter, and how an issued config goes out.

Long polling: the bot opens no port. It answers the Telegram ids of admin_ids only; a message or a
button press from anybody else gets no answer at all, so the bot does not even confirm it exists.
The command menu is set in the admins' own chats and nowhere else, for the same reason.

Nothing has to be remembered: the menu offers the commands, /add without a name asks for one, and
/list puts Reissue and Delete under every device. Both of those cut a config off, so each asks
before it acts.

An issued config goes out as three messages: the status with the vpn:// link, then the .conf file
and the QR series as replies to it. Telegram takes no file with a text message and no more than 1024
characters under a file, and the link is about 4000. Every message carries #awg and the device's own
tag, so the history of a device is one search in the chat.
"""

import html
import logging
import re

from aiogram import Bot, F, Router
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject, Filter, StateFilter
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BotCommand,
    BotCommandScopeChat,
    BufferedInputFile,
    CallbackQuery,
    ForceReply,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyParameters,
)

from . import amnezia
from .awg import AwgError
from .service import ClientError, Issued, Service, describe

log = logging.getLogger(__name__)

# Telegram's limit for one text message, counted without the markup
MESSAGE_LIMIT = 4096

HELP = (
    "/add — a new device; the bot asks for its name\n"
    "/list — devices, their handshakes and traffic, with Reissue and Delete under each\n"
    "/reissue — new keys for a device; its old config stops working\n"
    "/del — remove a device\n\n"
    "A name can follow the command: /add my-phone. "
    "A config comes three ways: a vpn:// link to paste into AmneziaVPN, a .conf file, and the "
    "QR series as one animation to show on another screen and scan."
)

COMMANDS = [
    BotCommand(command="add", description="A new device"),
    BotCommand(command="list", description="Devices, with Reissue and Delete"),
    BotCommand(command="reissue", description="New keys for a device"),
    BotCommand(command="del", description="Remove a device"),
    BotCommand(command="help", description="What the bot does"),
]


class AddDevice(StatesGroup):
    name = State()


class Device(CallbackData, prefix="dev"):
    """A button about one device. ask: reissue or del, asking first; do: the same, confirmed."""

    step: str
    action: str
    name: str


class Admin(Filter):
    def __init__(self, admin_ids: frozenset[int]):
        self.admin_ids = admin_ids

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        allowed = event.from_user is not None and event.from_user.id in self.admin_ids
        if not allowed:
            who = event.from_user.id if event.from_user else "unknown"
            log.warning("ignored %s from %s", type(event).__name__, who)
        return allowed


def build_router(admin_ids: frozenset[int]) -> Router:
    router = Router()
    router.message.filter(Admin(admin_ids))
    router.callback_query.filter(Admin(admin_ids))
    router.message.outer_middleware(_command_cancels_a_question)
    router.message.register(help_, Command("start", "help"))
    router.message.register(add, Command("add"))
    router.message.register(reissue, Command("reissue"))
    router.message.register(delete, Command("del"))
    router.message.register(list_, Command("list"))
    router.message.register(add_named, StateFilter(AddDevice.name), F.text)
    router.callback_query.register(ask, Device.filter(F.step == "ask"))
    router.callback_query.register(confirmed, Device.filter(F.step == "do"))
    router.callback_query.register(cancelled, F.data == "cancel")
    return router


async def announce(bot: Bot, admin_ids: frozenset[int]) -> None:
    """The command menu, in the admins' chats only: a stranger's menu stays empty."""
    await bot.delete_my_commands()
    for chat_id in sorted(admin_ids):
        await bot.set_my_commands(COMMANDS, scope=BotCommandScopeChat(chat_id=chat_id))


async def _command_cancels_a_question(handler, message: Message, data: dict):
    # A command while the bot waits for a name means the owner moved on; the next text is not a name
    state: FSMContext | None = data.get("state")
    if state is not None and (message.text or "").startswith("/"):
        await state.clear()
    return await handler(message, data)


def tags(name: str) -> str:
    """#awg and the device's tag. A hashtag stops at '-' and '.', and one of digits alone is none."""
    own = re.sub(r"\W", "_", name, flags=re.ASCII)
    if own.isdigit():
        own = f"awg_{own}"
    return f"#awg #{own}"


def _header(name: str, status: str) -> str:
    return f"<b>{html.escape(name)}</b> · {html.escape(status)}\n{tags(name)}"


def _button(text: str, step: str, action: str, name: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=Device(step=step, action=action, name=name).pack())


def _devices_keyboard(names: list[str], only: str | None = None) -> InlineKeyboardMarkup:
    """Reissue and Delete for each device, or the one action asked for."""
    rows = []
    for name in names:
        row = []
        if only in (None, "reissue"):
            row.append(_button(f"Reissue {name}", "ask", "reissue", name))
        if only in (None, "del"):
            row.append(_button(f"Delete {name}", "ask", "del", name))
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def help_(message: Message) -> None:
    await message.answer(HELP)


def _name(command: CommandObject) -> str | None:
    name = (command.args or "").strip()
    if " " in name:
        raise ClientError(f"One name, please: /{command.command} <name>")
    return name or None


async def _guarded(message: Message, what: str, action) -> None:
    try:
        await action()
    except ClientError as e:
        log.info("refused %r: %s", what, e)
        await message.answer(str(e))
    except (AwgError, OSError) as e:
        log.exception("failed %r", what)
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


async def _add(message: Message, service: Service, name: str) -> None:
    async def action():
        issued = await service.add(name)
        await send_issued(message, issued, "new device")

    await _guarded(message, f"/add {name}", action)


async def _reissue(message: Message, service: Service, name: str) -> None:
    async def action():
        issued = await service.reissue(name)
        await send_issued(message, issued, "new keys, the old config no longer works")

    await _guarded(message, f"/reissue {name}", action)


async def _delete(message: Message, service: Service, name: str) -> None:
    async def action():
        address = await service.delete(name)
        await message.answer(_header(name, f"removed, {address} is free"), parse_mode=ParseMode.HTML)

    await _guarded(message, f"/del {name}", action)


async def _pick(message: Message, service: Service, action: str) -> None:
    """A command that needs a device and was given none: the devices, as buttons."""
    names = [c.name for c in await service.list()]
    if not names:
        await message.answer("No devices yet; a new one is /add")
        return
    verb = "Reissue" if action == "reissue" else "Delete"
    await message.answer(f"{verb} which device?", reply_markup=_devices_keyboard(names, only=action))


async def add(message: Message, command: CommandObject, service: Service, state: FSMContext) -> None:
    try:
        name = _name(command)
    except ClientError as e:
        await message.answer(str(e))
        return
    if name is None:
        await state.set_state(AddDevice.name)
        await message.answer(
            "The name of the new device? Latin letters, digits, '.', '-' or '_'.",
            reply_markup=ForceReply(input_field_placeholder="my-phone"),
        )
        return
    await _add(message, service, name)


async def add_named(message: Message, service: Service, state: FSMContext) -> None:
    await state.clear()
    await _add(message, service, message.text.strip())


async def reissue(message: Message, command: CommandObject, service: Service) -> None:
    await _by_name(message, command, service, "reissue", _reissue)


async def delete(message: Message, command: CommandObject, service: Service) -> None:
    await _by_name(message, command, service, "del", _delete)


async def _by_name(message, command, service, action, run) -> None:
    try:
        name = _name(command)
    except ClientError as e:
        await message.answer(str(e))
        return
    if name is None:
        await _pick(message, service, action)
    else:
        await run(message, service, name)


async def list_(message: Message, service: Service) -> None:
    async def action():
        clients = await service.list()
        if not clients:
            await message.answer("No devices yet; a new one is /add")
            return
        await message.answer(
            "\n".join(describe(c) for c in clients),
            reply_markup=_devices_keyboard([c.name for c in clients]),
        )

    await _guarded(message, "/list", action)


async def ask(query: CallbackQuery, callback_data: Device) -> None:
    """Both buttons cut a config off, so each asks first."""
    name = callback_data.name
    if callback_data.action == "reissue":
        question = f"Reissue {name}? Its current config stops working, a new one comes here."
        yes = f"Yes, reissue {name}"
    else:
        question = f"Delete {name}? Its config stops working, and its address is freed."
        yes = f"Yes, delete {name}"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(yes, "do", callback_data.action, name),
                InlineKeyboardButton(text="No", callback_data="cancel"),
            ]
        ]
    )
    await query.answer()
    await query.message.answer(question, reply_markup=keyboard)


async def confirmed(query: CallbackQuery, callback_data: Device, service: Service) -> None:
    await query.answer()
    # The buttons go first: a second tap on Yes has nothing left to press
    await query.message.edit_reply_markup(reply_markup=None)
    run = _reissue if callback_data.action == "reissue" else _delete
    await run(query.message, service, callback_data.name)


async def cancelled(query: CallbackQuery) -> None:
    await query.answer("Nothing changed")
    await query.message.edit_reply_markup(reply_markup=None)
