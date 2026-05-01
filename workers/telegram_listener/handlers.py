"""텔레그램 명령어 핸들러 (multi-user, 소규모 화이트리스트).

지원 명령어:
  /start              — 사용자 등록 요청 (status=pending → admin 승인 대기)
  /help               — 명령어 안내
  /add 005930         — 보유 종목 추가 (active 사용자)
  /remove 005930      — 보유 종목 제거 (active 사용자)
  /list               — 보유 종목 조회 (active 사용자)
  /recent             — 최근 7일 추천 (active 사용자)
  /recent 2026-04-25  — 특정 날짜 추천
  /approve <chat_id>  — admin이 pending 사용자 승인

인가 정책:
- 봇 자체는 누구나 메시지 송신 가능
- /start만은 누구나 가능 (등록 요청)
- 그 외 명령어는 backend `/users/by-chat-id`로 status='active'인 사용자만 처리
- pending/inactive/미등록은 등록 안내 메시지만 응답
- /approve는 is_admin=true인 사용자만 처리
"""
import os
import re
from datetime import date as date_type

import structlog
from telegram import Update
from telegram.ext import ContextTypes

from core.http import BackendClient

logger = structlog.get_logger()

TICKER_PATTERN = re.compile(r"^\d{6}$")
DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
CHAT_ID_PATTERN = re.compile(r"^-?\d+$")

# 부트스트랩 admin 화이트리스트 (콤마 구분). 첫 등록 시 자동으로 active+admin 처리.
# 운영 시작 시점에 .env에 자기 chat_id를 설정 → /start로 자동 admin 승계.
_RAW_ADMIN_BOOTSTRAP = os.environ.get("TELEGRAM_ADMIN_CHAT_IDS", "")
ADMIN_BOOTSTRAP_IDS = {
    int(c.strip()) for c in _RAW_ADMIN_BOOTSTRAP.split(",") if c.strip().lstrip("-").isdigit()
}

WELCOME_PENDING = (
    "🤖 stock-signal 봇입니다.\n"
    "등록 요청이 접수됐습니다. admin 승인 후 명령어를 사용할 수 있습니다.\n"
    "(승인 후 /help로 명령어 목록을 확인하세요)"
)

WELCOME_ACTIVE = (
    "🤖 stock-signal 봇입니다.\n"
    "보유 종목을 등록해두면 매일 장 마감 후 다음 거래일 추천을 보내드립니다.\n\n"
    "/help — 명령어 안내"
)

HELP_TEXT = (
    "📖 *명령어 안내*\n\n"
    "/add 005930      — 보유 종목 추가\n"
    "/remove 005930   — 보유 종목 제거\n"
    "/list            — 보유 종목 조회\n"
    "/recent          — 최근 7일 추천\n"
    "/recent 2026-04-25 — 특정 날짜 추천\n"
    "/help            — 이 메시지\n\n"
    "(admin) /approve <chat_id> — 신규 사용자 승인"
)


# ─── 인증 헬퍼 ───────────────────────────────────────


def _chat_id(update: Update) -> int | None:
    chat = update.effective_chat
    if chat is None:
        return None
    return int(chat.id)


async def _reply(update: Update, text: str) -> None:
    if update.effective_chat:
        await update.effective_chat.send_message(text)


def _extract_args(context: ContextTypes.DEFAULT_TYPE) -> list[str]:
    return context.args or []


async def _resolve_active_user(
    update: Update, client: BackendClient
) -> dict | None:
    """현재 chat_id의 user를 backend에서 조회. active 아니면 안내 메시지 송신 후 None."""
    chat_id = _chat_id(update)
    if chat_id is None:
        return None
    status, body = await client.get_user(chat_id)
    if status == 404:
        await _reply(
            update,
            "❌ 등록되지 않은 사용자입니다. /start를 먼저 호출해 등록 요청해주세요.",
        )
        logger.warning("command_unauthorized_unknown_chat", chat_id=chat_id)
        return None
    if status != 200:
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
        logger.error("backend_error_resolve_user", chat_id=chat_id, status=status)
        return None
    if body.get("status") != "active":
        await _reply(
            update,
            f"⚠️ 등록 상태: {body.get('status')}. admin 승인 후 사용 가능합니다.",
        )
        logger.warning(
            "command_unauthorized_status", chat_id=chat_id, user_status=body.get("status")
        )
        return None
    return body


# ─── 핸들러 ────────────────────────────────────────────


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/start — register-or-fetch + admin bootstrap."""
    chat_id = _chat_id(update)
    if chat_id is None:
        return
    user_obj = update.effective_user
    username = (user_obj.username if user_obj else None) or None

    client: BackendClient = context.bot_data["backend"]
    logger.info("command_received", command="/start", chat_id=chat_id)

    status, body = await client.register_user(chat_id, telegram_username=username)
    if status not in (200, 201):
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
        logger.error("register_user_failed", chat_id=chat_id, status=status)
        return

    is_active = body.get("status") == "active"
    is_admin = bool(body.get("is_admin"))

    # admin bootstrap: 화이트리스트 chat_id가 처음 들어왔으면 자체 승급
    if not is_active and chat_id in ADMIN_BOOTSTRAP_IDS:
        approve_status, _ = await client.approve_user(chat_id, chat_id)
        # backend는 approver를 admin으로만 받지만, bootstrap은 별도 경로로 처리되어야 함.
        # 현재 backend 정책상 self-approve 미지원 → 안내만.
        if approve_status == 200:
            is_active = True

    if is_active:
        suffix = " (admin)" if is_admin else ""
        await _reply(update, f"✅ 환영합니다!{suffix}\n\n{WELCOME_ACTIVE}")
    else:
        if chat_id in ADMIN_BOOTSTRAP_IDS:
            await _reply(
                update,
                "🤖 stock-signal 봇입니다.\n"
                f"화이트리스트 admin chat_id로 등록 요청됐습니다. "
                "운영자가 직접 DB에서 status='active', is_admin=true로 승격해주세요.\n"
                "(예: SQL `UPDATE users SET status='active', is_admin=true WHERE chat_id=...`)",
            )
        else:
            await _reply(update, WELCOME_PENDING)
    logger.info("command_processed", command="/start", chat_id=chat_id, status=body.get("status"))


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    logger.info("command_received", command="/help", chat_id=user["chat_id"])
    await _reply(update, HELP_TEXT)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    args = _extract_args(context)
    logger.info("command_received", command="/add", chat_id=chat_id, args=args)

    if len(args) != 1 or not TICKER_PATTERN.match(args[0]):
        await _reply(update, "❌ 종목코드는 6자리 숫자입니다. 예: /add 005930")
        return
    ticker = args[0]

    status, body = await client.add_holding(ticker, chat_id)
    if status == 201:
        name = body.get("name") or ticker
        await _reply(update, f"✅ 추가됨: {name} ({ticker})")
    elif status == 409:
        await _reply(update, f"⚠️ 이미 등록된 종목입니다: {ticker}")
    elif status >= 500:
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
    else:
        await _reply(update, f"❌ 오류: {body.get('message', '알 수 없는 오류')}")
    logger.info("command_processed", command="/add", chat_id=chat_id, status=status)


async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    args = _extract_args(context)
    logger.info("command_received", command="/remove", chat_id=chat_id, args=args)

    if len(args) != 1 or not TICKER_PATTERN.match(args[0]):
        await _reply(update, "❌ 종목코드는 6자리 숫자입니다. 예: /remove 005930")
        return
    ticker = args[0]

    status, body = await client.remove_holding(ticker, chat_id)
    if status == 204:
        await _reply(update, f"🗑 제거됨: {ticker}")
    elif status == 404:
        await _reply(update, f"⚠️ 등록되지 않은 종목입니다: {ticker}")
    elif status >= 500:
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
    else:
        await _reply(update, f"❌ 오류: {body.get('message', '알 수 없는 오류')}")
    logger.info("command_processed", command="/remove", chat_id=chat_id, status=status)


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    logger.info("command_received", command="/list", chat_id=chat_id)

    status, body = await client.list_holdings(chat_id)
    if status != 200:
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
        return
    items = body.get("items", [])
    if not items:
        await _reply(update, "📂 등록된 보유 종목이 없습니다. /add 005930 으로 추가하세요.")
        return
    lines = ["📂 보유 종목"]
    for it in items:
        name = it.get("name") or "(미확인)"
        lines.append(f"  • {name} ({it['ticker']})")
    await _reply(update, "\n".join(lines))
    logger.info("command_processed", command="/list", chat_id=chat_id, count=len(items))


async def recent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    args = _extract_args(context)
    logger.info("command_received", command="/recent", chat_id=chat_id, args=args)

    if args:
        if not DATE_PATTERN.match(args[0]):
            await _reply(update, "❌ 날짜 형식: YYYY-MM-DD. 예: /recent 2026-04-25")
            return
        try:
            date_type.fromisoformat(args[0])
        except ValueError:
            await _reply(update, "❌ 유효하지 않은 날짜입니다")
            return
        status, body = await client.get_recommendations_by_date(args[0])
    else:
        status, body = await client.get_recent_recommendations(limit=7)

    if status != 200:
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
        return

    items = body.get("items", [])
    if not items:
        await _reply(update, "📭 해당 기간 추천 이력이 없습니다.")
        return

    by_date: dict[str, list[dict]] = {}
    for it in items:
        by_date.setdefault(it["date"], []).append(it)

    lines = [f"📜 최근 추천 ({len(items)}건)"]
    for d in sorted(by_date.keys(), reverse=True):
        lines.append(f"\n📅 {d}")
        for it in by_date[d]:
            label = (it.get("name") or it["ticker"])
            symbol = {
                "buy_hedge": "🟢",
                "watch": "🟡",
                "exit_alert": "🔴",
            }.get(it["recommendation_type"], "·")
            lines.append(f"  {symbol} {label}({it['ticker']}) — {it['score']}")
    await _reply(update, "\n".join(lines))
    logger.info("command_processed", command="/recent", chat_id=chat_id, count=len(items))


async def approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/approve <chat_id> — admin 전용. backend가 admin 권한 검증."""
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    args = _extract_args(context)
    logger.info("command_received", command="/approve", chat_id=chat_id, args=args)

    if len(args) != 1 or not CHAT_ID_PATTERN.match(args[0]):
        await _reply(update, "❌ 사용법: /approve <chat_id>")
        return
    target = int(args[0])

    status, body = await client.approve_user(target, chat_id)
    if status == 200:
        await _reply(update, f"✅ 승인 완료: chat_id={target}")
    elif status == 403:
        await _reply(update, "⛔ admin 권한이 없습니다")
    elif status == 404:
        await _reply(update, f"⚠️ 등록되지 않은 chat_id입니다: {target}")
    else:
        await _reply(update, f"❌ 오류: {body.get('message', '알 수 없는 오류')}")
    logger.info("command_processed", command="/approve", chat_id=chat_id, target=target, status=status)


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """알 수 없는 명령어 — active 사용자에게만 안내."""
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    logger.info("command_received", command="(unknown)", chat_id=user["chat_id"])
    await _reply(update, "❓ 알 수 없는 명령어입니다. /help 를 참조하세요.")
