# -*- coding: utf-8 -*-
import sys
import os
import argparse
from live_client.utils import logging

from live_agent import LiveAgent

__all__ = []

PIDFILE_ENVVAR = "DDA_PID_FILE"
DEFAULT_PIDFILE = "/var/run/live-agent.pid"

LOGFILE_ENVVAR = "DDA_LOG_FILE"
DEFAULT_LOG = "/var/log/live-agent.log"


def parse_arguments(argv):
    parser = argparse.ArgumentParser(description="Control of a live-agent")
    parser.add_argument(
        "command", choices=["console", "start", "stop", "restart"], help="Command for the agent"
    )
    parser.add_argument("--settings", dest="settings_file", required=True, help="A settings file")

    args = parser.parse_args(argv[1:])
    if not os.path.isfile(args.settings_file):
        parser.error(f"Invalid value for --settings ({args.settings_file}).")

    return args


if __name__ == "__main__":
    args = parse_arguments(sys.argv)
    command = args.command
    settings_file = args.settings_file

    pidfile = os.environ.get(PIDFILE_ENVVAR, DEFAULT_PIDFILE)
    daemon = LiveAgent(pidfile, settings_file)

    if command == "console":
        logging.info("Starting on-console run")
        daemon.run()
    elif command == "start":
        logging.info("A new START command was received")
        daemon.start()
    elif command == "stop":
        logging.info("A new STOP command was received")
        daemon.stop()
    elif command == "restart":
        logging.info("A new RESTART command was received")
        daemon.restart()
