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
                    # quote the check_id to plug the shell injection security hole
                    returncode = await run(
                        CHECK_CMD.format(check_id=quote(str(payload["check_id"])))
                    )
                    if returncode == 0:
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
