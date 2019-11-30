import chatterbot
from chatterbot.conversation import Statement

class ChatBot(chatterbot.ChatBot):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.context = kwargs

    def generate_response(self, input_statement, additional_response_selection_parameters=None):
        """
        Return a response based on a given input statement.

        :param input_statement: The input statement to be processed.
        """
        results = []
        result = None
        max_confidence = -1

        for adapter in self.logic_adapters:
            if adapter.can_process(input_statement):

                output = adapter.process(input_statement, additional_response_selection_parameters)
                results.append(output)

                self.logger.info(
                    '{} selected "{}" as a response with a confidence of {}'.format(
                        adapter.class_name, output.text, output.confidence
                    )
                )

                if output.confidence > max_confidence:
                    result = output
                    max_confidence = output.confidence
            else:
                self.logger.info(
                    'Not processing the statement using {}'.format(adapter.class_name)
                )

        class ResultOption:
            def __init__(self, statement, count=1):
                self.statement = statement
                self.count = count

        # If multiple adapters agree on the same statement,
        # then that statement is more likely to be the correct response
        if len(results) >= 3:
            result_options = {}
            for result_option in results:
                result_string = result_option.text + ':' + (result_option.in_response_to or '')

                if result_string in result_options:
                    result_options[result_string].count += 1
                    if result_options[result_string].statement.confidence < result_option.confidence:
                        result_options[result_string].statement = result_option
                else:
                    result_options[result_string] = ResultOption(
                        result_option
                    )

            most_common = list(result_options.values())[0]

            for result_option in result_options.values():
                if result_option.count > most_common.count:
                    most_common = result_option

            if most_common.count > 1:
                result = most_common.statement

        # Update the result to return:
        result.in_response_to = input_statement.text,
        result.conversation = input_statement.conversation,
        result.persona = 'bot:' + self.name

        return result


class ActionStatement(Statement):
    def __init__(self, text, in_response_to = None, confidence=None, **kwargs):
        super().__init__(text, in_response_to, **kwargs)
        self.confidence = confidence
        self.chatbot = kwargs.get('chatbot')
        self.liveclient = kwargs.get('liveclient')

    def run(self):
        raise NotImplementedError()


class ShowTextAction(ActionStatement):
    def run(self):
        return self.text


class NoTextAction(ActionStatement):
    def __init__(self, in_response_to = None, confidence=None, **kwargs):
        super().__init__("", in_response_to, confidence, **kwargs)
        self.params = kwargs
