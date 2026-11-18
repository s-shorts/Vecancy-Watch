"""Background loop: wakes every 15 minutes, runs a scan when one is due and sends reminders."""
import logging
import threading

from . import scanner

log = logging.getLogger("vacancywatch")
_stop = threading.Event()
CHECK_EVERY_SECONDS = 15 * 60


def _loop():
    _stop.wait(5)  # let the web server start first
    while not _stop.is_set():
        try:
            scanner.tick()
        except Exception:
            log.exception("scheduled check failed")
        _stop.wait(CHECK_EVERY_SECONDS)


def start():
    t = threading.Thread(target=_loop, name="vacancywatch-scheduler", daemon=True)
    t.start()
    return t


def stop():
    _stop.set()
