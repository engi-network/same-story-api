import asyncio
import json
import os
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


async def run(cmd):
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


async def check(check_id):
    t1_start = perf_counter()
    check_prefix = Path(f"same-story/checks/{check_id}")
    with tempfile.TemporaryDirectory() as _tmp_dir:
        check_dir = Path(_tmp_dir) / check_prefix
        log.info(f"{check_prefix=} {check_dir=}")
        await run_raise(f"aws s3 cp s3://{check_prefix} {check_dir} --recursive")
        spec = json.load(open(check_dir / "specification.json"))
        log.info(f"loaded spec {spec=}")
        check_repo = spec["repository"]
        check_code = check_dir / "code"
        await run_raise(f"gh repo clone {check_repo} {check_code}")
        with set_directory(check_code):
            log.info("npm install")
            await run_raise("npm install")
            # await run_raise("npm install puppeteer")
            log.info("capturing screenshots")
            # await run_raise("npm run storycap -- --serverTimeout 300000 --captureTimeout 300000")
            # somehow the screenshot ends up being 2x the dimensions given below, i.e. 800x600
            # TODO still can't run two jobs concurrently b/c port clash
            await run_raise(
                "npm run storycap -- --serverTimeout 300000 --captureTimeout 300000 --viewport 400x300"
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
            _, _, stderr = await run(
                f"compare -metric MAE {check_code_screenshot} {check_frame} null"
            )
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
