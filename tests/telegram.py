"""Telegram around the bot: updates go in through the real dispatcher, the API calls come out.

RecordingBot takes the place of the network: every method the bot calls is recorded and nothing
is sent. A sent message comes back with an id of its own, as Telegram returns it, so a reply can be
told from a message of its own. Text is read the way a chat shows it — the HTML markup rendered
away, a code block kept apart — and the images are read back with a QR decoder, frame by frame of an
animation, the way a phone camera reads them.
"""

import html
import io
import re
from datetime import UTC, datetime
from itertools import count

import zxingcpp
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.methods import SendAnimation, SendDocument, SendMediaGroup, SendMessage, SendPhoto
from aiogram.types import Chat, Message, Update, User
from PIL import Image, ImageSequence

from awg31_bot.bot import build_router

_ids = count(1)
SENDS = (SendMessage, SendDocument, SendPhoto, SendAnimation)


class RecordingBot(Bot):
    def __init__(self):
        super().__init__("123456:TEST")
        self.calls = []
        # id of the message Telegram would have given each call that sends one
        self.sent: dict[int, object] = {}

    async def __call__(self, method, request_timeout=None):
        self.calls.append(method)
        if isinstance(method, SENDS):
            n = next(_ids)
            self.sent[id(method)] = n
            return Message(message_id=n, date=datetime.now(UTC), chat=Chat(id=method.chat_id, type="private"))
        return True

    def message_id(self, call) -> int:
        return self.sent[id(call)]


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


def shown(call) -> str:
    """What the chat shows of a text message or a caption."""
    text = call.text if isinstance(call, SendMessage) else (call.caption or "")
    if call.parse_mode == ParseMode.HTML:
        text = html.unescape(re.sub(r"<[^>]+>", "", text))
    return text


def code_blocks(bot: RecordingBot) -> list[str]:
    """The contents of every code block, the part of a message Telegram copies with one tap."""
    out = []
    for c in bot.calls:
        if isinstance(c, SendMessage) and c.parse_mode == ParseMode.HTML:
            out += [html.unescape(b) for b in re.findall(r"<pre>(.*?)</pre>", c.text, flags=re.S)]
    return out


def texts(bot: RecordingBot) -> list[str]:
    return [shown(c) for c in bot.calls if isinstance(c, SendMessage)]


def everything_shown(bot: RecordingBot) -> list[str]:
    """Every message and caption of a reply, in the order the chat shows them."""
    return [shown(c) for c in bot.calls if isinstance(c, SENDS)]


def documents(bot: RecordingBot) -> dict[str, bytes]:
    return {c.document.filename: c.document.data for c in bot.calls if isinstance(c, SendDocument)}


def images(bot: RecordingBot) -> list[bytes]:
    """Every picture sent, an animation as one entry."""
    out = []
    for c in bot.calls:
        if isinstance(c, SendPhoto):
            out.append(c.photo.data)
        elif isinstance(c, SendAnimation):
            out.append(c.animation.data)
        elif isinstance(c, SendMediaGroup):
            out.extend(m.media.data for m in c.media)
    return out


def scan(picture: bytes) -> list[str]:
    """The code of a picture, or of every frame of an animation, in the order the frames play."""
    found = []
    for frame in ImageSequence.Iterator(Image.open(io.BytesIO(picture))):
        # QR only, as the app's scanner: among all formats the decoder now and then reads a 1D
        # barcode into the noise of the modules
        codes = zxingcpp.read_barcodes(frame.convert("L"), formats=zxingcpp.BarcodeFormat.QRCode)
        assert len(codes) == 1, f"{len(codes)} codes in one frame"
        found.append(codes[0].text)
    return found
