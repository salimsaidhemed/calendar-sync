"""Diagnostics boundary for single-threaded provider operations."""

from contextlib import contextmanager
import http.client
import logging


@contextmanager
def quiet_network():
    previous = logging.root.manager.disable
    http_debug = http.client.HTTPConnection.debuglevel
    https_debug = http.client.HTTPSConnection.debuglevel
    logging.disable(logging.CRITICAL)
    http.client.HTTPConnection.debuglevel = 0
    http.client.HTTPSConnection.debuglevel = 0
    try:
        yield
    finally:
        http.client.HTTPConnection.debuglevel = http_debug
        http.client.HTTPSConnection.debuglevel = https_debug
        logging.disable(previous)
