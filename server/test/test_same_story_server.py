import json
import os
from uuid import uuid4

import boto3

sns_client = boto3.client("sns")
s3_client = boto3.client("sns")

TOPIC_ARN = os.environ["TOPIC_ARN"]
BUCKET_NAME = os.environ.get("BUCKET_NAME", "same-story")


def upload(key_name, body):
    return s3_client.put_object(Body=body, Bucket=BUCKET_NAME, Key=key_name)


def download(key_name):
    r = s3_client.get_object(Bucket=BUCKET_NAME, Key=key_name)
    return r["Body"].read()


def should_be_able_to_successfully_run_check():
    check_id = str(uuid4())
    spec = {
        "check": check_id,
        "width": "800",
        "height": "600",
        "component": "Button",
        "story": "Primary",
        "repository": "engi-network/engi-ui",
        "branch": "master",  # optional
        "commit": "2f513f8411b438f140ddef716ea92d479bc76f81",  # optional
    }
    # copy checks/testing-source/frames/Button-Primary.png and checks/testing-source/specification.json
    # !aws s3 cp --recursive --exclude "report/*" s3://same-story/checks/{test_check_id}/ s3://same-story/checks/{check_id}
    sns_client.publish(
        TopicArn=TOPIC_ARN,
        Message=json.dumps({"check_id": check_id}),
    )


def should_error_on_repo_problem():
    pass


def should_error_on_branch_problem():
    pass


def should_error_on_commit_problem():
    pass


def should_error_on_missing_frame():
    pass
