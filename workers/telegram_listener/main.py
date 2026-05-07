"""worker-telegram-listener 진입점.

python-telegram-bot Application을 long-polling으로 운영.
명령어 핸들러는 handlers.py에 정의. Backend HTTP 클라이언트는 bot_data에 주입.
"""
import os
import signal

import structlog
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

import handlers
from core.http import BackendClient
from core.logging import setup_logging

SERVICE_NAME = "worker-telegram-listener"
TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]


def main() -> None:
    setup_logging(SERVICE_NAME)
    logger = structlog.get_logger()

    application: Application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()

    backend = BackendClient()
    application.bot_data["backend"] = backend

    application.add_handler(CommandHandler("start", handlers.start))
    application.add_handler(CommandHandler("help", handlers.help_cmd))
    application.add_handler(CommandHandler("add", handlers.add))
    application.add_handler(CommandHandler("edit", handlers.edit))
    application.add_handler(CommandHandler("remove", handlers.remove))
    application.add_handler(CommandHandler("list", handlers.list_cmd))
    application.add_handler(CommandHandler("recent", handlers.recent))
    application.add_handler(CommandHandler("reason", handlers.reason))
    application.add_handler(CommandHandler("approve", handlers.approve))
    # 등록되지 않은 명령어 → 알림
    application.add_handler(MessageHandler(filters.COMMAND, handlers.unknown))

    logger.info("startup_complete")
    application.run_polling(stop_signals=[signal.SIGINT, signal.SIGTERM])


if __name__ == "__main__":
    main()
