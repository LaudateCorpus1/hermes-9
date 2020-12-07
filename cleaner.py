"""
cleaner.py
==========
The cleaner service of Hermes. Responsible for deleting processed data after
retention time has passed and if it is offpeak time. Offpeak is the time
period when the cleaning has to be done, because cleaning I/O should be kept
to minimum when receiving and sending exams.
"""
from common.events import Hermes_Event, Series_Event, Severity
import logging
import os
import signal
import sys
import time
from datetime import timedelta, datetime
from pathlib import Path
from shutil import rmtree
import daiquiri
import graphyte

import common.config as config
import common.helper as helper
import common.monitor as monitor
import common.version as version



daiquiri.setup(
    level=logging.INFO,
    outputs=(
        daiquiri.output.Stream(
            formatter=daiquiri.formatter.ColorFormatter(
                fmt="%(color)s%(levelname)-8.8s " "%(name)s: %(message)s%(color_stop)s"
            )
        ),
    ),
)
logger = daiquiri.getLogger("cleaner")

_monitor = None

def receiveSignal(signalNumber, frame):
    """Function for testing purpose only. Should be removed."""
    logger.info("Received:", signalNumber)
    return


def terminateProcess(signalNumber, frame):
    """Triggers the shutdown of the service."""
    helper.g_log("events.shutdown", 1)
    logger.info("Shutdown requested")
    _monitor.send_event(Hermes_Event.SHUTDOWN_REQUEST, Severity.INFO)
    # Note: main_loop can be read here because it has been declared as global variable
    if "main_loop" in globals() and main_loop.is_running:
        main_loop.stop()
    helper.triggerTerminate()


def clean(args):
    """ Main entry function. """
    if helper.isTerminated():
        return

    helper.g_log("events.run", 1)

    try:
        config.read_config()
    except Exception:
        logger.exception("Unable to read configuration. Skipping processing.")
        _monitor.send_event(
            Hermes_Event.CONFIG_UPDATE,
            Severity.WARNING,
            "Unable to read configuration (possibly locked)",
        )
        return

    # TODO: Adaptively reduce the retention time if the disk space is running low

    if _is_offpeak(
        config.hermes["offpeak_start"],
        config.hermes["offpeak_end"],
        datetime.now().time(),
    ):
        success_folder = config.hermes["success_folder"]
        discard_folder = config.hermes["discard_folder"]
        retention = timedelta(seconds=config.hermes["retention"])
        clean_dir(success_folder, retention)
        clean_dir(discard_folder, retention)


def _is_offpeak(offpeak_start, offpeak_end, current_time):
    try:
        start_time = datetime.strptime(offpeak_start, "%H:%M").time()
        end_time = datetime.strptime(offpeak_end, "%H:%M").time()
    except ValueError as e:
        logger.error("Error parsing offpeak time, please check configuration", e)
        raise ValueError(e)

    if start_time < end_time:
        return current_time >= start_time and current_time <= end_time
    # End time is after midnight
    return current_time >= start_time or current_time <= end_time


def clean_dir(discard_folder, retention):
    """
    Cleans the discard folder if it is older than the retention time, starting
    from oldest first.
    """
    candidates = [
        (f, f.stat().st_mtime)
        for f in Path(discard_folder).iterdir()
        if f.is_dir()
        and retention < timedelta(seconds=(time.time() - f.stat().st_mtime))
    ]
    oldest_first = sorted(candidates, key=lambda x: x[1], reverse=True)
    for entry in oldest_first:
        delete_folder(entry)


def delete_folder(entry):
    """ Deletes given folder. """
    delete_path = entry[0]
    series_uid = find_series_uid(delete_path)
    try:
        rmtree(delete_path)
        logger.info(f"Deleted folder {delete_path} from {series_uid}")
        _monitor.send_series_event(Series_Event.CLEAN, series_uid, 0, delete_path, "Deleted folder")
    except Exception as e:
        logger.info(f"Unable to delete folder {delete_path}")
        logger.exception(e)
        _monitor.send_series_event(
            Series_Event.ERROR, series_uid, 0, delete_path, "Unable to delete folder"
        )
        _monitor.send_event(
            Hermes_Event.PROCESSING,
            Severity.ERROR,
            f"Unable to delete folder {delete_path}",
        )


def find_series_uid(work_dir):
    """
    Finds series uid which is always part before the '#'-sign in filename.
    """
    to_be_deleted_dir = Path(work_dir)
    for entry in to_be_deleted_dir.iterdir():
        if "#" in entry.name:
            return entry.name.split("#")[0]
        return "series_uid-not-found"


def exit_cleaner(args):
    """ Stop the asyncio event loop. """
    helper.loop.call_soon_threadsafe(helper.loop.stop)


if __name__ == "__main__":
    logger.info("")
    logger.info(f"Hermes DICOM Cleaner ver {version.hermes_version}")
    logger.info("----------------------------")
    logger.info("")

    # Register system signals to be caught
    signal.signal(signal.SIGINT, terminateProcess)
    signal.signal(signal.SIGQUIT, receiveSignal)
    signal.signal(signal.SIGILL, receiveSignal)
    signal.signal(signal.SIGTRAP, receiveSignal)
    signal.signal(signal.SIGABRT, receiveSignal)
    signal.signal(signal.SIGBUS, receiveSignal)
    signal.signal(signal.SIGFPE, receiveSignal)
    signal.signal(signal.SIGUSR1, receiveSignal)
    signal.signal(signal.SIGSEGV, receiveSignal)
    signal.signal(signal.SIGUSR2, receiveSignal)
    signal.signal(signal.SIGPIPE, receiveSignal)
    signal.signal(signal.SIGALRM, receiveSignal)
    signal.signal(signal.SIGTERM, terminateProcess)

    instance_name = "main"

    if len(sys.argv) > 1:
        instance_name = sys.argv[1]

    logger.info(sys.version)
    logger.info(f"Instance name = {instance_name}")
    logger.info(f"Cleaner PID is: {os.getpid()}")

    try:
        config.read_config()
    except Exception:
        logger.exception("Cannot start service. Going down.")
        sys.exit(1)

    _monitor = monitor.configure("cleaner", instance_name, config.hermes["bookkeeper"])
    _monitor.send_event(
        Hermes_Event.BOOT, Severity.INFO, f"PID = {os.getpid()}"
    )

    graphite_prefix = "hermes.cleaner." + instance_name

    if len(config.hermes["graphite_ip"]) > 0:
        logger.info(
            f"Sending events to graphite server: {config.hermes['graphite_ip']}"
        )
        graphyte.init(
            config.hermes["graphite_ip"],
            config.hermes["graphite_port"],
            prefix=graphite_prefix,
        )

    global main_loop
    main_loop = helper.RepeatedTimer(
        config.hermes["cleaner_scan_interval"], clean, exit_cleaner, {}
    )
    main_loop.start()

    helper.g_log("events.boot", 1)

    # Start the asyncio event loop for asynchronous function calls
    helper.loop.run_forever()

    # Process will exit here once the asyncio loop has been stopped
    _monitor.send_event(Hermes_Event.SHUTDOWN, Severity.INFO)
    logger.info("Going down now")
