from ATE.TES.apps.common.connection_handler import ConnectionHandler
from ATE.TES.apps.common.Utils import LogLevel
import json
import re
from typing import Optional

TOPIC_COMMAND = "Master/cmd"
TOPIC_TESTSTATUS = "TestApp/status"
TOPIC_TESTRESULT = "TestApp/testresult"
TOPIC_TESTSUMMARY = "TestApp/testsummary"
TOPIC_TESTRESOURCE = "TestApp/peripherystate"
TOPIC_CONTROLSTATUS = "Control/status"
INTERFACE_VERSION = 1
DEAD = 0
ALIVE = 1


class MasterConnectionHandler:

    """ handle commands """

    def __init__(self, host, port, sites, device_id, status_consumer):
        mqtt_client_id = f'masterapp.{device_id}'
        self.status_consumer = status_consumer
        self.log = status_consumer.log
        self.mqtt = ConnectionHandler(host, port, mqtt_client_id, self.log)
        self.mqtt.init_mqtt_client_callbacks(self._on_connect_handler,
                                             self._on_message_handler,
                                             self._on_disconnect_handler)
        self.sites = sites
        self.device_id = device_id
        self.connected_flag = False

        self.mqtt.register_route("Control", lambda topic, payload: self.dispatch_control_message(topic, self.mqtt.decode_payload(payload)))
        self.mqtt.register_route("TestApp", lambda topic, payload: self.dispatch_testapp_message(topic, self.mqtt.decode_payload(payload)))

    def start(self):
        self.mqtt.set_last_will(
            self._generate_base_topic_status(),
            self.mqtt.create_message(
                self._generate_status_message(DEAD, 'crash')))
        self.mqtt.start_loop()

    async def stop(self):
        await self.mqtt.stop_loop()

    def publish_state(self, state, statedict=None):
        self.mqtt.publish(self._generate_base_topic_status(),
                          self.mqtt.create_message(
                          self._generate_status_message(ALIVE, state, statedict)),
                          False)

    def publish_resource_config(self, resource_id: str, config: dict):
        self.mqtt.publish(self._generate_resource_response_topic(resource_id),
                          self.mqtt.create_message(
                              self._generate_io_control_result_message(resource_id, config)),
                          False)

    def send_load_test_to_all_sites(self, testapp_params):
        topic = f'ate/{self.device_id}/Control/cmd'
        params = {
            'type': 'cmd',
            'command': 'loadTest',
            'testapp_params': testapp_params,
            'sites': self.sites,
        }
        self.log.log_message(LogLevel.Info(), 'Send LoadLot to sites...')
        self.mqtt.publish(topic, json.dumps(params), 0, False)

    def send_next_to_all_sites(self, job_data: Optional[dict] = None):
        topic = f'ate/{self.device_id}/TestApp/cmd'
        params = self._generate_command_message('next')

        if job_data is not None:
            params['job_data'] = job_data
        self.mqtt.publish(topic, json.dumps(params), 0, False)

    def send_terminate_to_all_sites(self):
        topic = f'ate/{self.device_id}/TestApp/cmd'
        self.mqtt.publish(topic, json.dumps(self._generate_command_message('terminate')), 0, False)

    def send_reset_to_all_sites(self):
        topic = f'ate/{self.device_id}/Control/cmd'
        self.mqtt.publish(topic, json.dumps(self._generate_command_message('reset')), 0, False)

    def _generate_command_message(self, command):
        return {'type': 'cmd',
                'command': command,
                'sites': self.sites,
                }

    def on_cmd_message(self, message):
        payload = self.decode_message(message)

        to_exec_command = self.commands.get(payload.get("value"))
        if to_exec_command is None:
            self.log.log_message(LogLevel.Warning(), 'received command not found')
            return False

        return to_exec_command()

    def _on_connect_handler(self, client, userdata, flags, conect_res):
        self.log.log_message(LogLevel.Info(), 'mqtt connected')
        self.connected_flag = True

        self.mqtt.subscribe(self._generate_base_topic_cmd())
        self.mqtt.subscribe(self._generate_base_topic_resource())
        self.mqtt.subscribe(self.__generate_sub_topic(TOPIC_CONTROLSTATUS))
        self.mqtt.subscribe(self.__generate_sub_topic(TOPIC_TESTRESULT))
        self.mqtt.subscribe(self.__generate_sub_topic(TOPIC_TESTSTATUS))
        self.mqtt.subscribe(self.__generate_sub_topic(TOPIC_TESTRESOURCE))
        self.mqtt.subscribe(self.__generate_sub_topic(TOPIC_TESTSUMMARY))
        self.status_consumer.startup_done()

    def _on_disconnect_handler(self, client, userdata, distc_res):
        self.log.log_message(LogLevel.Info(), f'mqtt disconnected (rc: {distc_res})')
        self.connected_flag = False

    def _generate_status_message(self, alive, state, statedict=None):
        message = {
            "type": "status",
            "alive": alive,
            "interface_version": INTERFACE_VERSION,
            "state": state,
        }
        if statedict is not None:
            message.update(statedict)
        return message

    def _generate_base_topic_status(self):
        return "ate/" + str(self.device_id) + "/Master/status"

    def _generate_io_control_result_message(self, periphery_name: str, result: dict):
        message = {
            'type': 'io-control-result',
            'periphery_type': periphery_name,
            'result': result
        }
        return message

    def _generate_resource_response_topic(self, resource_id: str):
        return self._generate_base_topic_resource + "/response"

    def _generate_usersettings_message(self, usersettings: dict):
        message = {
            "type": "usersettings",
        }
        message.update(usersettings)
        return message

    def _generate_topic_usersettings(self):
        return "ate/" + str(self.device_id) + "/Master/usersettings"

    def __generate_sub_topic(self, topic):
        return "ate/" + str(self.device_id) + "/" + topic + "/#"

    def _generate_base_topic_cmd(self):
        return "ate/" + str(self.device_id) + "/Master/cmd"

    def _generate_base_topic_resource(self):
        return "ate/" + str(self.device_id) + "/Master/peripherystate"

    def __extract_siteid_from_control_topic(self, topic):
        pat = rf'ate/{self.device_id}/{TOPIC_CONTROLSTATUS}/site(.+)$'
        m = re.match(pat, topic)
        if m:
            return m.group(1)

    def __extract_siteid_from_testapp_topic(self, topic):
        pat1 = rf'ate/{self.device_id}/TestApp/(?:status|testsummary)/site(.+)$'
        m = re.match(pat1, topic)
        if m:
            return m.group(1)
        pat = rf'ate/{self.device_id}/TestApp/(?:status|testresult)/site(.+)$'
        m = re.match(pat, topic)
        if m:
            return m.group(1)
        pat2 = rf'ate/{self.device_id}/TestApp/peripherystate/site(.+)/request$'
        m = re.match(pat2, topic)
        if m:
            return m.group(1)

    def dispatch_control_message(self, topic, msg):
        siteid = self.__extract_siteid_from_control_topic(topic)
        if siteid is None:
            self.log.log_message(LogLevel.Warning(), 'unexpected message on control topic ' /
                                 + f'"{topic}": extracting siteid failed')
            return

        if "status" in topic:
            self.status_consumer.on_control_status_changed(siteid, msg)
        else:
            assert False

    def dispatch_testapp_message(self, topic, msg):
        siteid = self.__extract_siteid_from_testapp_topic(topic)
        if siteid is None:
            self.log.log_message(LogLevel.Warning(), 'unexpected message on testapp topic ' /
                                 + f'"{topic}": extracting siteid failed')
            return

        if "testresult" in topic:
            self.status_consumer.on_testapp_testresult_changed(siteid, msg)
        elif "testsummary" in topic:
            self.status_consumer.on_testapp_testsummary_changed(msg)
        elif "peripherystate" in topic:
            assert 'type' in msg
            assert msg['type'] == 'io-control-request'
            assert 'periphery_type' in msg
            assert 'ioctl_name' in msg
            assert 'parameters' in msg
            self.status_consumer.on_testapp_resource_changed(siteid, msg)
        elif "status" in topic:
            self.status_consumer.on_testapp_status_changed(siteid, msg)
        else:
            assert False

    def _on_message_handler(self, client, userdata, msg):
        self.mqtt.router.inject_message(msg.topic, msg.payload)