import json
import os
import time
from pathlib import Path
from uuid import uuid4

import boto3
import pytest

sns_client = boto3.client("sns")
s3_client = boto3.client("s3")

TOPIC_ARN = os.environ["TOPIC_ARN"]
BUCKET_NAME = os.environ.get("BUCKET_NAME", "same-story")


def upload(key_name, body):
    return s3_client.put_object(Body=body, Bucket=BUCKET_NAME, Key=key_name)


def upload_file(local, remote):
    s3_client.upload_file(local, BUCKET_NAME, str(remote))


def download(key_name):
    r = s3_client.get_object(Bucket=BUCKET_NAME, Key=key_name)
    return r["Body"].read()


def delete(key_name):
    r = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=key_name)
    for obj in r["Contents"]:
        s3_client.delete_object(Bucket=BUCKET_NAME, Key=obj["Key"])


def exists(key_name):
    r = s3_client.list_objects_v2(Bucket=BUCKET_NAME, Prefix=key_name)
    return "Contents" in r


@pytest.fixture
def results():
    check_id = str(uuid4())
    print(f"{check_id=}")
    spec_d = {
        "check": check_id,
        "width": "800",
        "height": "600",
        "component": "Button",
        "story": "Primary",
        "repository": "engi-network/engi-ui",
        "branch": "master",  # optional
        "commit": "2f513f8411b438f140ddef716ea92d479bc76f81",  # optional
    }
    prefix = f"checks/{check_id}"
    spec = f"{prefix}/specification.json"
    results = f"{prefix}/report/results.json"
    error = f"{prefix}/report/error.json"
    button = Path(f"{prefix}/frames/Button-Primary.png")

    # upload specification.json
    upload(spec, json.dumps(spec_d))
    # upload the button image (this is the check frame from Figma)
    upload_file(f"server/test/data/{button.name}", button)
    # publish the job
    sns_client.publish(
        TopicArn=TOPIC_ARN,
        Message=json.dumps({"check_id": check_id}),
    )

    # a crude loop to poll for the results
    count = 16
    results_d = {"spec": spec_d}
    while True:
        time.sleep(10)
        print(f"looking for {results=}")
        if exists(results):
            results_d["results"] = json.loads(download(results))
            break
        if exists(error):
            results_d["error"] = json.loads(download(error))
            break

        count -= 1
        assert count != 0

    return results_d


def test_should_be_able_to_successfully_run_check(results):
    print(f"{results=}")

    assert "results" in results
    assert not "error" in results

    check_id = results["spec"]["check"]
    prefix = f"checks/{check_id}"
    gray_difference = f"{prefix}/report/gray_difference.png"
    blue_difference = gray_difference.replace("gray", "blue")
    primary = f"{prefix}/report/__screenshots__/Example/Button/Primary.png"

    # check for the screenshot captured by storycap
    assert exists(primary)
    # check for the output comparison images in S3
    assert exists(gray_difference)
    assert exists(blue_difference)

    # check for the objective visual difference between the check frame and the
    # screenshot captured by storycap
    mae = float(results["results"]["MAE"].split()[0])
    assert mae < 5.0

    # clean up the directory in S3
    delete(prefix)


def test_should_error_on_repo_problem():
    pass


def test_should_error_on_branch_problem():
    pass


def test_should_error_on_commit_problem():
    pass


def test_should_error_on_missing_frame():
    pass
