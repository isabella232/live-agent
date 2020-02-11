import queue
import re
import traceback

from live.utils import query
from live_client.utils import logging
from utils import logging as logx
from setproctitle import setproctitle
from utils import monitors
from ddg.search import DuckEngine

__all__ = ["start"]

READ_TIMEOUT = 120


def clean_term(term):
    m = re.search(r"\w+", term)
    if m is None:
        return None
    return m.group(0)


class AlertReferenceMonitor(monitors.Monitor):
    def __init__(self, asset_name, settings, helpers=None, task_id=None):
        super().__init__(asset_name, settings, helpers, task_id)

        self.process_name = f"{self.asset_name} - alert reference monitor"
        self.span = settings["monitor"].get("span")

    def run(self):
        with logx.manage_action(
            logx.get_log_action(self.task_id, "alert_reference_monitor")
        ) as action:
            with action.context():
                self.execute()

    def execute(self):
        logging.info("{}: Alert Reference Monitor".format(self.process_name))
        setproctitle('DDA: Alert Reference Monitor "{}"'.format(self.process_name))

        while True:
            # Register query for annotations in the console API:
            results_process, results_queue = self.run_query(
                self.build_query(), span=self.span, realtime=True
            )

            # Get and process results:
            try:
                handle_process_queue(self.process_annotation, results_process, results_queue, self)
            except queue.Empty:
                continue
            except Exception as e:
                # Dev log:
                print(f"Ocorreu um erro: {e}")
                print("Stack trace:")
                traceback.print_exc()
                # Persistent log:
                logging.exception()
                logging.error(str(e))
                break
            finally:
                results_process.join()

    def build_query(self):
        return "__annotations (__src:rulealert | src:rulealert)"

    def process_annotation(self, event):
        annotation_message = event["message"]
        search_term = self._extract_search_term(annotation_message)

        engine = DuckEngine()
        results = engine.search(search_term)
        message_lines = [
            f'References found for query "{annotation_message}":',
            f"Search term: {search_term}",
        ]
        if len(results) > 0:
            res = results[0]
            message_lines.append(f"{search_term}: <{res.url}|{res.desc}>")
        else:
            message_lines.append("No result found")
        message = "\n".join(message_lines)

        self.send_message(self.process_name, f"{message}", timestamp=event["timestamp"])

    def _extract_search_term(self, annotation_message):
        parts = annotation_message.split(":")
        part = parts[1] if len(parts) > 1 else parts[0]
        words = part.strip().split(" ")
        word = None
        for w in words:
            word = clean_term(w)
            if word is not None:
                break

        return word


def start(asset_name, settings, helpers=None, task_id=None):
    m = AlertReferenceMonitor(asset_name, settings, helpers, task_id)
    m.run()


def handle_process_queue(processor, process, output_queue, context):
    query.handle_events(
        processor_func=process_accumulator_last_result(processor),
        results_queue=output_queue,
        settings=context.settings,
        timeout=READ_TIMEOUT,
    )


def process_accumulator_last_result(processor):
    def process(accumulator):
        latest_event = accumulator[-1]
        processor(latest_event)
        return accumulator

    return process
