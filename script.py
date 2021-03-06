#!/usr/bin/env python3

import asyncio
import logging
from random import randint

import aiohttp
from asyncpraw import Reddit
from asyncpraw.models import Comment
from asyncprawcore.exceptions import ServerError
from dynaconf import Dynaconf

from discord_logging import DiscordWebhookHandler

config = Dynaconf(settings_files=["settings.toml", ".secrets.toml"])

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(logging.Formatter(fmt="[{levelname}] {message}", style="{"))
stream_handler.setLevel(logging.DEBUG)
logger.addHandler(stream_handler)

if config.webhook:
    discord_handler = DiscordWebhookHandler(config.webhook, min_emit_interval=0.0)
    discord_handler.setFormatter(
        logging.Formatter(
            fmt="[{levelname} | {asctime}] {message}",
            datefmt="%Y-%m-%d %H:%M:%S %Z",
            style="{",
        )
    )
    discord_handler.setLevel(logging.INFO)
    logger.addHandler(discord_handler)

url = f"https://commentanalyzer.googleapis.com/v1alpha1/comments:analyze?key={config.credentials.perspective.api_key}"
params = {
    "languages": ["en"],
    "requestedAttributes": {
        "TOXICITY": {},
        "SEVERE_TOXICITY": {},
        "IDENTITY_ATTACK": {},
        "INSULT": {},
        "THREAT": {},
    },
    "communityId": f"reddit.com/r/{config.subreddit}",
}


async def authenticate_reddit(username: str) -> Reddit:
    reddit_instance = Reddit(
        username=username,
        user_agent=f"web:mod.{config.subreddit}.{username}.Perspective:v{config.version} by {config.author})",
        **config.credentials[username],
    )
    logger.info(f"Authenticated as {await reddit_instance.user.me()}!")

    return reddit_instance


async def main():
    mod_reddit = await authenticate_reddit(config.mod_username)
    stream_reddit = await authenticate_reddit(config.stream_username)
    subreddit = await stream_reddit.subreddit(config.subreddit)

    while True:
        try:
            async for comment in subreddit.stream.comments(skip_existing=False):
                await process_comment(comment, mod_reddit)

        except ServerError as e:
            sleep_duration = randint(25, 35)
            logger.warning(f"Server error, retrying in {sleep_duration}s", exc_info=e)
            await asyncio.sleep(sleep_duration)

        except Exception as e:
            logger.error(f"Encountered exception:", exc_info=e)
            raise e


async def process_comment(comment: Comment, mod_reddit: Reddit) -> None:
    results = await evaluate_comment(comment)

    cleaned_permalink = comment.permalink.replace(comment.permalink.split("/")[5], "_")
    log_content = (
        f"New comment {comment.id} by {comment.author}\n"
        f"https://www.reddit.com{cleaned_permalink}\n"
        f"{comment.body[:1500]}\n\n"
    )
    log_func = logger.debug

    for attribute, score in results.items():
        log_content += f"{attribute:16s}: {score:6.2%}\n"
        if score >= config.threshold[attribute]:
            log_func = logger.info

            # handoff to mod account to enable free-form report
            comment = await mod_reddit.comment(comment.id, lazy=True)
            await comment.report(
                f"{attribute}: {score:.2%} | threshold: {config.threshold[attribute]:.2%}"
            )

    log_func(log_content)


async def evaluate_comment(comment: Comment) -> dict[str, float]:
    params["comment"] = {"text": comment.body}

    async with aiohttp.ClientSession() as session:
        # sleep to avoid hitting rate limit
        await asyncio.sleep(1)
        async with session.post(url, json=params) as response:
            response_dict = await response.json()

            return {
                attribute.lower(): val["summaryScore"]["value"]
                for attribute, val in response_dict["attributeScores"].items()
            }


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
