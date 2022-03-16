import logging

import coloredlogs


def setup_logging(log_level=logging.INFO):
    logger = logging.getLogger()

    # Set log format to dislay the logger name to hunt down verbose logging modules
    fmt = "%(name)-25s %(levelname)-8s %(message)s"

    coloredlogs.install(level=log_level, fmt=fmt, logger=logger)

    return logger
