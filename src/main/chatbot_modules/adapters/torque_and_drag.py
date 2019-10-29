import queue
import re
import requests

from chatterbot.conversation import Statement
from eliot import start_action
from live_client.events.constants import EVENT_TYPE_EVENT, EVENT_TYPE_DESTROY
from live_client.utils import logging

from .base import (
    BaseBayesAdapter,
    WithAssetAdapter
)
from ..constants import (
    get_positive_examples,
    get_negative_examples,
)


tnd_query_template = """
-- custom functions
def @custom_batch():
    list(*) every (
      (*->timestamp# - (*->timestamp#:prev ?? *->timestamp#)) > 10
    ) before
    => @for
    => @yield;

def convert_to_custom_unit(mnemonic, uom, value):
    curve_unit_force_convert(value#, uom, mnemonic:decode("DBTM", "m","HKLA", "N", "MFIA", "m3/s"));

-- getting tripping in data
va007_trip_out .timestamp:adjusted_index_timestamp mnemonic!:(WOBA|DMEA|RPMA|ROPA|DBTM|MFIA|BPOS|HKLA)

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
=> @filter values['HKLA'] > 900000

-- printing data
=> newmap('flowRate', values['MFIA'], 'hookLoad', values['HKLA'], 'bitDepth', values['DBTM'], 'wellId', well_id)
=> @yield
"""


class TorqueAndDragAdapter(WithAssetAdapter, BaseBayesAdapter):
    state_key = 'torque-drag'
    positive_examples = get_positive_examples(state_key)
    negative_examples = get_negative_examples(state_key)

    def __init__(self, chatbot, **kwargs):
        super().__init__(chatbot, **kwargs)

        self.process_settings = kwargs['process_settings']
        self.live_host = self.process_settings['live']['host']
        self.username = self.process_settings['live']['username']
        self.password = self.process_settings['live']['password']

        self.helpers = dict(
            (name, func)
            for (name, func) in kwargs.get('functions', {}).items()
            if '_state' not in name
        )

    def process(self, statement, additional_response_selection_parameters=None):
        confidence = self.get_confidence(statement)
        params = self.extract_calibration_params(statement.text)
        if not params:
            response = Statement("Sorry, I can't read the calibration parameters from your message")
            response.confidence = confidence
            return response

        points = self.retrieve_regression_points(params)
        # TODO: [ECS]: Parâmetros hardcoded abaixo, tratar: <<<<<
        well_id = points[0]['wellId']

        calibration_result = self.request_calibration(well_id, points)

        confidence = 1
        response = self.build_response(confidence, {
            'wellId': well_id,
            'calibration_result': calibration_result,
        })
        return response

    def can_process(self, statement):
        keywords = ['torque', 'drag']
        found_keywords = [word for word in statement.text.lower().split() if word in keywords]
        has_required_terms = sorted(found_keywords) == sorted(keywords)
        return has_required_terms and super().can_process(statement)

    def build_response(self, confidence, context):
        message = f"""
Calibration Results:
-  Well ID: {context["wellId"]}
-  Regression method: {context["calibration_result"]["calibrationMethod"]}
-  Travelling Block Weight: {context["calibration_result"]["travellingBlockWeight"]}
-  Pipes Weight Multiplier: {context["calibration_result"]["pipesWeightMultiplier"]}
"""
        response = Statement(message)
        response.confidence = confidence
        return response

    # TODO: Melhorar a maneira de obter os parâmetros para ficar mais fácil para o usuário:
    def extract_calibration_params(self, message):
        # NOTE: Eu usaria o mesmo mecanismo de `EtimQueryAdapter.find_index_value`
        ret = None
        m = re.search(r'(\d+).+?(\d+).+?(\d{4}-\d\d-\d\d \d{1,2}:\d{2}).+?(\d{4}-\d\d-\d\d \d{1,2}:\d{2})\s*$', message)
        if m is not None:
            ret = {
                'min_depth': m.group(1),
                'max_depth': m.group(2),
                'start_time': m.group(3),
                'end_time': m.group(4),
            }
        return ret

    def run_query(self, query_str, realtime=False, span=None, callback=None):
        results_process, results_queue = self.query_runner(
            query_str,
            realtime=realtime,
            span=span,
        )

        result = []
        while True:
            try:
                event = results_queue.get(timeout=self.query_timeout)
                event_data = event.get('data', {})
                event_type = event_data.get('type')
                if event_type == EVENT_TYPE_EVENT:
                    result.extend(event_data.get('content', []))

                if event_type == EVENT_TYPE_DESTROY:
                    break

            except queue.Empty as e:
                logging.exception(e)

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
            "points": points
        }

    def request_calibration(self, well_id, points):
        service_path = '/services/plugin-og-model-torquendrag/calibrate/'
        url = f'{self.live_host}{service_path}'

        s = requests.Session()
        s.auth = (self.username, self.password)
        travelling_block_weight = 0 # This value is ignored for LINEAR_REGRESSION (the only supported right now)
        calibration_data = self.build_calibration_data(well_id, travelling_block_weight, points)
        response = s.post(
            url,
            json=calibration_data,
        )
        try:
            response.raise_for_status()
        except Exception as e:
            logging.exception(e)
            # NOTE: Acredito que ninguem vai tratar esta exceção.
            # O chamador deveria captura-la e retornar um statement
            # com confidence baixa dizendo que ocorreu um erro
            raise

        result = response.json()
        return result

    def retrieve_regression_points(self, params):
        points = []
        pipes_query = (tnd_query_template.format(
            min_depth = params['min_depth'],
            max_depth = params['max_depth'],
        ))

        start_time = params['start_time']
        end_time = params['end_time']
        span = f'{start_time} to {end_time}'
        points = self.run_query(
            pipes_query,
            span=span,
        )
        return points
