import logging
from logging import Handler, LogRecord
from time import monotonic, sleep
from typing import Optional

import requests


class DiscordWebhookHandler(Handler):
    def __init__(self, webhook_url: str, min_emit_interval: float = 1.0):
        """Initialize a logging handler that posts to a Discord webhook.

        :param webhook_url: URL of the webhook.
        :param min_emit_interval: The minimum interval between emits in seconds.
        """

        super().__init__()
        self.url = webhook_url

        self.interval = min_emit_interval
        self.last_emit: float = 0

        self.queue: list[logging.LogRecord] = []

    def post_webhook(self, content: str, timeout: float = 5.0) -> bool:
        """
        Post content to the webhook, retrying on rate limit errors for a maximum
        of `timeout` seconds.

        :param content: Content to be posted to the webhook.
        :param timeout: Time in seconds after which the operation should be aborted.
        :return: Whether the post request was successful.
        """

        response = requests.post(self.url, data=dict(content=content[:2000]))
        start_time = monotonic()

        while not response.ok:
            sleep_duration = float(response.headers.get("x-ratelimit-reset-after", 2))

            # return if timeout would be exceeded after sleep
            if monotonic() - start_time + sleep_duration > timeout:
                return False

            sleep(sleep_duration)
            response = requests.post(self.url, data=dict(content=content[:2000]))

        return True

    def emit(self, record: Optional[LogRecord]) -> None:
        """
        Emits a log record. The log record can be `None`, in which case the queue will
        be flushed.

        :param record: Log record to emit or `None`.
        """

        now = monotonic()

        if record is not None and self.last_emit + self.interval > now:
            self.queue.append(record)
            return

        queue_content = "\n".join(self.format(queued_record) for queued_record in self.queue)
        record_content = self.format(record) if record is not None else ""
        success = self.post_webhook(f"```{queue_content}\n{record_content}```")

        self.last_emit = now
        if success:
            self.queue = []
        else:
            self.queue.append(record)

    def flush(self) -> None:
        if self.queue:
            # flush the queue
            self.emit(None)
