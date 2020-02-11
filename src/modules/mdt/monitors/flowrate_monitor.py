# -*- coding: utf-8 -*-
from setproctitle import setproctitle

from live_client.utils import logging
from live_client.query import on_event
from live_client.events import messenger
from utils.logging import get_log_action
from live.utils.query import handle_events as process_event


__all__ = ["start"]

read_timeout = 120
request_timeout = (3.05, 5)
max_retries = 5


def check_rate(accumulator, settings):
    if not accumulator:
        return

    # Generate alerts whether the threshold was reached
    # a new event means another threshold breach
    template = "Whoa! {} was changed {} times over the last {} seconds, please calm down ({})"
    latest_event = accumulator[-1]
    message = template.format(
        latest_event["mnemonic"],
        int(latest_event["num_changes"]),
        int((int(latest_event["end"]) - int(latest_event["start"])) / 1000),
        latest_event["values_list"],
    )
    messenger.send_message(message, timestamp=latest_event["timestamp"], process_settings=settings)

    return accumulator


def build_query(settings):
    event_type = settings.get("event_type")
    monitor_settings = settings.get("monitor", {})
    max_threshold = monitor_settings["max_threshold"]
    window_duration = monitor_settings["window_duration"]
    sampling_frequency = monitor_settings["sampling_frequency"]
    precision = monitor_settings["precision"]

    flowrate_mnemonics = monitor_settings["flowrate_mnemonics"]
    mnemonics_list = "|".join(flowrate_mnemonics)

    query = f"""
        {event_type} mnemonic!:({mnemonics_list})
        => value as current_value,
           value#:round({precision}):dcount() as num_changes,
           value#round({precision}):set() as values_list,
           timestamp:min() as start,
           timestamp:max() as end,
           mnemonic
          by mnemonic
          every {sampling_frequency} seconds over last {window_duration} seconds
        => @filter(num_changes >= {max_threshold})
        => @throttle 1, {window_duration} seconds
    """
    logging.debug(f'query is "{query}"')

    return query


def start(settings, task_id=None, **kwargs):
    action = get_log_action(task_id, "flowrate_monitor")
    with action.context():
        logging.info("Flowrate monitor started")
        setproctitle("DDA: Flowrate monitor")

        window_duration = settings["monitor"]["window_duration"]

        fr_query = build_query(settings)
        span = f"last {window_duration} seconds"

        @on_event(fr_query, settings, span=span, timeout=read_timeout)
        def handle_events(event, settings=None, accumulator=None):
            def update_monitor_state(accumulator):
                check_rate(accumulator, settings)

            process_event(event, update_monitor_state, settings, accumulator)

        handle_events(settings=settings, accumulator=[])

    action.finish()