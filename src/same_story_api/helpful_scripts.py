import json
import logging
import os
import shutil
import socket
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import boto3
import coloredlogs


def setup_logging(log_level=logging.INFO):
    logger = logging.getLogger()

    # Set log format to dislay the logger name to hunt down verbose logging modules
    fmt = "%(asctime)s %(name)-25s %(levelname)-8s %(message)s"

    coloredlogs.install(level=log_level, fmt=fmt, logger=logger)

    return logger


log = setup_logging()

sns_client = boto3.client("sns")
sqs_client = boto3.client("sqs")
s3_client = boto3.client("s3")
sts_client = boto3.client("sts")

AWS_REGION = boto3.session.Session().region_name
AWS_ACCOUNT = sts_client.get_caller_identity()["Account"]


def get_name(env=None):
    if env is None:
        env = os.environ.get("ENV", "dev")
    return f"same-story-api-{env}"


def get_sns_arn(name):
    return f"arn:aws:sns:{AWS_REGION}:{AWS_ACCOUNT}:{name}"


def get_sqs_url(name):
    return f"{sqs_client.meta._endpoint_url}/{AWS_ACCOUNT}/{name}"


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


def allow_all_to_publish_to_sns(topic_arn):
    return """{{
        "Id": "Policy1654105353800",
        "Version": "2012-10-17",
        "Statement": [
            {{
            "Sid": "Stmt1654105351953",
            "Action": [
                "sns:Publish"
            ],
            "Effect": "Allow",
            "Resource": "{}",
            "Principal": "*"
            }}
        ]
        }}""".format(
        topic_arn
    )


def allow_sns_to_write_to_sqs(topic_arn, queue_arn):
    return """{{
        "Version":"2012-10-17",
        "Statement":[
            {{
            "Sid": "MyPolicy",
            "Effect": "Allow",
            "Principal": {{"AWS" : "*"}},
            "Action": "SQS:SendMessage",
            "Resource": "{}",
            "Condition":{{
                "ArnEquals":{{
                "aws:SourceArn": "{}"
                }}
            }}
            }}
        ]
        }}""".format(
        queue_arn, topic_arn
    )


def get_topic_arn(queue_url):
    return get_sns_arn(queue_url.split("/")[-1])


class NullFanout(object):
    def __init__(self, *_, **__):
        self.topic_arn = None

    def __enter__(self):
        return self

    def receive(self, **_):
        yield

    def __exit__(self, *_):
        pass


class SNSFanoutSQS(object):
    """Create a SNS topic and connect it to an SQS queue. Use contextlib to
    optionally tear down both the topic and queue after exiting a with
    statement"""

    def __init__(self, name, visibility_timeout=180, persist=False, fifo=True):
        self.sns = sns_client
        self.sqs = sqs_client
        self.name = f"{name}.fifo" if fifo else name
        self.visibility_timeout = visibility_timeout
        self.persist = persist
        self.fifo = fifo
        self.created = False

    @staticmethod
    def load(queue_url):
        self = SNSFanoutSQS("")
        self.queue_url = queue_url
        self.topic_arn = get_topic_arn(queue_url)
        return self

    @staticmethod
    def cleanup_old(suffix, age_cutoff=60 * 60):
        now = datetime.utcnow()
        log.info("cleaning up old SQS and SNS queues")
        r = sqs_client.list_queues(QueueNamePrefix=get_name())
        for queue_url in r["QueueUrls"]:
            if not suffix in queue_url:
                continue
            try:
                r_ = sqs_client.get_queue_attributes(
                    QueueUrl=queue_url, AttributeNames=["LastModifiedTimestamp"]
                )
                age = (
                    now - datetime.utcfromtimestamp(int(r_["Attributes"]["LastModifiedTimestamp"]))
                ).seconds
                log.info(f"{queue_url} is {age} seconds old, {age_cutoff=}")
                if age >= age_cutoff:
                    SNSFanoutSQS.load(queue_url).cleanup()
            except sqs_client.exceptions.QueueDoesNotExist:
                continue

    def topic_exists(self):
        return getattr(self, "topic_arn", get_sns_arn(self.name)) in [
            t["TopicArn"] for t in self.sns.list_topics()["Topics"]
        ]

    def create(self):
        if self.topic_exists():
            log.info(f"topic {self.name} exists")
            self.topic_arn = get_sns_arn(self.name)
            self.queue_url = get_sqs_url(self.name)
            return self
        log.info(f"creating {self.name=}")
        # create the SQS queue
        attrs = {"VisibilityTimeout": str(self.visibility_timeout)}
        if self.fifo:
            attrs["FifoQueue"] = "true"
        r = self.sqs.create_queue(
            QueueName=self.name,
            Attributes=attrs,
        )
        self.queue_url = r["QueueUrl"]
        r = self.sqs.get_queue_attributes(QueueUrl=self.queue_url, AttributeNames=["QueueArn"])
        self.queue_arn = r["Attributes"]["QueueArn"]
        log.info(f"{self.queue_url=} {self.queue_arn=}")
        log.info(f"creating {self.name=}")
        # create the SNS topic
        r = self.sns.create_topic(
            Name=self.name,
            Attributes={"FifoTopic": "true", "ContentBasedDeduplication": "true"}
            if self.fifo
            else {},
        )
        self.topic_arn = r["TopicArn"]
        log.info(f"{self.topic_arn=}")
        # subscribe the topic to the queue, aka fanout
        r = self.sns.subscribe(
            TopicArn=self.topic_arn,
            Protocol="sqs",
            Endpoint=self.queue_arn,
            ReturnSubscriptionArn=True,
        )
        # permissions
        r = self.sns.set_topic_attributes(
            TopicArn=self.topic_arn,
            AttributeName="Policy",
            AttributeValue=allow_all_to_publish_to_sns(self.topic_arn),
        )
        r = self.sqs.set_queue_attributes(
            QueueUrl=self.queue_url,
            Attributes={"Policy": allow_sns_to_write_to_sqs(self.topic_arn, self.queue_arn)},
        )
        self.created = True
        return self

    def last_modified(self):
        r = self.sqs.get_queue_attributes(
            QueueUrl=self.queue_url, AttributeNames=["LastModifiedTimestamp"]
        )
        return datetime.utcfromtimestamp(int(r["Attributes"]["LastModifiedTimestamp"]))

    def __enter__(self):
        return self.create()

    def publish(self, d, **kwargs):
        return self.sns.publish(TopicArn=self.topic_arn, Message=json.dumps(d), **kwargs)

    def receive(self, wait_time=5):
        r = self.sqs.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=wait_time,
        )
        for msg in r.get("Messages", []):
            yield json.loads(json.loads(msg["Body"])["Message"])
            receipt_handle = msg["ReceiptHandle"]
            # log.info(f"{receipt_handle=}")
            self.sqs.delete_message(
                QueueUrl=self.queue_url,
                ReceiptHandle=receipt_handle,
            )

    def cleanup(self):
        if self.topic_exists():
            log.info(f"deleting {self.queue_url=} {self.topic_arn=}")
            self.sqs.delete_queue(QueueUrl=self.queue_url)
            self.sns.delete_topic(TopicArn=self.topic_arn)
            return True
        return False

    def __exit__(self, *_):
        if not self.persist:
            self.cleanup()


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
