"""awg31-bot --config /etc/awg31-bot/config.toml"""

import argparse
import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher

from . import config as config_module
from .awg import Awg
from .bot import announce, build_router
from .service import Service


async def serve(config: config_module.Config) -> None:
    dp = Dispatcher()
    dp["service"] = Service(config, Awg(config.awg, config.interface))
    dp.include_router(build_router(config.admin_ids))
    bot = Bot(config.token)
    try:
        await announce(bot, config.admin_ids)
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        await bot.session.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="awg31-bot", description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("/etc/awg31-bot/config.toml"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(serve(config_module.load(args.config)))


if __name__ == "__main__":
    main()
