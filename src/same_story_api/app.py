import asyncio
import json
import logging
import os

from aiobotocore.session import get_session
from dotenv import load_dotenv

from check import CheckRequest
from helpful_scripts import setup_logging

load_dotenv()

QUEUE_URL = os.environ["QUEUE_URL"]
# if storycap wouldn't mind us running multiple jobs concurrently, we could up this
MAX_QUEUE_MESSAGES = int(os.environ.get("MAX_QUEUE_MESSAGES", 1))
WAIT_TIME = int(os.environ.get("WAIT_TIME", 5))

DEFAULT_STATUS_TOPIC_ARN = os.environ.get("DEFAULT_STATUS_TOPIC_ARN")

debug = os.environ.get("DEBUG", False)
log_level = logging.DEBUG if debug else logging.INFO
log = setup_logging(log_level)


async def status_callback(sns, spec_d, msg):
    topic_arn = spec_d.get("sns_topic_arn", DEFAULT_STATUS_TOPIC_ARN)
    if topic_arn is None:
        return
    log.info(f"sending status update to {topic_arn=} {msg=}")
    await sns.publish(
        TopicArn=topic_arn,
        Message=json.dumps(msg),
    )


async def worker(n, queue):
    while True:
        # dequeue a "work item"
        sqs, sns, spec_d, receipt_handle = await queue.get()
        log.info(f"worker {n} got {spec_d=}")
        try:
            await CheckRequest(spec_d, lambda msg: status_callback(sns, spec_d, msg)).run()
        except Exception as e:
            log.exception(e)
        # remove the message from the SQS queue
        r = await sqs.delete_message(
            QueueUrl=QUEUE_URL,
            ReceiptHandle=receipt_handle,
        )
        log.info(f"worker {n} deleting {receipt_handle=} {r=}")
        queue.task_done()


async def poll_queue():
    session = get_session()
    # create a queue that we will use to store our "workload"
    queue = asyncio.Queue(maxsize=MAX_QUEUE_MESSAGES)

    tasks = []
    # storybook seems not to like concurrency
    for n in range(1):
        task = asyncio.create_task(worker(n, queue))
        tasks.append(task)

    async with session.create_client("sqs") as sqs, session.create_client("sns") as sns:
        while True:
            try:
                log.info("receiving messages")
                # grab a message from the SQS queue
                r = await sqs.receive_message(
                    QueueUrl=QUEUE_URL,
                    WaitTimeSeconds=WAIT_TIME,
                    MaxNumberOfMessages=MAX_QUEUE_MESSAGES,
                )
                # queue up the asyncio queue for the workers to process
                for m in r.get("Messages", []):
                    msg = json.loads(m["Body"])
                    spec_d = json.loads(msg["Message"])
                    log.debug(f"got {spec_d=}")
                    receipt_handle = m["ReceiptHandle"]
                    # if the queue is full, wait until a free slot is available
                    await queue.put((sqs, sns, spec_d, receipt_handle))

            except KeyboardInterrupt:
                break

    # cancel our worker tasks.
    for task in tasks:
        task.cancel()
    # wait until all worker tasks are cancelled.
    await asyncio.gather(*tasks, return_exceptions=True)

    log.info("done")


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(poll_queue())


if __name__ == "__main__":
    main()
