"""Telegram around the bot: updates go in through the real dispatcher, the API calls come out.

RecordingBot takes the place of the network: every method the bot calls is recorded and nothing
is sent. The images are read back with a QR decoder, the way a phone camera reads them.
"""

import io
from datetime import UTC, datetime
from itertools import count

import zxingcpp
from aiogram import Bot, Dispatcher
from aiogram.methods import SendDocument, SendMediaGroup, SendMessage, SendPhoto
from aiogram.types import Chat, Message, Update, User
from PIL import Image

from awg31_bot.bot import build_router

_ids = count(1)


class RecordingBot(Bot):
    def __init__(self):
        super().__init__("123456:TEST")
        self.calls = []

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        return True


def dispatcher(service, admin_ids=frozenset({42})) -> Dispatcher:
    dp = Dispatcher()
    dp["service"] = service
    dp.include_router(build_router(admin_ids))
    return dp


async def send(dp: Dispatcher, text: str, user_id: int = 42) -> RecordingBot:
    bot = RecordingBot()
    n = next(_ids)
    update = Update(
        update_id=n,
        message=Message(
            message_id=n,
            date=datetime.now(UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="someone"),
            text=text,
        ),
    )
    await dp.feed_update(bot, update)
    return bot


def texts(bot: RecordingBot) -> list[str]:
    return [c.text for c in bot.calls if isinstance(c, SendMessage)]


def documents(bot: RecordingBot) -> dict[str, bytes]:
    return {c.document.filename: c.document.data for c in bot.calls if isinstance(c, SendDocument)}


def images(bot: RecordingBot) -> list[bytes]:
    out = []
    for c in bot.calls:
        if isinstance(c, SendPhoto):
            out.append(c.photo.data)
        elif isinstance(c, SendMediaGroup):
            out.extend(m.media.data for m in c.media)
    return out


def scan(png: bytes) -> str:
    found = zxingcpp.read_barcodes(Image.open(io.BytesIO(png)))
    assert len(found) == 1, f"{len(found)} codes in one image"
    return found[0].text
