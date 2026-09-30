"""How the AmneziaVPN app reads a vpn:// link and a QR series — the oracle of the format tests.

A port of amnezia-client (branch dev), written from its C++ and not from awg31_bot: nothing here
imports the bot, so a misreading of the format in the bot cannot be repeated here and pass.

- ImportController::extractConfigFromData, the Amnezia branch: strip "vpn://", QByteArray::fromBase64
  with Base64UrlEncoding | OmitTrailingEquals, qUncompress when it yields anything, then
  checkConfigFormat must say Amnezia ("containers" in the text) and the JSON must be an object.
- ImportController::parseQrCodeChunk: each scanned text is base64url; a QDataStream reads qint16
  magic, and for 1984 quint8 count, quint8 chunk number and a QByteArray (quint32 length, big-endian,
  0xFFFFFFFF for a null array). Chunks are kept by number until all have come, joined in number
  order and handed to extractConfigFromQr, which tries JSON, then qUncompress.
- qUncompress (Qt): four bytes of the expected length, big-endian, then a zlib stream; anything that
  does not inflate yields an empty array.
- rejectUnsupportedFormatVersion: "formatVersion" absent or not above the app's own is accepted.
"""

import base64
import binascii
import json
import struct
import zlib

QR_MAGIC = 1984
# serverConfigUtils::currentConfigFormatVersion is at least 0; a config without the key reads as 0
SUPPORTED_FORMAT_VERSION = 0


class ImportError_(Exception):
    """The app would show ImportInvalidConfigError or a format version error."""


def q_uncompress(data: bytes) -> bytes:
    if len(data) < 4:
        return b""
    (expected,) = struct.unpack(">I", data[:4])
    try:
        out = zlib.decompress(data[4:])
    except zlib.error:
        return b""
    return out if len(out) == expected else b""


def from_base64_url(text: str | bytes) -> bytes:
    raw = text.encode() if isinstance(text, str) else text
    try:
        return base64.urlsafe_b64decode(raw + b"=" * (-len(raw) % 4))
    except (binascii.Error, ValueError):
        return b""


def _as_amnezia_object(text: bytes) -> dict:
    if b"containers" not in text:
        raise ImportError_("checkConfigFormat does not see an Amnezia config")
    try:
        obj = json.loads(text)
    except ValueError:
        raise ImportError_("QJsonDocument::fromJson: not JSON") from None
    if not isinstance(obj, dict) or not obj:
        raise ImportError_("QJsonDocument::fromJson: not an object")
    if int(obj.get("formatVersion", 0)) > SUPPORTED_FORMAT_VERSION:
        raise ImportError_("format version newer than supported")
    return obj


def read_link(link: str) -> dict:
    ba = from_base64_url(link.replace("vpn://", ""))
    uncompressed = q_uncompress(ba)
    if uncompressed:
        ba = uncompressed
    return _as_amnezia_object(ba)


def read_qr_series(scanned: list[str]) -> dict:
    chunks: dict[int, bytes] = {}
    total = 0
    for code in scanned:
        frame = from_base64_url(code)
        (magic,) = struct.unpack(">h", frame[:2])
        if magic != QR_MAGIC:
            raise ImportError_(f"not a chunk: magic {magic}")
        count, number = frame[2], frame[3]
        if total != count:
            # The app starts over when a chunk of another series comes in
            chunks, total = {}, count
        (length,) = struct.unpack(">I", frame[4:8])
        chunks[number] = b"" if length == 0xFFFFFFFF else frame[8 : 8 + length]
    if not total or len(chunks) != total:
        raise ImportError_(f"{len(chunks)} of {total} chunks")
    data = b"".join(chunks[i] for i in range(total))
    uncompressed = q_uncompress(data)
    if not uncompressed:
        raise ImportError_("qUncompress of the joined chunks is empty")
    return _as_amnezia_object(uncompressed)
