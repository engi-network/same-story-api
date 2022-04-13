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


def upload_frame(prefix, spec_d):
    story = spec_d["story"]
    frame = f"{story}.png"
    button = Path(f"{prefix}/frames/{frame}")
    # upload the button image (this is the check frame from Figma)
    upload_file(f"server/test/data/{button.name}", button)


def get_results(spec_d, upload=True):
    prefix = f"checks/{spec_d['check_id']}"
    results = f"{prefix}/report/results.json"

    if upload:
        upload_frame(prefix, spec_d)

    # publish the job
    sns_client.publish(
        TopicArn=TOPIC_ARN,
        Message=json.dumps(spec_d),
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

        count -= 1
        assert count != 0

    print(f"got {results_d=}")
    return results_d


@pytest.fixture
def success_spec():
    return {
        "check_id": str(uuid4()),
        "width": "800",
        "height": "600",
        "path": "Global/Components",
        "component": "Button",
        "story": "Button With Knobs",
        "repository": "engi-network/figma-plugin",
        "branch": "main",  # optional
        "commit": "b7843ac1a0b66da9b84a516d9970749d5e8a8b5a",  # optional
    }


@pytest.fixture
def success_results(success_spec):
    yield get_results(success_spec)
    cleanup(success_spec)


@pytest.fixture
def error_results_repo(success_spec):
    success_spec["repository"] = "nonsense"
    yield get_results(success_spec)
    cleanup(success_spec)


@pytest.fixture
def error_results_commit(success_spec):
    success_spec["commit"] = "nonsense"
    yield get_results(success_spec)
    cleanup(success_spec)


@pytest.fixture
def error_results_branch(success_spec):
    success_spec["branch"] = "nonsense"
    yield get_results(success_spec)
    cleanup(success_spec)


@pytest.fixture
def error_results_frame(success_spec):
    yield get_results(success_spec, upload=False)
    cleanup(success_spec)


@pytest.fixture
def success_results_no_commit_branch(success_spec):
    del success_spec["branch"]
    del success_spec["commit"]
    yield get_results(success_spec)
    cleanup(success_spec)


def check_spec_in_results(spec, results):
    # check spec got copied into results
    for key, val in spec.items():
        assert results[key] == val


def get_error(results, key):
    check_spec_in_results(results["spec"], results["results"])
    # key is the step where our job failed
    # stdout and stderr should tell us why
    assert set(results["results"]["error"].keys()) >= set([key, "stdout", "stderr"])


def cleanup(spec):
    check_id = spec["check_id"]
    print(f"cleaning up {check_id=}")
    prefix = f"checks/{check_id}"
    # clean up the directory in S3
    delete(prefix)


def test_should_be_able_to_successfully_run_check(success_results):
    results = success_results["results"]
    spec_d = success_results["spec"]
    assert not "error" in results

    check_id = success_results["spec"]["check_id"]
    prefix = f"checks/{check_id}"
    gray_difference = f"{prefix}/report/gray_difference.png"
    blue_difference = gray_difference.replace("gray", "blue")
    button = f"{prefix}/report/__screenshots__/{spec_d['path']}/{spec_d['component']}/{spec_d['story']}.png"

    # check for the screenshot captured by storycap
    assert exists(button)
    # check for the output comparison images in S3
    assert exists(gray_difference)
    assert exists(blue_difference)

    # check for the objective visual difference between the check frame and the
    # screenshot captured by storycap
    mae = float(results["MAE"].split()[0])
    assert mae < 5.0
    # check timestamps
    assert results["completed_at"] > results["created_at"]
    check_spec_in_results(spec_d, results)


def test_should_be_able_to_successfully_run_check_no_branch_commit(
    success_results_no_commit_branch,
):
    return test_should_be_able_to_successfully_run_check(success_results_no_commit_branch)


def test_should_error_on_branch_problem(error_results_branch):
    get_error(error_results_branch, "branch")


def test_should_error_on_repo_problem(error_results_repo):
    get_error(error_results_repo, "clone")


def test_should_error_on_commit_problem(error_results_commit):
    get_error(error_results_commit, "commit")


def test_should_error_on_missing_frame(error_results_frame):
    get_error(error_results_frame, "frame")
