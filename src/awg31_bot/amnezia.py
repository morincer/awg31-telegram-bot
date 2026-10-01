"""The AmneziaVPN app's own formats: a vpn:// link and a series of QR codes.

Both carry the same JSON the app exports, compressed the way Qt's qCompress does it: four bytes of
the uncompressed length, big-endian, then a zlib stream. The link is "vpn://" and that, base64url
without padding (ExportController::generateVpnUrl). The QR series cuts the compressed bytes into
chunks of 850 and wraps each in a QDataStream frame — qint16 magic 1984, quint8 chunk count,
quint8 chunk number, QByteArray (quint32 length, bytes) — base64url again, one QR code at error
correction L each (qrCodeUtils::generateQrCodeImageSeries). The app scans the chunks in any order
and joins them (ImportController::parseQrCodeChunk).

Why a series: a config with three I packets of 556 bytes is about 4 KB, and one QR code holds
2953 bytes at most. The series goes out as one looping animation, a code a second: held up to a
camera, it hands the app every chunk in turn without anybody swiping.

The JSON follows the one mycelium-mesh/amneziawg-ui builds (Apache-2.0,
internal/amnezialink/link.go) for AmneziaWG 3.1. last_config carries no "config", the native config
text: the app reads it only where allowed_ips or persistent_keep_alive are missing
(VpnConnection::appendSplitTunnelingConfig), and with three I packets it would take the link past
the 4096 characters of one Telegram message.
"""

import base64
import io
import json
import math
import struct
import zlib

import segno
from PIL import Image

from .clientconf import Client, Server

CONTAINER = "amnezia-awg2"
# protocols::awg::awgV3 of the app, the literal string "3.1" in every release that knows 3.x
PROTOCOL_VERSION = "3.1"
QR_MAGIC = 1984
QR_CHUNK = 850


def config_json(
    server: Server,
    client: Client,
    *,
    endpoint: str,
    dns: tuple[str, ...],
    mtu: int,
    server_ip: str,
    prefix: int,
    description: str,
) -> dict:
    obfuscation = dict(server.obfuscation)
    client_obj = {
        "hostName": endpoint,
        "port": server.port,
        "client_ip": str(client.address),
        "client_priv_key": client.private_key,
        "client_pub_key": client.public_key,
        "server_pub_key": server.public_key,
        "psk_key": client.preshared_key,
        "clientId": client.public_key,
        "allowed_ips": ["0.0.0.0/0", "::/0"],
        "mtu": str(mtu),
        "persistent_keep_alive": "25",
        **obfuscation,
    }
    awg_obj = {
        "port": str(server.port),
        "transport_proto": "udp",
        "protocol_version": PROTOCOL_VERSION,
        "subnet_address": server_ip,
        "subnet_cidr": str(prefix),
        "isThirdPartyConfig": True,
        **obfuscation,
        "last_config": json.dumps(client_obj, separators=(",", ":")),
    }
    root = {
        "containers": [{"container": CONTAINER, "awg": awg_obj}],
        "defaultContainer": CONTAINER,
        "description": description,
        "hostName": endpoint,
    }
    if dns:
        root["dns1"] = dns[0]
    if len(dns) > 1:
        root["dns2"] = dns[1]
    return root


def qcompress(data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + zlib.compress(data, 8)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def compressed(config: dict) -> bytes:
    return qcompress(json.dumps(config, separators=(",", ":")).encode())


def vpn_link(config: dict) -> str:
    return "vpn://" + _b64(compressed(config))


def qr_chunks(config: dict) -> list[str]:
    data = compressed(config)
    count = math.ceil(len(data) / QR_CHUNK)
    if count > 255:
        raise ValueError("the config does not fit 255 QR codes")
    chunks = []
    for n in range(count):
        part = data[n * QR_CHUNK : (n + 1) * QR_CHUNK]
        frame = struct.pack(">hBB", QR_MAGIC, count, n) + struct.pack(">I", len(part)) + part
        chunks.append(_b64(frame))
    return chunks


def qr_png(text: str, scale: int = 6) -> bytes:
    buf = io.BytesIO()
    segno.make(text, error="l", micro=False, boost_error=False).save(buf, kind="png", scale=scale, border=4)
    return buf.getvalue()


def qr_gif(chunks: list[str], scale: int = 6, frame_ms: int = 1000) -> bytes:
    """The QR series as one looping GIF, a frame per code.

    The last chunk is shorter and its code smaller; every frame is centred on a canvas of the largest
    one, so the picture does not jump between frames.
    """
    frames = [Image.open(io.BytesIO(qr_png(chunk, scale))).convert("L") for chunk in chunks]
    side = max(max(f.size) for f in frames)
    canvas = []
    for frame in frames:
        page = Image.new("L", (side, side), 255)
        page.paste(frame, ((side - frame.width) // 2, (side - frame.height) // 2))
        canvas.append(page)
    buf = io.BytesIO()
    canvas[0].save(buf, format="GIF", save_all=True, append_images=canvas[1:], duration=frame_ms, loop=0)
    return buf.getvalue()
