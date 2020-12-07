"""
router.py
=========
Hermes' central router module that evaluates the routing rules and decides which series should be sent to which target. 
"""
# Standard python includes
from common.events import Hermes_Event, Series_Event, Severity
import time
import signal
import os
import sys
import graphyte
import logging
import daiquiri

# App-specific includes
import common.helper as helper
import common.config as config
import common.monitor as monitor
import common.version as version
from routing.process_series import process_series
from routing.process_series import process_error_files


# NOTES: Currently, the router only implements series-level rules, i.e. the proxy rules will be executed
#        once the series is complete. In the future, also study-level rules can be implemented (i.e. a
#        rule can be a series-level or study-level rule). Series-level rules are executed as done right now.
#        If a study-level rule exists that applies to a series, the series will be moved to an /incoming-study
#        folder and renamed with the studyUID as prefix. Once the study is complete (via a separate time
#        tigger), the study-level rules will be applied by taking each rule and collecting the series of
#        the studies that apply to the rule. Each study-level rule will create a separate outgoing folder
#        so that all series that apply to the study-level rule are transferred together in one DICOM
#        transfer (association). This might be necessary for certain PACS systems or workstations (e.g.
#        when transferring 4D series).

daiquiri.setup(
    level=logging.INFO,
    outputs=(
        daiquiri.output.Stream(
            formatter=daiquiri.formatter.ColorFormatter(
                fmt="%(color)s%(levelname)-8.8s "
                "%(name)s: %(message)s%(color_stop)s"
            )
        ),
    ),
)
logger = daiquiri.getLogger("router")

_monitor = None

def receiveSignal(signalNumber, frame):
    """Function for testing purpose only. Should be removed."""
    logger.info(f'Received: {signalNumber}')
    return


def terminateProcess(signalNumber, frame):
    """Triggers the shutdown of the service."""
    helper.g_log('events.shutdown', 1)
    logger.info('Shutdown requested')
    _monitor.send_event(Hermes_Event.SHUTDOWN_REQUEST, Severity.INFO)
    # Note: main_loop can be read here because it has been declared as global variable
    if 'main_loop' in globals() and main_loop.is_running:
        main_loop.stop()
    helper.triggerTerminate()


def runRouter(args):
    """Main processing function that is called every second."""
    if helper.isTerminated():
        return

    helper.g_log('events.run', 1)

    #logger.info('')
    #logger.info('Processing incoming folder...')

    try:
        config.read_config()
    except Exception:
        logger.exception("Unable to update configuration. Skipping processing.")
        _monitor.send_event(Hermes_Event.CONFIG_UPDATE,Severity.WARNING,"Unable to update configuration (possibly locked)")
        return

    filecount=0
    series={}
    completeSeries={}

    error_files_found = False

    # Check the incoming folder for completed series. To this end, generate a map of all
    # series in the folder with the timestamp of the latest DICOM file as value
    for entry in os.scandir(config.hermes['incoming_folder']):
        if entry.name.endswith(".tags") and not entry.is_dir():
            filecount += 1
            seriesString=entry.name.split('#',1)[0]
            modificationTime=entry.stat().st_mtime

            if seriesString in series.keys():
                if modificationTime > series[seriesString]:
                    series[seriesString]=modificationTime
            else:
                series[seriesString]=modificationTime
        # Check if at least one .error file exists. In that case, the incoming folder should
        # be searched for .error files at the end of the update run
        if (not error_files_found) and entry.name.endswith(".error"):
            error_files_found = True

    # Check if any of the series exceeds the "series complete" threshold
    for entry in series:
        if ((time.time()-series[entry]) > config.hermes['series_complete_trigger']):
            completeSeries[entry]=series[entry]

    #logger.info(f'Files found     = {filecount}')
    #logger.info(f'Series found    = {len(series)}')
    #logger.info(f'Complete series = {len(completeSeries)}')
    helper.g_log('incoming.files', filecount)
    helper.g_log('incoming.series', len(series))

    # Process all complete series
    for entry in sorted(completeSeries):
        try:
            process_series(entry)
        except Exception:
            logger.exception(f'Problems while processing series {entry}')
            _monitor.send_series_event(Series_Event.ERROR, entry, 0, "", "Exception while processing")
            _monitor.send_event(Hermes_Event.PROCESSING, Severity.ERROR, "Exception while processing series")
        # If termination is requested, stop processing series after the active one has been completed
        if helper.isTerminated():
            return

    if error_files_found:
        process_error_files(_monitor)


def exitRouter(args):
    """Callback function that is triggered when the process terminates. Stops the asyncio event loop."""
    helper.loop.call_soon_threadsafe(helper.loop.stop)


if __name__ == '__main__':
    logger.info("")
    logger.info(f"Hermes DICOM Router ver {version.hermes_version}")
    logger.info("----------------------------")
    logger.info("")

    # Register system signals to be caught
    signal.signal(signal.SIGINT,   terminateProcess)
    signal.signal(signal.SIGQUIT,  receiveSignal)
    signal.signal(signal.SIGILL,   receiveSignal)
    signal.signal(signal.SIGTRAP,  receiveSignal)
    signal.signal(signal.SIGABRT,  receiveSignal)
    signal.signal(signal.SIGBUS,   receiveSignal)
    signal.signal(signal.SIGFPE,   receiveSignal)
    signal.signal(signal.SIGUSR1,  receiveSignal)
    signal.signal(signal.SIGSEGV,  receiveSignal)
    signal.signal(signal.SIGUSR2,  receiveSignal)
    signal.signal(signal.SIGPIPE,  receiveSignal)
    signal.signal(signal.SIGALRM,  receiveSignal)
    signal.signal(signal.SIGTERM,  terminateProcess)
    #signal.signal(signal.SIGHUP,  readConfiguration)
    #signal.signal(signal.SIGKILL, receiveSignal)

    instance_name="main"

    if len(sys.argv)>1:
        instance_name=sys.argv[1]

    logger.info(sys.version)
    logger.info(f'Instance name = {instance_name}')
    logger.info(f'Instance PID = {os.getpid()}')

    # Read the configuration file and terminate if it cannot be read
    try:
        config.read_config()
    except Exception:
        logger.exception("Cannot start service. Going down.")
        sys.exit(1)

    _monitor = monitor.configure('router',instance_name,config.hermes['bookkeeper'])
    _monitor.send_event(Hermes_Event.BOOT,Severity.INFO,f'PID = {os.getpid()}')

    graphite_prefix='hermes.router.'+instance_name
    if len(config.hermes['graphite_ip']) > 0:
        logger.info(f'Sending events to graphite server: {config.hermes["graphite_ip"]}')
        graphyte.init(config.hermes['graphite_ip'], config.hermes['graphite_port'], prefix=graphite_prefix)

    logger.info(f'Incoming folder: {config.hermes["incoming_folder"]}')
    logger.info(f'Outgoing folder: {config.hermes["outgoing_folder"]}')

    # Start the timer that will periodically trigger the scan of the incoming folder
    global main_loop
    main_loop = helper.RepeatedTimer(config.hermes['router_scan_interval'], runRouter, exitRouter, {})
    main_loop.start()

    helper.g_log('events.boot', 1)

    # Start the asyncio event loop for asynchronous function calls
    helper.loop.run_forever()

    # Process will exit here once the asyncio loop has been stopped
    _monitor.send_event(Hermes_Event.SHUTDOWN, Severity.INFO)
    logger.info('Going down now')
