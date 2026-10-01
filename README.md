# awg31-telegram-bot

A Telegram bot that adds AmneziaWG 3.x clients to a server running the `amneziawg` kernel module, on the fly, and sends each client its config three ways: a `vpn://` link, a `.conf` file and a series of QR codes, looping in one animation, that the AmneziaVPN app scans from another screen.

It is meant for a small server kept by hand or by configuration management, where a web panel would be one more thing published to the internet. The bot opens no port — it long-polls Telegram — and answers only the Telegram ids it is given.

## How it fits a server

The server's `awg0.conf` holds the `[Interface]` section only. The clients live in a peers file of their own, which the interface's `PostUp` loads:

```ini
PostUp = ...; awg addconf %i /var/lib/awg31-bot/awg0.peers.conf
```

The bot owns that file. Every change goes to the running interface first, with `awg set`, and to the file second; if the file cannot be written, the interface change is rolled back. Other clients keep their sessions throughout.

The 3.x parameter set — junk, padding, headers, header protection, the I packets, the timings — is read from `awg showconf` of the running interface, so the configs the bot issues always match what the server runs.

**Never `systemctl reload awg-quick@awg0`.** Its reload is `awg syncconf` over `awg0.conf`, which has no clients, and it takes every client off the interface. Restart instead: `PostUp` loads the peers file again.

## Commands

| Command | What it does |
|:--|:--|
| `/add <name>` | A new device: new keys and preshared key, the lowest free address, the config sent back |
| `/reissue <name>` | New keys for a device on the same address. The old config stops working |
| `/del <name>` | Removes the device |
| `/list` | Devices with their address, latest handshake and traffic |

Names are Latin letters, digits, `.`, `-` and `_`, up to 32 characters.

## What a device's config looks like in the chat

Three messages. The first is the status — the device, what happened, its address — with the `vpn://` link in a code block, which Telegram copies with one tap. The `.conf` file and the QR animation come as replies to it: Telegram takes no file with a text message and no more than 1024 characters under a file, and the link is about 4000. Every message of a device carries `#awg` and the device's own tag (`#my_phone` for `my-phone`: a hashtag ends at `-` and `.`), so the history of a device is one search.

AmneziaVPN names the connection after the `name` of the settings alone; a phone shows about a dozen characters of it.

Every change that took effect is logged — `added phone at 10.66.66.2` — and so is every refused command; no key goes to the journal.

## Where the private keys are

Nowhere but in the Telegram message: the bot generates a client's keys, puts them in the config it sends and forgets them. The peers file has the public key and the preshared key of each client. A lost config is not resent — it is reissued, which also cuts off a lost device.

## The vpn:// link and the QR series

Both carry the JSON AmneziaVPN exports, compressed like Qt's `qCompress`. A config with three I packets of real-protocol size is about 4 KB, more than one QR code holds (2953 bytes), so the bot cuts it into the app's own QR series: chunks of 850 bytes framed with the magic 1984. The app joins the chunks in whatever order they are scanned, so the bot sends the series as one looping animation, a code a second: held up to the app's scanner, it hands over every chunk in turn. A link that does not fit one Telegram message (4096 characters) beside the status goes as a file.

## Running it

Needs Python 3.12+, `amneziawg-tools` and `CAP_NET_ADMIN` for `awg set`. A systemd unit:

```ini
[Unit]
Description=awg31-telegram-bot
After=network-online.target awg-quick@awg0.service
Wants=network-online.target

[Service]
User=awg31-bot
ExecStart=/opt/awg31-bot/venv/bin/awg31-bot --config /etc/awg31-bot/config.toml
AmbientCapabilities=CAP_NET_ADMIN
CapabilityBoundingSet=CAP_NET_ADMIN
NoNewPrivileges=true
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

The settings are in [`config.example.toml`](config.example.toml). Each release carries the wheel and a `requirements.txt` with hashes, for `pip install --require-hashes`.

## Development

```sh
uv run pytest        # with coverage, 85% at least
uv run ruff check .
uv run ruff format --check .
```

## Credits

The `vpn://` JSON follows [mycelium-mesh/amneziawg-ui](https://github.com/mycelium-mesh/amneziawg-ui) (Apache-2.0); the compression and the QR series follow [amnezia-client](https://github.com/amnezia-vpn/amnezia-client).
