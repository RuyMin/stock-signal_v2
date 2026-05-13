"""텔레그램 명령어 핸들러 (multi-user, 소규모 화이트리스트).

지원 명령어:
  /start                       — 사용자 등록 요청 (status=pending → admin 승인 대기)
  /help                        — 명령어 안내
  /add 005930                  — 보유 종목 추가 (이름은 KIS API가 자동 채움)
  /add 005930 75000            — 추가 + 평단가
  /add 005930 코리안리         — 추가 + 종목명 직접 입력
  /add 005930 코리안리 75000   — 추가 + 종목명 + 평단가
  /edit 005930 75000           — 평단가 갱신 (숫자)
  /edit 005930 코리안리        — 종목명 갱신 (비숫자)
  /edit 005930 -               — 평단가 제거
  /remove 005930               — 보유 종목 제거
  /list                        — 보유 종목 조회
  /recent                      — 최근 7일 추천
  /recent 2026-04-25           — 특정 날짜 추천
  /reason 005930               — 해당 종목의 최근 추천 판단 자세히 보기
  /approve <chat_id>           — admin이 pending 사용자 승인
  /announce <메시지>           — admin이 active 사용자 전원에게 공지 송신

인가 정책:
- 봇 자체는 누구나 메시지 송신 가능
- /start만은 누구나 가능 (등록 요청)
- 그 외 명령어는 backend `/users/by-chat-id`로 status='active'인 사용자만 처리
- pending/inactive/미등록은 등록 안내 메시지만 응답
- /approve, /announce는 is_admin=true인 사용자만 처리
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
PRICE_PATTERN = re.compile(r"^\d+(\.\d{1,2})?$")  # 양의 수, 소수점 둘째 자리까지

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
    "/add 005930                  — 보유 종목 추가 (이름 자동)\n"
    "/add 005930 75000            — 추가 + 평단가\n"
    "/add 005930 코리안리         — 추가 + 종목명\n"
    "/add 005930 코리안리 75000   — 추가 + 종목명 + 평단가\n"
    "/edit 005930 75000           — 평단가 갱신 (숫자)\n"
    "/edit 005930 코리안리        — 종목명 갱신 (비숫자)\n"
    "/edit 005930 -               — 평단가 제거\n"
    "/remove 005930               — 보유 종목 제거\n"
    "/list                        — 보유 종목 조회\n"
    "/recent                      — 최근 7일 추천\n"
    "/recent 2026-04-25           — 특정 날짜 추천\n"
    "/reason 005930               — 해당 종목 최근 판단 자세히\n"
    "/help                        — 이 메시지\n\n"
    "(admin) /approve <chat_id> — 신규 사용자 승인\n"
    "(admin) /announce <메시지> — active 사용자 전원에게 공지"
)


_TYPE_DISPLAY: dict[str, tuple[str, str]] = {
    "buy_hedge": ("🟢", "매수 헬지"),
    "watch": ("🟡", "관망"),
    "exit_alert": ("🔴", "탈출 경보"),
}


def _fmt_int_with_sign(n) -> str:
    """+1,234 또는 -1,234 형태."""
    try:
        v = int(n)
    except (TypeError, ValueError):
        return "?"
    return f"{v:+,}"


def _fmt_price(value, suffix: str = "원") -> str:
    if value is None:
        return "?"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "?"
    return f"{int(v):,}{suffix}" if v == int(v) else f"{v:,.2f}{suffix}"


def _format_reason_message(detail: dict) -> str:
    """RecommendationDetailResponse dict → 자세한 사람-친화 메시지.

    구성: 헤더 / 분류 / 📊수급(시그널 raw + 추정 평단가) / 📰뉴스 N건 / 🌐매크로 5지표 / 💼보유정보.
    데이터 부족 시 해당 섹션은 자동 생략.
    """
    rec = detail.get("recommendation") or {}
    icon, type_label = _TYPE_DISPLAY.get(
        rec.get("recommendation_type", ""), ("·", rec.get("recommendation_type", "?"))
    )
    name = rec.get("name") or "(종목명 미확인)"
    ticker = rec.get("ticker", "")
    score = rec.get("score", "?")
    issued = rec.get("date") or "?"
    target = rec.get("target_trading_date") or "?"

    lines = [
        f"🔍 {name} ({ticker})",
        "",
        f"발행일: {issued} → 대상 거래일: {target}",
        f"분류: {icon} {type_label} (스코어 {score})",
    ]

    # ─── 📊 수급 ────────────────────────────────────────────
    signals = detail.get("signals") or []
    inst_avg = detail.get("institutional_avg")
    foreign_consec = detail.get("foreign_consecutive_buy_days")
    agency_consec = detail.get("agency_consecutive_buy_days")
    if signals or rec.get("reason_supply"):
        lines.append("")
        lines.append("📊 수급")
        # 가장 최근 signal raw 표시
        latest = signals[0] if signals else None
        if latest:
            lines.append(f"  • {latest['date']}")
            if latest.get("foreign_net_buy") is not None:
                lines.append(f"    외국인 순매수: {_fmt_int_with_sign(latest['foreign_net_buy'])}주")
            if latest.get("agency_net_buy") is not None:
                lines.append(f"    기관 순매수:   {_fmt_int_with_sign(latest['agency_net_buy'])}주")
        # 분리된 연속 매수일 (외인/기관 별도)
        if foreign_consec is not None or agency_consec is not None:
            f_part = f"외국인 {foreign_consec or 0}일"
            a_part = f"기관 {agency_consec or 0}일"
            lines.append(f"    연속 매수일:   {f_part} / {a_part}")
        if inst_avg:
            avg_str = _fmt_price(inst_avg.get("avg_price"))
            d = inst_avg.get("days", 0)
            lines.append(f"  ▸ 외+기관 추정 평단가 ≈ {avg_str} ({d}일 가중, 추정값)")
        if rec.get("reason_supply"):
            lines.append(f"  ▸ {rec['reason_supply']}")

    # ─── 📰 뉴스 ────────────────────────────────────────────
    news = detail.get("news") or []
    if news or rec.get("reason_news"):
        lines.append("")
        lines.append(f"📰 뉴스 ({len(news)}건)")
        for n in news:
            title = n.get("title", "")
            d = n.get("date", "")
            lines.append(f"  • {title} ({d})")
        if rec.get("reason_news"):
            lines.append(f"  ▸ {rec['reason_news']}")

    # ─── 🌐 매크로 ──────────────────────────────────────────
    macro = detail.get("macro")
    if macro or rec.get("reason_macro"):
        lines.append("")
        lines.append("🌐 매크로")
        if macro:
            lines.append(f"  ({macro.get('date', '?')} 미국 종가)")
            parts = []
            if macro.get("us10y") is not None:
                parts.append(f"US10Y {macro['us10y']:.2f}%")
            if macro.get("dxy") is not None:
                parts.append(f"DXY {macro['dxy']:.2f}")
            if macro.get("wti") is not None:
                parts.append(f"WTI {macro['wti']:.2f}")
            if parts:
                lines.append("  " + "  ".join(parts))
            parts2 = []
            if macro.get("sp500") is not None:
                parts2.append(f"S&P500 {macro['sp500']:,.2f}")
            if macro.get("gold") is not None:
                parts2.append(f"Gold {macro['gold']:,.2f}")
            if parts2:
                lines.append("  " + "  ".join(parts2))
        if rec.get("reason_macro"):
            lines.append(f"  ▸ {rec['reason_macro']}")

    # ─── 💰 추천 추정 매집가 (buy_hedge 한정, LLM 산출) ───────
    if rec.get("recommendation_type") == "buy_hedge" and rec.get("estimated_avg_price") is not None:
        try:
            price = int(float(rec["estimated_avg_price"]))
            lines.append("")
            lines.append(f"💰 추천 추정 매집가: {price:,}원")
        except (TypeError, ValueError):
            pass

    # ─── 💼 내 보유 정보 ────────────────────────────────────
    holding = detail.get("holding")
    if holding:
        lines.append("")
        lines.append("💼 내 보유 정보")
        if holding.get("avg_price") is not None:
            lines.append(f"  평단가: {_fmt_price(holding['avg_price'])}")
        else:
            lines.append("  평단가 미등록 (`/edit {ticker} <가격>`로 설정 가능)".format(ticker=ticker))

    lines.append("")
    lines.append("⚠️ 최종 판단은 본인이 직접 하세요")
    return "\n".join(lines)


def _format_price(value) -> str:
    """평단가를 사람이 읽기 좋은 형태로 (정수면 콤마, 소수면 그대로)."""
    if value is None:
        return "—"
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return f"{int(f):,}원"
    return f"{f:,.2f}원"


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


async def _notify_admins_new_registration(
    context: ContextTypes.DEFAULT_TYPE,
    client: BackendClient,
    new_user: dict,
) -> None:
    """신규 pending 사용자 등록을 active admin들에게 텔레그램으로 알림.

    backend `/users` 목록에서 is_admin=true & status=active인 chat_id를 추려 fan-out.
    한 admin 송신 실패는 try/except로 격리(다른 admin과 등록 흐름 자체에 영향 없음).
    """
    new_chat_id = new_user.get("chat_id")
    list_status, body = await client.list_users()
    if list_status != 200:
        logger.warning("admin_notify_skipped_list_failed", status=list_status)
        return
    admins = [
        u for u in body.get("items", [])
        if u.get("is_admin") and u.get("status") == "active"
        and u.get("chat_id") != new_chat_id
    ]
    if not admins:
        logger.warning("admin_notify_skipped_no_active_admin", new_chat_id=new_chat_id)
        return

    username = new_user.get("telegram_username")
    username_line = f"@{username}" if username else "(미설정)"
    text = (
        "🆕 신규 사용자 등록 요청\n"
        f"chat_id: {new_chat_id}\n"
        f"username: {username_line}\n\n"
        f"승인: /approve {new_chat_id}"
    )
    bot = context.bot
    for admin in admins:
        admin_chat_id = admin.get("chat_id")
        try:
            await bot.send_message(chat_id=admin_chat_id, text=text)
            logger.info("admin_notified_new_registration",
                        admin_chat_id=admin_chat_id, new_chat_id=new_chat_id)
        except Exception as exc:
            logger.error("admin_notify_failed",
                         admin_chat_id=admin_chat_id, new_chat_id=new_chat_id,
                         error=str(exc))


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
    is_new_pending = status == 201 and body.get("status") == "pending"

    if is_new_pending and chat_id not in ADMIN_BOOTSTRAP_IDS:
        await _notify_admins_new_registration(context, client, body)

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


_ADD_USAGE = (
    "❌ 사용법:\n"
    "  /add 005930                — ticker만\n"
    "  /add 005930 75000          — + 평단가\n"
    "  /add 005930 코리안리       — + 종목명\n"
    "  /add 005930 코리안리 75000 — + 종목명 + 평단가"
)


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    args = _extract_args(context)
    logger.info("command_received", command="/add", chat_id=chat_id, args=args)

    if not args or not TICKER_PATTERN.match(args[0]) or len(args) > 3:
        await _reply(update, _ADD_USAGE)
        return
    ticker = args[0]
    name: str | None = None
    avg_price: str | None = None

    if len(args) == 2:
        # 2nd arg: 숫자면 평단가, 비숫자면 종목명 (auto-detect)
        if PRICE_PATTERN.match(args[1]):
            avg_price = args[1]
        else:
            name = args[1]
    elif len(args) == 3:
        # 2nd=name, 3rd=price (3rd는 반드시 숫자)
        name = args[1]
        if not PRICE_PATTERN.match(args[2]):
            await _reply(update, "❌ 3번째 인자(평단가)는 양의 숫자입니다. 예: /add 005930 코리안리 75000")
            return
        avg_price = args[2]

    status, body = await client.add_holding(
        ticker, chat_id, name=name, avg_price=avg_price
    )
    if status == 201:
        display_name = body.get("name") or ticker
        extras: list[str] = []
        if avg_price is not None:
            extras.append(f"평단가 {_format_price(body.get('avg_price'))}")
        suffix = f" — {', '.join(extras)}" if extras else ""
        await _reply(update, f"✅ 추가됨: {display_name} ({ticker}){suffix}")
    elif status == 409:
        await _reply(update, f"⚠️ 이미 등록된 종목입니다: {ticker}")
    elif status >= 500:
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
    else:
        await _reply(update, f"❌ 오류: {body.get('message', '알 수 없는 오류')}")
    logger.info("command_processed", command="/add", chat_id=chat_id, status=status)


_EDIT_USAGE = (
    "❌ 사용법:\n"
    "  /edit 005930 75000     — 평단가 갱신 (숫자)\n"
    "  /edit 005930 코리안리  — 종목명 갱신 (비숫자)\n"
    "  /edit 005930 -         — 평단가 제거"
)


async def edit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/edit <ticker> <value> — value가 숫자면 평단가, '-'면 평단가 제거, 그 외는 종목명."""
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    args = _extract_args(context)
    logger.info("command_received", command="/edit", chat_id=chat_id, args=args)

    if len(args) != 2 or not TICKER_PATTERN.match(args[0]):
        await _reply(update, _EDIT_USAGE)
        return
    ticker = args[0]
    raw = args[1]

    edited: str  # 결과 메시지에 무엇을 갱신했는지 표시
    if raw == "-":
        status, body = await client.update_holding(
            ticker, chat_id, clear_avg_price=True
        )
        edited = "price_clear"
    elif PRICE_PATTERN.match(raw):
        status, body = await client.update_holding(
            ticker, chat_id, avg_price=raw
        )
        edited = "price_set"
    else:
        # 비숫자 → 종목명 갱신
        status, body = await client.update_holding(
            ticker, chat_id, name=raw
        )
        edited = "name_set"

    if status == 200:
        name = body.get("name") or ticker
        price = body.get("avg_price")
        if edited == "price_clear":
            await _reply(update, f"✏️ 평단가 제거됨: {name} ({ticker})")
        elif edited == "price_set":
            await _reply(update, f"✏️ 평단가 갱신: {name} ({ticker}) — {_format_price(price)}")
        else:  # name_set
            await _reply(update, f"✏️ 종목명 갱신: {name} ({ticker})")
    elif status == 404:
        await _reply(update, f"⚠️ 등록되지 않은 종목입니다: {ticker}")
    elif status >= 500:
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
    else:
        await _reply(update, f"❌ 오류: {body.get('message', '알 수 없는 오류') if body else '알 수 없는 오류'}")
    logger.info(
        "command_processed",
        command="/edit", chat_id=chat_id, status=status, edited=edited,
    )


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
        avg_price = it.get("avg_price")
        if avg_price is not None:
            lines.append(f"  • {name} ({it['ticker']}) — 평단가 {_format_price(avg_price)}")
        else:
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


async def reason(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/reason 005930 — 해당 종목의 가장 최근 추천 판단을 자세히 표시."""
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    args = _extract_args(context)
    logger.info("command_received", command="/reason", chat_id=chat_id, args=args)

    if len(args) != 1 or not TICKER_PATTERN.match(args[0]):
        await _reply(update, "❌ 사용법: /reason 005930")
        return
    ticker = args[0]

    status, body = await client.get_recommendation_by_ticker(ticker, chat_id=chat_id)
    if status == 200:
        await _reply(update, _format_reason_message(body))
    elif status == 404:
        await _reply(update, f"⚠️ {ticker}에 대한 추천 이력이 없습니다.")
    elif status >= 500:
        await _reply(update, "⚠️ 잠시 후 다시 시도해주세요")
    else:
        await _reply(update, f"❌ 오류: {body.get('message', '알 수 없는 오류')}")
    logger.info("command_processed", command="/reason", chat_id=chat_id, status=status)


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


async def announce(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/announce <메시지> — admin 전용. active 사용자(본인 제외) 전원에게 공지 송신."""
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    chat_id = user["chat_id"]
    if not user.get("is_admin"):
        await _reply(update, "⛔ admin 권한이 필요합니다")
        logger.warning("announce_denied_non_admin", chat_id=chat_id)
        return

    # 메시지 본문은 텔레그램 raw text에서 직접 추출 (멀티라인/공백 보존).
    raw_text = (update.effective_message.text or "") if update.effective_message else ""
    parts = raw_text.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await _reply(update, "❌ 사용법: /announce <메시지>")
        return
    body = parts[1].strip()
    logger.info("command_received", command="/announce", chat_id=chat_id, length=len(body))

    # active 사용자 목록
    list_status, list_body = await client.list_users()
    if list_status != 200:
        await _reply(update, "⚠️ 사용자 목록 조회 실패 — 잠시 후 다시 시도해주세요")
        logger.error("announce_list_users_failed", status=list_status)
        return

    targets = [
        u for u in (list_body.get("items") or [])
        if u.get("status") == "active" and u.get("chat_id") != chat_id
    ]
    if not targets:
        await _reply(update, "⚠️ 송신 대상 active 사용자가 없습니다")
        return

    formatted = f"📢 공지\n\n{body}"
    bot = context.bot
    sent: list[int] = []
    failed: list[int] = []
    for t in targets:
        target = t.get("chat_id")
        try:
            await bot.send_message(chat_id=target, text=formatted, disable_web_page_preview=True)
            sent.append(target)
            logger.info("announce_sent", target_chat_id=target)
        except Exception as exc:  # noqa: BLE001
            failed.append(target)
            logger.error("announce_send_failed", target_chat_id=target, error=str(exc))

    summary = f"✅ 공지 송신 완료: {len(sent)}명"
    if failed:
        summary += f" / 실패 {len(failed)}명"
    await _reply(update, summary)
    logger.info("command_processed", command="/announce", sent=len(sent), failed=len(failed))


async def unknown(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """알 수 없는 명령어 — active 사용자에게만 안내."""
    client: BackendClient = context.bot_data["backend"]
    user = await _resolve_active_user(update, client)
    if user is None:
        return
    logger.info("command_received", command="(unknown)", chat_id=user["chat_id"])
    await _reply(update, "❓ 알 수 없는 명령어입니다. /help 를 참조하세요.")
