import asyncio
import json
import logging
import os

from aiobotocore.session import get_session
from dotenv import load_dotenv

load_dotenv()

from same_story_api.helpful_scripts import (
    SNSFanoutSQS,
    get_name,
    last_step,
    setup_env,
    setup_logging,
)
from same_story_api.tasks import fanout_cleanup

setup_env()

from check import CheckRequest

QUEUE_URL = os.environ["QUEUE_URL"]
# if storycap wouldn't mind us running multiple jobs concurrently, we could up this
MAX_QUEUE_MESSAGES = int(os.environ.get("MAX_QUEUE_MESSAGES", 1))
# how long in seconds to wait when receiving messages from the main SQS job queue
WAIT_TIME = int(os.environ.get("WAIT_TIME", 5))
# visibility timeout for status messages
STATUS_VISIBILITY_TIMEOUT = int(os.environ.get("STATUS_VISIBILITY_TIMEOUT", 5))
# how long in seconds to wait before cleaning up the SNS -> SQS status message fanout
STATUS_CLEANUP_TIME = int(os.environ.get("STATUS_CLEANUP_TIME", 60 * 60))

debug = os.environ.get("DEBUG", False)
log_level = logging.DEBUG if debug else logging.INFO
log = setup_logging(log_level)


def get_sns_topic(spec_d, msg):
    """Get the SNS topic for status updates. If an ARN is given in spec_d then
    use it. Otherwise, create a temporary SQS -> SNS fanout and schedule its
    destruction"""
    topic_arn = spec_d.get("sns_topic_arn")
    if topic_arn is not None:
        return topic_arn
    check_id = spec_d["check_id"]
    name = f"{get_name()}-{check_id}-status"
    fanout = SNSFanoutSQS(
        name, name, persist=True, visibility_timeout=STATUS_VISIBILITY_TIMEOUT
    ).create()
    if last_step(msg):
        fanout_cleanup.apply_async(
            (fanout.topic_arn, fanout.queue_url, STATUS_CLEANUP_TIME),
            countdown=STATUS_CLEANUP_TIME,
        )
    return fanout.topic_arn


async def status_callback(sns, spec_d, msg):
    topic_arn = get_sns_topic(spec_d, msg)
    if topic_arn is None:
        return
    log.info(f"sending status update to {topic_arn=} {msg=}")
    kwargs = {
        "TopicArn": topic_arn,
        "Message": json.dumps(msg),
    }
    if topic_arn.endswith("fifo"):
        # FIFO (first-in-first-out) topics require additional params for deduplication
        kwargs.update(
            {"MessageGroupId": msg["check_id"], "MessageDeduplicationId": str(msg["step"])}
        )
    await sns.publish(**kwargs)


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
