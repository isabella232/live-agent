# -*- coding: utf-8 -*-
from live_client.utils import logging

__all__ = ["refresh_accumulator", "filter_events", "maybe_reset_latest_index"]


def refresh_accumulator(latest_events, accumulator, index_mnemonic, window_duration):
    # Purge old events and add the new ones
    accumulator.extend(latest_events)

    # Find out the latest timestamp received
    latest_event = latest_events[-1]
    window_end = latest_event.get(index_mnemonic, 0)
    window_start = window_end - window_duration

    seen_indexes = set()

    purged_accumulator = []
    for item in accumulator:
        index = item.get(index_mnemonic, 0)
        if (index not in seen_indexes) and (window_start <= index <= window_end):
            purged_accumulator.append(item)
            seen_indexes.add(index)

    logging.debug(
        "{} of {} events between {} and {}".format(
            len(purged_accumulator), len(accumulator), window_start, window_end
        )
    )

    return purged_accumulator, window_start, window_end


def filter_events(events, window_start, index_mnemonic, value_mnemonic=None):
    events_in_window = [item for item in events if item.get(index_mnemonic, 0) > window_start]

    if value_mnemonic:
        valid_events = [item for item in events_in_window if item.get(value_mnemonic) is not None]
    else:
        valid_events = events_in_window

    return valid_events


def maybe_reset_latest_index(process_data, event_list):
    # If the index gets reset we must reset {latest_seen_index}
    latest_seen_index = process_data.get("latest_seen_index", 0)
    index_mnemonic = process_data["index_mnemonic"]

    last_event = event_list[-1]
    last_event_index = last_event.get(index_mnemonic, latest_seen_index)
    if last_event_index < latest_seen_index:
        process_data["latest_seen_index"] = last_event_index

    return process_data
