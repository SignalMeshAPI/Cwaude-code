"""Reddit social sentiment via PRAW + VADER. Soft signal; polled every 5min.

PRAW requires a client_id/secret to authenticate even for read-only access.
If credentials are absent, the source no-ops and the signal stays None.
"""

from __future__ import annotations

import asyncio
import time

from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)

EMA_ALPHA = 0.3


async def run_social(
    state: MarketState,
    *,
    client_id: str,
    client_secret: str,
    user_agent: str,
    subreddits: list[str],
    interval: float = 300.0,
    posts_per_sub: int = 20,
) -> None:
    if not client_id or not client_secret:
        log.info("social.disabled", reason="missing_reddit_credentials")
        return

    # Lazy imports so the bot still runs without these libs available.
    try:
        import praw
        from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer
    except ImportError as exc:
        log.warning("social.disabled", reason="missing_lib", error=str(exc))
        return

    analyzer = SentimentIntensityAnalyzer()
    reddit = praw.Reddit(
        client_id=client_id,
        client_secret=client_secret,
        user_agent=user_agent,
        check_for_async=False,
    )
    reddit.read_only = True

    while True:
        try:
            scores: list[float] = []
            for sub_name in subreddits:
                # praw is sync; offload to thread to keep loop responsive
                posts = await asyncio.to_thread(
                    lambda s=sub_name: list(reddit.subreddit(s).new(limit=posts_per_sub))
                )
                for post in posts:
                    text = f"{post.title}\n{post.selftext or ''}".strip()
                    if not text:
                        continue
                    scores.append(analyzer.polarity_scores(text)["compound"])

            if scores:
                avg = sum(scores) / len(scores)
                prev = state.social_sentiment
                state.social_sentiment = avg if prev is None else (
                    EMA_ALPHA * avg + (1 - EMA_ALPHA) * prev
                )
                state.social_sentiment_ts = time.time()
        except Exception as exc:  # noqa: BLE001
            metrics.API_ERRORS.labels(source="reddit").inc()
            log.warning("social.error", error=str(exc))
        await asyncio.sleep(interval)
