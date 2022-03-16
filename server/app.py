import asyncio
import json
import os

from aiobotocore.session import get_session
from dotenv import load_dotenv

from helpful_scripts import setup_logging

load_dotenv()

QUEUE_URL = os.environ["QUEUE_URL"]
MAX_QUEUE_MESSAGES = int(os.environ.get("MAX_QUEUE_MESSAGES", 10))

log = setup_logging()


async def poll_queue():
    session = get_session()
    async with session.create_client("sqs") as client:
        while True:
            try:
                log.info("receiving messages")
                r = await client.receive_message(
                    QueueUrl=QUEUE_URL,
                    WaitTimeSeconds=2,
                    MaxNumberOfMessages=MAX_QUEUE_MESSAGES,
                )
                for m in r.get("Messages", []):
                    msg = json.loads(m["Body"])
                    payload = json.loads(msg["Message"])
                    log.info(f"got {payload=}")
                    receipt_handle = m["ReceiptHandle"]
                    r = await client.delete_message(
                        QueueUrl=QUEUE_URL,
                        ReceiptHandle=receipt_handle,
                    )
                    log.info(f"deleting {receipt_handle=} {r=}")

            except KeyboardInterrupt:
                break

    log.info("done")


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(poll_queue())


if __name__ == "__main__":
    main()
