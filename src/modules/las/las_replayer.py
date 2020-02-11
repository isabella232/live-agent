# -*- coding: utf-8 -*-
from enum import Enum
import csv
from setproctitle import setproctitle
from eliot import Action

import lasio

from live_client.events import raw, messenger
from live_client.utils import timestamp, logging

from .utils import loop

__all__ = ["start"]


READ_MODES = Enum("READ_MODES", "SINGLE_PASS, CONTINUOUS")


def maybe_send_chat_message(chat, last_ts, next_ts, index_mnemonic, process_settings):
    if not chat:
        return

    items_to_send = []
    for item in chat:
        item_index = int(item.get(index_mnemonic, -1))

        if last_ts < item_index <= next_ts:
            items_to_send.append(item)

    logging.debug("{} messages between {} and {}".format(len(items_to_send), last_ts, next_ts))

    for item in items_to_send:
        message = item.get("MESSAGE")
        source = item.get("SOURCE")
        if message and source:
            messenger.maybe_send_chat_message(message, process_settings, author_name=source)


def send_message(message, timestamp, process_settings=None):
    messenger.maybe_send_message_event(message, timestamp, process_settings)
    messenger.maybe_send_chat_message(message, process_settings)


def delay_output(last_timestamp, next_timestamp):
    if last_timestamp == 0:
        sleep_time = 0
    else:
        sleep_time = max(next_timestamp - last_timestamp, 0)

    loop.await_next_cycle(sleep_time)


def read_next_frame(values_iterator, curves, curves_data, index_mnemonic):
    try:
        index, values = next(values_iterator)
        success = True
    except Exception as e:
        output_frame = {}
        success = False
        logging.debug("Error reading next value, {}<{}>".format(e, type(e)))

    if success:
        output_frame = {index_mnemonic: {"value": index, "uom": "s"}}

        for index, channel in enumerate(curves):
            uom = curves_data.get(channel)
            channel_value = values[index]
            output_frame[channel] = {"value": channel_value, "uom": uom}

    return success, output_frame


def open_files(process_settings, iterations, mode=READ_MODES.CONTINUOUS):
    path_list = process_settings["path_list"]
    index_mnemonic = process_settings["index_mnemonic"]

    if mode == READ_MODES.CONTINUOUS:
        path_index = iterations % len(path_list)
    else:
        path_index = iterations

    try:
        las_path, chat_path = path_list[path_index]
        with open(las_path, "r") as las_file:
            data = lasio.read(las_file)

        if chat_path:
            with open(chat_path, "r") as chat_file:
                chat_data = list(csv.DictReader(chat_file))

            logging.debug("Success opening files {} and {}>".format(las_path, chat_path))
        else:
            chat_data = []
            logging.debug("Success opening file {}>".format(las_path))

        success = True
    except Exception as e:
        data = e
        chat_data = None
        success = False
        logging.error("Error opening file {}, {}<{}>".format(las_path, e, type(e)))

    return success, data, chat_data, index_mnemonic


def generate_events(event_type, las_data, chat_data, index_mnemonic, process_settings):
    logging.info("{}: Event generation started".format(event_type))

    source_name = las_data.version.SOURCE.value
    curves_data = dict((item.mnemonic, item.unit) for item in las_data.curves)
    las_df = las_data.df()
    values_iterator = las_df.iterrows()
    curves = las_df.columns

    success = True
    last_timestamp = 0
    while success:
        success, statuses = read_next_frame(values_iterator, curves, curves_data, index_mnemonic)

        if success:
            next_timestamp = statuses.get(index_mnemonic, {}).get("value", 0)

            delay_output(last_timestamp, next_timestamp)

            if last_timestamp == 0:
                message = "Replay from '{}' started at TIME {}".format(source_name, next_timestamp)
                send_message(message, timestamp.get_timestamp(), process_settings=process_settings)

            raw.create(event_type, statuses, process_settings)

            maybe_send_chat_message(
                chat_data, last_timestamp, next_timestamp, index_mnemonic, process_settings
            )
            last_timestamp = next_timestamp


def start(process_settings, task_id):
    with Action.continue_task(task_id=task_id):
        event_type = process_settings["output"]["event_type"]
        cooldown_time = process_settings.get("cooldown_time", 300)
        setproctitle('DDA: LAS replayer for "{}"'.format(event_type))

        read_mode = READ_MODES.CONTINUOUS

        iterations = 0
        while True:
            try:
                success, las_data, chat_data, index_mnemonic = open_files(
                    process_settings, iterations, mode=read_mode
                )

                if success:
                    generate_events(
                        event_type, las_data, chat_data, index_mnemonic, process_settings
                    )
                    logging.info("Iteration {} successful".format(iterations))
                else:
                    logging.warn("Could not open files")

                loop.await_next_cycle(
                    cooldown_time,
                    event_type,
                    message="Sleeping for {:.1f} minutes between runs".format(cooldown_time / 60.0),
                    log_func=logging.info,
                )

            except KeyboardInterrupt:
                logging.info("Stopping after {} iterations".format(iterations))
                raise

            except Exception as e:
                logging.error(
                    "Error processing events during iteration {}, {}<{}>".format(
                        iterations, e, type(e)
                    )
                )

            iterations += 1

    return