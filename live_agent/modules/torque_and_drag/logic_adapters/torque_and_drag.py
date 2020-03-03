import queue
import re
import requests

from live_client.events.constants import EVENT_TYPE_EVENT, EVENT_TYPE_DESTROY
from live_client.utils import logging

from live_agent.modules.chatbot.src.actions import (
    CallbackAction,
    ChainedAction,
    NoTextAction,
    ShowTextAction,
)
from live_agent.modules.chatbot.logic_adapters.base import BaseBayesAdapter, WithAssetAdapter


tnd_query_template = """
    -- custom functions
    def @custom_batch():
        list(*) every (
          (*->timestamp# - (*->timestamp#:prev ?? *->timestamp#)) > 10
        ) before
        => @for
        => @yield;

    def convert_to_custom_unit(mnemonic, uom, value):
        curve_unit_force_convert(
            value#, uom, mnemonic:decode("DBTM", "m","HKLA", "N", "MFIA", "m3/s")
        );

    -- getting tripping in data
    {event_type} .timestamp:adjusted_index_timestamp mnemonic!:(WOBA|DMEA|RPMA|ROPA|DBTM|MFIA|BPOS|HKLA)  -- # NOQA

    -- making batch of data and calculating opmode
    => @custom_batch
    => expand
      normalized_operating_mode(
        timestamp#, mnemonic, value#, uom,
        'WOBA', 'DMEA', 'RPMA', 'ROPA', 'DBTM', 'MFIA', 'BPOS', 'HKLA'
      ),
      map(mnemonic, convert_to_custom_unit(mnemonic, uom, value)) as values,
      lookup.rig_event_type_well_id.get(__type) as well_id
      every batch

    -- filtering by opmode
    => @filter
      operating_mode != null
      && operating_mode != 'Connection'
      && operating_mode != 'Drilling Connection'
      && operating_mode != 'Tripping Connection'

    -- filtering by bit depth value
    => @filter values['DBTM'] >= {min_depth}
    => @filter values['DBTM'] < {max_depth}

    -- filtering by low hook load values
    => @filter values['HKLA'] > {min_hookload}

    -- printing data
    => newmap('flowRate', values['MFIA'], 'hookLoad', values['HKLA'], 'bitDepth', values['DBTM'], 'wellId', well_id)
    => @yield
"""


def attempt(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception:
        return None


class TorqueAndDragAdapter(WithAssetAdapter, BaseBayesAdapter):
    state_key = "torque-drag"
    positive_examples = [
        "start td",
        "begin td",
        "calibrate td",
        "calibrate torque and drag",
        "start torque and drag calibration",
    ]
    negative_examples = []
    description = "Start T&D calibration"
    usage_example = "calibrate torque and drag"

    expected_variables = ["start_time", "end_time", "min_depth", "max_depth", "min_hookload"]

    def __init__(self, chatbot, **kwargs):
        super().__init__(chatbot, **kwargs)

        self.settings = kwargs["settings"]
        self.live_url = self.settings["live"]["url"]
        self.username = self.settings["live"]["username"]
        self.password = self.settings["live"]["password"]

        self.helpers = dict(
            (name, func)
            for (name, func) in kwargs.get("functions", {}).items()
            if "_state" not in name
        )

    def process(self, statement, additional_response_selection_parameters=None):
        confidence = self.get_confidence(statement)
        confidence = 1

        # Do we have a selected asset?
        asset = self.get_selected_asset()
        if asset is None or asset == {}:
            return ShowTextAction(
                "[T&D]: Please, select an asset before performing the calibration",
                confidence=confidence,
            )

        # Collect params from message and update variables:
        cmd_params = self.extract_calibration_params(statement.text)
        params = dict(cmd_params)
        for varname in self.expected_variables:
            # Set or update variables passed in the command body:
            if varname in cmd_params.keys():
                self.chatbot.setvar(varname, cmd_params[varname])

            # Read stored vars:
            value = self.chatbot.getvar(varname)
            if value is not None:
                params[varname] = value

        # Try to infer the time range:
        pending_params = self.list_missing_params(cmd_params)
        if "start_time" in pending_params and not (
            "min_depth" in pending_params or "max_depth" in pending_params
        ):
            return ChainedAction(
                confidence=confidence,
                actions=[
                    CallbackAction(self.action_infer_time_range, confidence, params=params),
                    PerformCalibrationAction(confidence, params),
                ],
            )

        # Check if all variables are set:
        pending_variables = self.list_missing_params(params)
        if len(pending_variables) > 0:
            return ShowTextAction(
                f"[T&D]: The following variables are needed to perform the calibration:\n{str(pending_variables)}",  # NOQA
                confidence=confidence,
            )
        params["event_type"] = asset.get("asset_config", {}).get("event_type")

        # Calibration shall be executed:
        return PerformCalibrationAction(confidence=confidence, **params)

    def action_infer_time_range(self, params):
        self.chatbot.live_client.send_message("[T&D]: Attempting to infer time range ...")
        calibrator = TorqueAndDragCalibrator(self.chatbot.live_client)
        if params.get("start_time") is None:
            try:
                calibrator.infer_time_range(params)
            except Exception as e:
                raise Exception(f"[T&D]: {str(e)} Please select another range.")

        return f"Time range retrieved: start_time = {params['start_time']}, end_time = {params['end_time']}"  # NOQA

    def list_missing_params(self, params):
        return list(set(self.expected_variables) - set(params.keys()))

    def can_process(self, statement):
        keywords = ["torque", "drag"]
        found_keywords = [word for word in statement.text.lower().split() if word in keywords]
        has_required_terms = sorted(found_keywords) == sorted(keywords)
        return has_required_terms and super().can_process(statement)

    def extract_calibration_params(self, message):
        # NOTE: Eu usaria o mesmo mecanismo de `EtimQueryAdapter.find_index_value`
        time_pat = r"\d{4}-\d\d-\d\d \d{1,2}:\d{2}"

        m = re.search(
            fr"(\d+).+?(\d+).+?({time_pat}).+?({time_pat}).+?(\d+)\s*$", message
        ) or re.search(fr"(\d+).+?(\d+).+?({time_pat}).+?({time_pat})\s*$", message)
        if m is not None:
            min_hookload = None
            try:
                min_hookload = m.group(5)
            except Exception:
                pass

            return {
                "min_depth": m.group(1),
                "max_depth": m.group(2),
                "start_time": m.group(3),
                "end_time": m.group(4),
                "min_hookload": min_hookload,
            }

        m = re.search(r"(\d+) *m *(?:and|to) *(\d+) *m.*?$", message)
        if m is not None:
            return {"min_depth": m.group(1), "max_depth": m.group(2)}

        return {}


class TorqueAndDragCalibrator:

    query_timeout = 60

    def __init__(self, live_client):
        self.live_client = live_client
        self.settings = live_client.settings
        self.live_url = self.settings["live"]["url"]
        self.username = self.settings["live"]["username"]
        self.password = self.settings["live"]["password"]

    def run_query(self, query_str, realtime=False, span=None, callback=None):
        results_process, results_queue = self.live_client.run_query(
            query_str, realtime=realtime, span=span
        )

        result = []
        while True:
            try:
                event = results_queue.get(timeout=self.query_timeout)
                event_data = event.get("data", {})
                event_type = event_data.get("type")
                if event_type == EVENT_TYPE_EVENT:
                    result.extend(event_data.get("content", []))

                if event_type == EVENT_TYPE_DESTROY:
                    break

            except queue.Empty:
                logging.exception(f"Queue is empty")

        results_process.join(1)

        if callback is not None:
            return callback(result)
        return result

    def build_calibration_data(self, well_id, travelling_block_weight, points):
        return {
            "wellId": well_id,
            "travellingBlockWeight": travelling_block_weight,
            "saveResult": "true",
            "calibrationMethod": "LINEAR_REGRESSION",
            "points": points,
        }

    def request_calibration(self, well_id, points):
        service_path = "/services/plugin-og-model-torquendrag/calibrate/"
        url = f"{self.live_url}{service_path}"

        s = requests.Session()
        s.auth = (self.username, self.password)
        # This variable is ignored for LINEAR_REGRESSION (the only supported right now)
        travelling_block_weight = 0
        calibration_data = self.build_calibration_data(well_id, travelling_block_weight, points)
        response = s.post(url, json=calibration_data)
        try:
            response.raise_for_status()
        except Exception as e:
            logging.exception(f"Request error: {e}")
            raise

        result = response.json()
        return result

    def live_retrieve_regression_points(self, params):
        points = []
        pipes_query = tnd_query_template.format(**params)

        start_time = params["start_time"]
        end_time = params["end_time"]
        span = f"{start_time} to {end_time}"
        points = self.run_query(pipes_query, span=span)
        return points

    def live_timestamps_from_depth_range(self, event_type, depth_range, span=None):
        span = span or "since ts 0 #partial='1'"
        template = "{event_type} mnemonic!:DBTM value#:{depth_range} .timestamp:adjusted_index_timestamp .flags:reversed"  # NOQA

        candidates = []
        pipes_query = template.format(event_type=event_type, depth_range=str(depth_range))
        candidates = self.run_query(pipes_query, span=span)
        return candidates

    def infer_time_range(self, params):
        RANGE_SIZE = 100
        min_depth = int(params["min_depth"])
        max_depth = int(params["max_depth"])
        event_type = params["event_type"]
        start_depth_range = [min_depth, min_depth + RANGE_SIZE]
        end_depth_range = [max_depth - RANGE_SIZE, max_depth]

        end_ts = self.retrieve_timestamp_for_depth_range(
            max, end_depth_range, event_type, error_message="Could not infer a value for end_ts."
        )

        start_ts = self.retrieve_timestamp_for_depth_range(
            min,
            start_depth_range,
            event_type,
            error_message="Could not infer a value for start_ts.",
            span=f"since ts 0 until ts {end_ts} #partial='1'",
        )

        end_ts = (
            attempt(
                self.retrieve_timestamp_for_depth_range,
                max,
                end_depth_range,
                event_type,
                error_message="Could not infer a value for end_ts.",
                span=f"since ts {start_ts} until ts {end_ts} #partial='1'",
            )
            or end_ts
        )

        params.update(start_time=f"ts {start_ts}", end_time=f"ts {end_ts}")

    def retrieve_timestamp_for_depth_range(
        self, fn, depth_range, event_type, *, error_message, span=None
    ):
        try:
            candidates = self.live_timestamps_from_depth_range(event_type, depth_range, span=span)
            timestamp = fn(map(lambda val: int(val["timestamp"]), candidates))
        except Exception:
            raise Exception(error_message)
        return timestamp

    def build_success_response(self, context):
        return f"""Calibration Results:
-  Well ID: {context["wellId"]}
-  Regression method: {context["calibration_result"]["calibrationMethod"]}
-  Travelling Block Weight: {context["calibration_result"]["travellingBlockWeight"]}
-  Pipes Weight Multiplier: {context["calibration_result"]["pipesWeightMultiplier"]}
"""


class PerformCalibrationAction(NoTextAction):
    def run(self):
        MIN_POINT_COUNT = 2
        MIN_HOOKLOAD = 900000

        params = self.params
        calibrator = TorqueAndDragCalibrator(self.live_client)

        # Retrieve the points to calculate the regression:
        self.live_client.send_message("[T&D]: Retrieving data points")
        params["min_hookload"] = params["min_hookload"] or MIN_HOOKLOAD
        points = calibrator.live_retrieve_regression_points(params)
        if len(points) < MIN_POINT_COUNT:
            return "[T&D]: There are not enough data points to perform the calibration. Please select another range."  # NOQA

        well_id = points[0]["wellId"]

        # Perform calibration:
        self.live_client.send_message("[T&D]: Starting calibration")
        try:
            calibration_result = calibrator.request_calibration(well_id, points)
        except Exception:
            return "[T&D]: I'm not able to get data from calibration service. Please, check dda and live configuration."  # NOQA

        # Return a response:
        response = calibrator.build_success_response(
            {"wellId": well_id, "calibration_result": calibration_result}
        )
        return f"[T&D]: {response}"