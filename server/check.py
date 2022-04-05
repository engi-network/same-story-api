import asyncio
import gettext
import json
import os
import socket
import sys
from ast import dump
from asyncio.subprocess import PIPE
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter, time

from helpful_scripts import setup_logging

log = setup_logging()

_ = gettext.gettext


@contextmanager
def set_directory(path):
    origin = Path().absolute()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(origin)


def get_port():
    sock = socket.socket()
    sock.bind(("", 0))
    return sock.getsockname()[1]


async def run(cmd):
    log.info(cmd)
    t1_start = perf_counter()
    proc = await asyncio.create_subprocess_shell(cmd, stdout=PIPE, stderr=PIPE)
    stdout, stderr = await proc.communicate()
    t1_stop = perf_counter()

    if stdout:
        log.info(f"[stdout]\n{stdout.decode()}")
    if stderr:
        log.info(f"[stderr]\n{stderr.decode()}")
    log.info(f"{cmd!r} exited with code {proc.returncode} elapsed {t1_stop - t1_start} seconds")
    return proc.returncode, stdout, stderr


class CheckError(Exception):
    messages = {
        "clone": _("failed to clone GitHub repo"),
        "frame": _("Figma frame missing"),
        "branch": _("failed to sync GitHub repo, check branch"),
        "commit": _("failed to checkout commit in GitHub repo"),
        "install": _("npm install failed"),
        "storycap": _("storycap failed"),
        "aws": _("internal AWS error"),
        "comp": _("failed to generate visual comparison"),
    }

    def __init__(self, e_key):
        self.e_key = e_key

    def __str__(self):
        return CheckError.messages[self.e_key]

    def to_dict(self):
        return {"error": {self.e_key: str(self)}}


async def run_raise(cmd, returncode=0, e_key=None):
    retval = await run(cmd)
    if retval[0] != returncode:
        raise CheckError(e_key)
    return retval


def gettempdir():
    # return tempfile.gettempdir()
    return Path(os.environ.get("TMPDIR", "/tmp/"))


def get_dims(spec):
    # somehow the screenshot ends up being 2x the dimensions given below!
    height = int(spec.get("height", "600")) // 2
    width = int(spec.get("width", "800")) // 2
    return f"{width}x{height}"


async def check(check_id):
    t1_start = perf_counter()
    check_prefix = Path(f"same-story/checks/{check_id}")
    check_dir = gettempdir() / check_prefix
    check_dir.mkdir(parents=True, exist_ok=True)
    log.info(f"{check_prefix=} {check_dir=}")
    await run_raise(f"aws s3 cp s3://{check_prefix} {check_dir} --recursive", e_key="aws")
    spec = json.load(open(check_dir / "specification.json"))
    log.info(f"loaded spec {spec=}")
    check_repo = spec["repository"]
    check_code = check_dir / "code"
    results = "results.json"
    branch = spec.get("branch")
    branch_cmd = f" --branch {branch}" if branch is not None else ""
    commit = spec.get("commit")
    sync = True
    try:
        if not check_code.exists():
            sync = False
            await run_raise(f"gh repo clone {check_repo} {check_code}", e_key="clone")
        with set_directory(check_code):
            if sync:
                # stash any local changes, e.g. package-lock.json
                await run_raise(f"git stash", e_key="clone")
            if branch:
                await run_raise(f"gh repo sync{branch_cmd}", e_key="branch")
            if commit:
                await run_raise(f"git checkout {commit}", e_key="commit")
            await run_raise("npm install", e_key="install")

            log.info("capturing screenshots")
            # TODO storycap concurrency fail, hence MAX_QUEUE_MESSAGES=1
            port = get_port()
            await run_raise(
                f"npx storycap http://localhost:{port} --viewport {get_dims(spec)} "
                f"--serverCmd 'start-storybook -p {port}'",
                e_key="storycap",
            )

            log.info("uploading code screenshots to s3")
            await run_raise(
                f"aws s3 cp {check_code}/__screenshots__ "
                f"s3://{check_prefix}/report/__screenshots__ --recursive",
                e_key="aws",
            )

            log.info("running visual comparisons")
            check_story = spec["story"]
            check_component = spec["component"]
            check_frame = check_dir / f"frames/{check_component}-{check_story}.png"
            check_code_screenshot = (
                check_code / f"__screenshots__/Example/{check_component}/{check_story}.png"
            )
            if not check_frame.exists():
                raise CheckError("frame")
            if not check_code_screenshot.exists():
                raise CheckError("storycap")

            log.info("running regression with blue hightlight and uploading")
            blue_difference = Path("blue_difference.png")
            # compare exits with code 1 even though it seems to have run successfully
            await run(
                f"compare {check_code_screenshot} {check_frame} "
                f"-highlight-color blue {blue_difference}"
            )
            if not blue_difference.exists():
                raise CheckError("comp")
            await run_raise(
                f"aws s3 cp {blue_difference} s3://{check_prefix}/report/{blue_difference}",
                e_key="aws",
            )

            log.info("running regression with gray hightlight and uploading")
            gray_difference = Path("gray_difference.png")
            await run_raise(
                f"convert {check_code_screenshot} -flatten -grayscale Rec709Luminance "
                f"{check_frame} -flatten -grayscale Rec709Luminance "
                "-clone 0-1 -compose darken -composite "
                f"-channel RGB -combine {gray_difference}",
                e_key="comp",
            )
            if not gray_difference.exists():
                raise CheckError("comp")
            await run_raise(
                f"aws s3 cp {gray_difference} s3://{check_prefix}/report/{gray_difference}",
                e_key="aws",
            )
            # compare exits with code 1 even though it seems to have run successfully
            _, _, stderr = await run(
                f"compare -metric MAE {check_code_screenshot} {check_frame} null"
            )
            t1_stop = perf_counter()
            log.info(f"check done {t1_stop - t1_start} seconds")
            now = time()
            json.dump(
                {
                    **spec,
                    "MAE": stderr.decode(),
                    "created_at": now - t1_start,
                    "completed_at": now,
                },
                open(results, "w"),
            )
            await run_raise(
                f"aws s3 cp {results} s3://{check_prefix}/report/{results}", e_key="aws"
            )
    except CheckError as e:
        log.exception(e)
        results_file = check_dir / results
        d = {**spec, **e.to_dict()}
        log.error(f"{d=}")
        json.dump(d, open(results_file, "w"))
        await run_raise(f"aws s3 cp {results_file} s3://{check_prefix}/report/{results}")

    return 0


async def main():
    await check(sys.argv[1])


if __name__ == "__main__":
    asyncio.run(main())
