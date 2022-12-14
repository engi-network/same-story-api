import json
import logging
import os
import shutil
import socket
import time
from contextlib import contextmanager
from pathlib import Path

import boto3
import coloredlogs
from engi_message_queue import (
    NullFanout,
    SNSFanoutSQS,
    get_name,
    get_sns_arn,
    get_sqs_url,
)

sns_client = boto3.client("sns")


def setup_logging(name="same_story_api", log_level=logging.INFO):
    logger = logging.getLogger(name)

    # set log format to display the logger name to hunt down verbose logging modules
    fmt = "%(asctime)s %(name)s %(levelname)s %(message)s"

    coloredlogs.install(level=log_level, fmt=fmt, logger=logger)

    return logger


log = setup_logging()

s3_client = boto3.client("s3")

AWS_REGION = boto3.session.Session().region_name


def setup_env(env=None):
    """env is one of dev, staging, production"""
    name = get_name(env)
    sns_topic = get_sns_arn(name)
    # a place to submit job requests
    os.environ["TOPIC_ARN"] = sns_topic
    log.info(f"{os.environ['TOPIC_ARN']=}")
    # the S3 bucket to store the input image and output screenshots and results
    os.environ["BUCKET_NAME"] = name
    log.info(f"{os.environ['BUCKET_NAME']=}")
    # a place for the backend server to dequeue job requests
    os.environ["QUEUE_URL"] = get_sqs_url(name)
    log.info(f"{os.environ['QUEUE_URL']=}")


@contextmanager
def set_directory(path):
    origin = Path().absolute()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(origin)


@contextmanager
def cleanup_directory(path):
    try:
        yield
    finally:
        if path.exists():
            shutil.rmtree(path)


def get_port():
    sock = socket.socket()
    sock.bind(("", 0))
    return sock.getsockname()[1]


def get_s3_url(suffix):
    return f"{s3_client.meta.endpoint_url}/{suffix}"


def make_s3_public(suffix):
    bits = suffix.split("/")
    bucket, key = bits[0], "/".join(bits[1:])
    return boto3.resource("s3").ObjectAcl(bucket, key).put(ACL="public-read")


class Client(object):
    SPEC_KEYS = [
        "width",
        "height",
        "path",
        "component",
        "story",
        "repository",
        "branch",  # optional
        "commit",  # optional
    ]

    def __init__(self):
        self.bucket_name = os.environ["BUCKET_NAME"]
        self.topic_arn = os.environ["TOPIC_ARN"]

    def upload(self, key_name, body):
        return s3_client.put_object(Body=body, Bucket=self.bucket_name, Key=key_name)

    def upload_file(self, local, remote):
        s3_client.upload_file(local, self.bucket_name, str(remote))

    def upload_frame(self, path):
        story = self.spec_d["story"]
        frame = f"{story}.png"
        button = Path(f"{self.prefix}/frames/{frame}")
        # upload the button image (this is the check frame from Figma)
        self.upload_file(str(path / button.name), button)

    def get_path(self, error=False):
        suffix = "error" if error else "results"
        return f"{self.prefix}/report/{suffix}.json"

    def download(self, key_name):
        r = s3_client.get_object(Bucket=self.bucket_name, Key=key_name)
        return r["Body"].read()

    def delete(self, key_name):
        r = s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=key_name)
        for obj in r["Contents"]:
            s3_client.delete_object(Bucket=self.bucket_name, Key=obj["Key"])

    def exists(self, key_name):
        r = s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=key_name)
        return "Contents" in r

    def get_results(self, spec_d, path, upload=True, callback=None, no_status=False):
        self.spec_d = spec_d
        check_id = spec_d["check_id"]
        self.prefix = f"checks/{check_id}"
        results = self.get_path()
        error = self.get_path(error=True)
        if callback is None:
            callback = lambda _: None

        fanout_class = NullFanout if no_status else SNSFanoutSQS

        # create a temporary SNS -> SQS fanout to receive status updates
        with fanout_class(f"{check_id}-same-story-test-queue") as fanout:
            if fanout.topic_arn:
                # let the backend server know where we'd like to receive status updates
                spec_d["sns_topic_arn"] = fanout.topic_arn

            if upload:
                self.upload_frame(path)

            # publish the job
            sns_client.publish(
                TopicArn=self.topic_arn,
                Message=json.dumps(spec_d),
            )

            results_d = {"spec": spec_d, "status": []}
            done = False
            count = 100
            # receive status updates, break when done, error or timeout
            while not done and count:
                log.info(f"{count=}")
                for msg in fanout.receive():
                    if msg is None:
                        # poll for it
                        if self.exists(error) or self.exists(results):
                            done = True
                        time.sleep(30)
                    else:
                        log.info(f"received {msg=}")
                        callback(msg)
                        results_d["status"].append(msg)
                        if msg["step"] == msg["step_count"] - 1 or "error" in msg:
                            done = True
                count -= 1
            if done:
                time.sleep(1)
                results_d["results"] = json.loads(self.download(results))

        log.info(f"got {results_d=}")
        return results_d
