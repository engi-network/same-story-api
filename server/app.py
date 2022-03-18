import asyncio
import json
import os
from shlex import quote
from time import perf_counter

from aiobotocore.session import get_session
from dotenv import load_dotenv

from helpful_scripts import setup_logging

load_dotenv()

QUEUE_URL = os.environ["QUEUE_URL"]
MAX_QUEUE_MESSAGES = int(os.environ.get("MAX_QUEUE_MESSAGES", 10))
WAIT_TIME = int(os.environ.get("WAIT_TIME", 5))
CHECK_CMD = os.environ.get("CHECK", "sh check.sh {check_id}")

log = setup_logging()


async def run(cmd):
    t1_start = perf_counter()
    log.info(f"running {cmd!r}")
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()
    t1_stop = perf_counter()

    if stdout:
        log.info(f"[stdout]\n{stdout.decode()}")
    if stderr:
        log.info(f"[stderr]\n{stderr.decode()}")
    log.info(f"{cmd!r} exited with {proc.returncode} elapsed {t1_stop - t1_start} seconds")
    return proc.returncode


async def worker(queue):
    while True:
        # get a "work item" out of the queue
        client, check_id, receipt_handle = await queue.get()
        # run the shell script to do run storybook and capture the screenshots with diffs
        returncode = await run(CHECK_CMD.format(check_id=check_id))
        if returncode == 0:
            # remove the message from the SQS queue
            r = await client.delete_message(
                QueueUrl=QUEUE_URL,
                ReceiptHandle=receipt_handle,
            )
            log.info(f"deleting {receipt_handle=} {r=}")
        queue.task_done()


async def poll_queue():
    session = get_session()
    # create a queue that we will use to store our "workload"
    queue = asyncio.Queue()

    tasks = []
    # create three worker tasks to process the queue concurrently
    for i in range(3):
        task = asyncio.create_task(worker(queue))
        tasks.append(task)

    async with session.create_client("sqs") as client:
        while True:
            try:
                log.info("receiving messages")
                # grab a message from the SQS queue
                r = await client.receive_message(
                    QueueUrl=QUEUE_URL,
                    WaitTimeSeconds=WAIT_TIME,
                    MaxNumberOfMessages=MAX_QUEUE_MESSAGES,
                )
                # queue up the asyncio queue for the workers to process
                for m in r.get("Messages", []):
                    msg = json.loads(m["Body"])
                    payload = json.loads(msg["Message"])
                    log.info(f"got payload= {payload=}")
                    # quote the check_id to plug the shell injection security hole
                    check_id = quote(str(payload["check_id"]))
                    receipt_handle = m["ReceiptHandle"]
                    queue.put_nowait((client, check_id, receipt_handle))

            except KeyboardInterrupt:
                break

    # Cancel our worker tasks.
    for task in tasks:
        task.cancel()
    # Wait until all worker tasks are cancelled.
    await asyncio.gather(*tasks, return_exceptions=True)

    log.info("done")


def main():
    loop = asyncio.get_event_loop()
    loop.run_until_complete(poll_queue())


if __name__ == "__main__":
    main()
