import asyncio
import json
import os
import shutil
import socket
import sys
import tempfile
from asyncio.subprocess import PIPE
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter

from helpful_scripts import setup_logging

log = setup_logging()


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


async def run_raise(cmd, returncode=0):
    retval = await run(cmd)
    if retval[0] != returncode:
        raise RuntimeError
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
    await run_raise(f"aws s3 cp s3://{check_prefix} {check_dir} --recursive")
    spec = json.load(open(check_dir / "specification.json"))
    log.info(f"loaded spec {spec=}")
    check_repo = spec["repository"]
    check_code = check_dir / "code"
    branch = spec.get("branch")
    commit = spec.get("commit")
    sync = True
    if not check_code.exists():
        sync = False
        await run_raise(f"gh repo clone {check_repo} {check_code}")
    with set_directory(check_code):
        if sync:
            # stash any local changes, e.g. package-lock.json
            await run_raise(f"git stash")
            # perform incremental update
            branch_cmd = f" --branch {branch}" if branch is not None else ""
            await run_raise(f"gh repo sync{branch_cmd}")
        if commit:
            await run_raise(f"git checkout {commit}")
        await run_raise("npm install")
        log.info("capturing screenshots")
        # TODO even when specifying a different port for each run, storycap doesn't work concurrently
        port = get_port()
        await run_raise(
            f"npx storycap http://localhost:{port} --viewport {get_dims(spec)} --serverCmd 'start-storybook -p {port}'"
        )
        log.info("uploading code screenshots to s3")
        await run_raise(
            f"aws s3 cp {check_code}/__screenshots__ s3://{check_prefix}/report/__screenshots__ --recursive"
        )
        log.info("running visual comparisons")
        check_story = spec["story"]
        check_component = spec["component"]
        check_frame = check_dir / f"frames/{check_component}-{check_story}.png"
        check_code_screenshot = (
            check_code / f"__screenshots__/Example/{check_component}/{check_story}.png"
        )
        assert check_frame.exists()
        assert check_code_screenshot.exists()
        blue_difference = "blue_difference.png"
        log.info("running regression with blue hightlight and uploading")
        # compare exits with code 1 even though it seems to have run successfully
        await run(
            f"compare {check_code_screenshot} {check_frame} -highlight-color blue {blue_difference}"
        )
        await run_raise(
            f"aws s3 cp {blue_difference} s3://{check_prefix}/report/{blue_difference}"
        )
        log.info("running regression with gray hightlight and uploading")

        gray_difference = "gray_difference.png"
        await run_raise(
            f"convert {check_code_screenshot} -flatten -grayscale Rec709Luminance "
            f"{check_frame} -flatten -grayscale Rec709Luminance "
            "-clone 0-1 -compose darken -composite "
            f"-channel RGB -combine {gray_difference}"
        )
        await run_raise(
            f"aws s3 cp {gray_difference} s3://{check_prefix}/report/{gray_difference}"
        )
        # compare exits with code 1 even though it seems to have run successfully
        _, _, stderr = await run(f"compare -metric MAE {check_code_screenshot} {check_frame} null")
        results = "results.json"
        json.dump({"MAE": stderr.decode()}, open(results, "w"))
        await run_raise(f"aws s3 cp {results} s3://{check_prefix}/report/{results}")
    t1_stop = perf_counter()
    log.info(f"check done {t1_stop - t1_start} seconds")
    return 0


async def main():
    await check(sys.argv[1])


if __name__ == "__main__":
    asyncio.run(main())
