#!/usr/bin/env python3
"""
📈 포트폴리오 일일 투자 분석 텔레그램 봇
매일 아침 10시에 전체 포트폴리오 분석 및 투자 의견을 전송합니다.
"""

import os
import asyncio
import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta

import pytz
import requests
import anthropic
from telegram import Bot

# ──────────────────────────────────────────────────────
# 설정값 — Railway Variables 탭에서 환경변수로 입력하세요
# ──────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID  = os.getenv("TELEGRAM_CHAT_ID")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")

# 이메일 설정
EMAIL_FROM     = "hongjinarosa@gmail.com"
EMAIL_TO       = "hongjinarosa@gmail.com"
EMAIL_PASSWORD = "hhrswefiykrolglm"

KST = pytz.timezone("Asia/Seoul")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────
# 포트폴리오 정의
# ──────────────────────────────────────────────────────
KR_STOCKS = [
    {"name": "셀트리온",          "code": "068270", "buy_price": 207500, "currency": "KRW"},
    {"name": "KODEX 코스닥150",   "code": "229200", "buy_price": 21015,  "currency": "KRW"},
    {"name": "미래에셋증권",      "code": "006800", "buy_price": 60600,  "currency": "KRW"},
    {"name": "미래에셋벤처투자",  "code": "100790", "buy_price": 39550,  "currency": "KRW"},
]

US_STOCKS = [
    {"name": "힐튼",       "ticker": "HLT",  "buy_price": 124.79, "currency": "USD"},
    {"name": "에어비앤비", "ticker": "ABNB", "buy_price": 169.45, "currency": "USD"},
]

HEADERS = {"User-Agent": "Mozilla/5.0"}


# ──────────────────────────────────────────────────────
# 주가 데이터 수집
# ──────────────────────────────────────────────────────
def fetch_yahoo(ticker: str, range_: str = "1mo") -> dict:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {"interval": "1d", "range": range_}
    try:
        r = requests.get(url, params=params, headers=HEADERS, timeout=10)
        r.raise_for_status()
        data   = r.json()
        result = data["chart"]["result"][0]
        meta   = result["meta"]
        quotes = result["indicators"]["quote"][0]
        ts     = result["timestamp"]

        closes  = quotes.get("close",  [])
        volumes = quotes.get("volume", [])

        recent = []
        for i, t in enumerate(ts):
            c = closes[i]  if i < len(closes)  else None
            v = volumes[i] if i < len(volumes) else None
            if c is not None:
                recent.append({
                    "date":   datetime.fromtimestamp(t, tz=KST).strftime("%Y-%m-%d"),
                    "close":  c,
                    "volume": v,
                })

        current = meta.get("regularMarketPrice") or (recent[-1]["close"] if recent else None)
        prev    = meta.get("previousClose")      or (recent[-2]["close"] if len(recent) >= 2 else None)
        return {"current": current, "prev": prev, "recent": recent[-20:]}
    except Exception as e:
        logger.warning(f"[{ticker}] 데이터 오류: {e}")
        return {}


def get_all_prices() -> list:
    results = []

    for s in KR_STOCKS:
        suffix = "KQ" if s["code"] in ["072710", "100790", "229200"] else "KS"
        d = fetch_yahoo(f"{s['code']}.{suffix}")
        if d and d.get("current"):
            change     = d["current"] - d["prev"] if d.get("prev") else 0
            change_pct = change / d["prev"] * 100  if d.get("prev") else 0
            pl         = d["current"] - s["buy_price"]
            pl_pct     = pl / s["buy_price"] * 100
            results.append({**s,
                "current": d["current"], "change": change,
                "change_pct": change_pct, "pl": pl,
                "pl_pct": pl_pct, "recent": d["recent"],
            })
        else:
            results.append({**s, "current": None, "error": True})

    for s in US_STOCKS:
        d = fetch_yahoo(s["ticker"])
        if d and d.get("current"):
            change     = d["current"] - d["prev"] if d.get("prev") else 0
            change_pct = change / d["prev"] * 100  if d.get("prev") else 0
            pl         = d["current"] - s["buy_price"]
            pl_pct     = pl / s["buy_price"] * 100
            results.append({**s,
                "current": d["current"], "change": change,
                "change_pct": change_pct, "pl": pl,
                "pl_pct": pl_pct, "recent": d["recent"],
            })
        else:
            results.append({**s, "current": None, "error": True})

    return results


# ──────────────────────────────────────────────────────
# Claude AI 분석
# ──────────────────────────────────────────────────────
def get_ai_analysis(portfolio: list) -> str:
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

        lines = []
        for s in portfolio:
            if s.get("error") or not s.get("current"):
                lines.append(f"- {s['name']}: 데이터 없음")
                continue
            ccy = "원" if s["currency"] == "KRW" else "$"
            recent_str = ", ".join(
                [f"{d['close']:,.1f}" for d in s.get("recent", [])[-10:]]
            ) or "없음"
            lines.append(
                f"- {s['name']}: 현재 {ccy}{s['current']:,.2f} | "
                f"매입 {ccy}{s['buy_price']:,.2f} | "
                f"수익률 {s['pl_pct']:+.2f}% | "
                f"전일대비 {s['change_pct']:+.2f}% | "
                f"최근종가: {recent_str}"
            )

        prompt = f"""당신은 전문 투자 분석가입니다. 아래 포트폴리오 7개 종목을 분석해주세요.

포트폴리오 현황:
{chr(10).join(lines)}

각 종목에 대해 아래 형식으로 작성해주세요.
마크다운 기호(**, ##, --- 등)는 절대 사용하지 마세요. 일반 텍스트와 이모지만 사용하세요.

[종목명]
추세: (최근 흐름 1~2문장)
의견: 매도/보유/추가매수 중 하나 + 이유 1문장

마지막에 전체 포트폴리오 한줄 총평을 추가해주세요.
각 종목당 3줄 이내로 간결하게 작성해주세요.
"""
        msg = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        result = msg.content[0].text
        result = result.replace("**", "")
        result = result.replace("## ", "")
        result = result.replace("# ", "")
        result = result.replace("---", "")
        result = result.strip()
        return result
    except Exception as e:
        logger.error(f"Claude API 오류: {e}")
        return "AI 분석을 불러오는 데 실패했습니다."


# ──────────────────────────────────────────────────────
# 메시지 조립
# ──────────────────────────────────────────────────────
def fmt(val, currency):
    if val is None:
        return "N/A"
    if currency == "KRW":
        return f"{val:,.0f}원"
    return f"${val:,.2f}"


def build_message(portfolio: list, analysis: str) -> str:
    now = datetime.now(KST)
    kr_lines, us_lines = [], []
    pl_pcts = []

    for s in portfolio:
        if s.get("error") or not s.get("current"):
            line = f"- {s['name']}: 데이터 없음"
        else:
            day_e   = "🟢" if s["change_pct"] >= 0 else "🔴"
            pl_e    = "📈" if s["pl_pct"]     >= 0 else "📉"
            pl_sign = "+" if s["pl"] >= 0 else "-"
            line = (
                f"{day_e} {s['name']}\n"
                f"  현재가 {fmt(s['current'], s['currency'])} ({s['change_pct']:+.2f}%)\n"
                f"  {pl_e} 수익률 {s['pl_pct']:+.2f}% ({pl_sign}{fmt(abs(s['pl']), s['currency'])})"
            )
            pl_pcts.append(s["pl_pct"])

        if s["currency"] == "KRW":
            kr_lines.append(line)
        else:
            us_lines.append(line)

    avg_pl     = sum(pl_pcts) / len(pl_pcts) if pl_pcts else 0
    port_emoji = "📈" if avg_pl >= 0 else "📉"

    return (
        f"🌅 {now.strftime('%Y년 %m월 %d일 %H:%M')} 포트폴리오 브리핑\n\n"
        f"🇰🇷 국내 주식\n\n"
        + "\n\n".join(kr_lines)
        + f"\n\n🇺🇸 해외 주식\n\n"
        + "\n\n".join(us_lines)
        + f"\n\n{port_emoji} 포트폴리오 평균 수익률: {avg_pl:+.2f}%\n\n"
        f"🤖 AI 투자 의견\n\n{analysis}\n\n"
        f"⚠️ 본 내용은 투자 참고용이며, 투자 결과의 책임은 본인에게 있습니다."
    )


# ──────────────────────────────────────────────────────
# 전송 & 스케줄러
# ──────────────────────────────────────────────────────
async def send_daily_report():
    logger.info("📊 일일 리포트 생성 시작...")
    portfolio = get_all_prices()

    if not any(s.get("current") for s in portfolio):
        bot = Bot(token=TELEGRAM_TOKEN)
        await bot.send_message(
            chat_id=TELEGRAM_CHAT_ID,
            text="주가 데이터를 불러오지 못했습니다. 장 휴장일이거나 네트워크 오류입니다.",
        )
        return

    analysis = get_ai_analysis(portfolio)
    message  = build_message(portfolio, analysis)

    # 텔레그램 전송
    bot = Bot(token=TELEGRAM_TOKEN)
    await bot.send_message(
        chat_id=TELEGRAM_CHAT_ID,
        text=message,
    )
    logger.info("✅ 텔레그램 전송 완료!")

    # 이메일 전송
    try:
        now = datetime.now(pytz.timezone("Asia/Seoul"))
        subject = f"📈 {now.strftime("%m월 %d일")} 포트폴리오 브리핑"
        msg = MIMEMultipart()
        msg["From"]    = EMAIL_FROM
        msg["To"]      = EMAIL_TO
        msg["Subject"] = subject
        msg.attach(MIMEText(message, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_FROM, EMAIL_PASSWORD)
            server.sendmail(EMAIL_FROM, EMAIL_TO, msg.as_string())
        logger.info("✅ 이메일 전송 완료!")
    except Exception as e:
        logger.error(f"이메일 전송 오류: {e}")


async def scheduler():
    logger.info("🤖 스케줄러 시작 - 매일 오전 10:00 KST")
    while True:
        now    = datetime.now(KST)
        target = now.replace(hour=10, minute=0, second=0, microsecond=0)
        if now >= target:
            target += timedelta(days=1)

        wait = (target - now).total_seconds()
        logger.info(f"⏳ 다음 전송까지 {wait / 3600:.1f}시간 대기...")
        await asyncio.sleep(wait)

        if target.weekday() < 5:
            await send_daily_report()
        else:
            day_names = ["월","화","수","목","금","토","일"]
            logger.info(f"📅 {day_names[target.weekday()]}요일 - 주말 스킵")


# ──────────────────────────────────────────────────────
# 진입점
# ──────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--test":
        logger.info("🧪 테스트 모드: 즉시 전송")
        asyncio.run(send_daily_report())
    else:
        asyncio.run(scheduler())
