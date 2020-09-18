""" master App """

# External imports
from aiohttp import web
from transitions.extensions import HierarchicalMachine as Machine
import asyncio
import mimetypes
import sys
import os

from typing import Callable

from ATE.semiateplugins.pluginmanager import get_plugin_manager

from ATE.TES.apps.common.Utils import LogLevel
from ATE.TES.apps.common.logger import Logger
from ATE.TES.apps.masterApp.master_connection_handler import MasterConnectionHandler
from ATE.TES.apps.masterApp.master_webservice import webservice_setup_app
from ATE.TES.apps.masterApp.parameter_parser import parser_factory
from ATE.TES.apps.masterApp.sequence_container import SequenceContainer
from ATE.TES.apps.masterApp.user_settings import UserSettings
from ATE.TES.apps.masterApp.utils import NonBlockingCall

from ATE.TES.apps.masterApp.resulthandling.stdf_aggregator import StdfTestResultAggregator
from ATE.TES.apps.masterApp.resulthandling.result_collection_handler import ResultsCollector

from ATE.TES.apps.masterApp.peripheral_controller import PeripheralController

from ATE.TES.apps.masterApp.statemachines.MultiSite import (MultiSiteTestingModel, MultiSiteTestingMachine)


INTERFACE_VERSION = 1
MAX_NUM_OF_TEST_PROGRAM_RESULTS = 1000


def assert_valid_system_mimetypes_config():
    """
    Perform sanity check for system/enfironment configuration and return
    result as boolean.

    Background info:

    aiohttp uses mimetypes.guess_type() to guess the content-type to be
    used in the http response headers when serving static files.

    If we serve javascript modules with "text/plain" instead
    of "application/javascript" the browser will not execute the file as
    javascript module and the angular frontend does not load.

    On windows mimetypes.init (called automatically by guess_type if not
    called before) will always read content types from registry in Python
    3.8.1 (e.g. "HKLM\\Software\\Classes\\.js\\Content Type"). The values
    stored there may not be standard because they have been changed on
    certain systems (reasons unknown).

    Apparently it was possible to avoid this in earlier python
    version by explicitly passing empty list to files, e.g.
    mimetypes.init(files=[]). But this does not work anymore in 3.8.1,
    where types from registry will always be loaded.
    """
    js_mime_type = mimetypes.guess_type('file.js')[0]
    if js_mime_type != 'application/javascript':
        print('FATAL ERROR: Invalid system configuration for .js type: '
              + 'expected "application/javascript" but got '
              + f'"{js_mime_type}".'
              + ' Please fix your systems mimetypes configuration.')
        sys.exit(1)


CONTROL_STATE_UNKNOWN = "unknown"
CONTROL_STATE_LOADING = "loading"
CONTROL_STATE_BUSY = "busy"
CONTROL_STATE_IDLE = "idle"
CONTROL_STATE_CRASH = "crash"

TEST_STATE_IDLE = "idle"
TEST_STATE_TESTING = "testing"
TEST_STATE_CRASH = "crash"
TEST_STATE_TERMINATED = "terminated"

STARTUP_TIMEOUT = 300
LOAD_TIMEOUT = 180
UNLOAD_TIMEOUT = 60
TEST_TIMEOUT = 30
RESET_TIMEOUT = 20


class MasterApplication(MultiSiteTestingModel):

    states = ['startup',
              'connecting',
              'initialized',
              'loading',
              'ready',
              {'name': 'testing', 'children': MultiSiteTestingMachine()},  # , 'remap': {'completed': 'ready'}
              'finished',
              'unloading',
              'error',
              'softerror']

    # multipe space code style "error" will be ignored for a better presentation of the possible state machine transitions
    transitions = [
        {'source': 'startup',           'dest': 'connecting',  'trigger': "startup_done",                'after': "on_startup_done"},               # noqa: E241
        {'source': 'connecting',        'dest': 'initialized', 'trigger': 'all_sites_detected',          'after': "on_allsitesdetected"},           # noqa: E241
        {'source': 'connecting',        'dest': 'error',       'trigger': 'bad_interface_version'},                                                 # noqa: E241

        {'source': 'initialized',       'dest': 'loading',     'trigger': 'load_command',                'after': 'on_loadcommand_issued'},         # noqa: E241
        {'source': 'loading',           'dest': 'ready',       'trigger': 'all_siteloads_complete',      'after': 'on_allsiteloads_complete'},      # noqa: E241
        {'source': 'loading',           'dest': 'initialized', 'trigger': 'load_error',                  'after': 'on_load_error'},                 # noqa: E241

        # TODO: properly limit source states to valid states where usersettings are allowed to be modified
        #       ATE-104 says it should not be possible while testing in case of stop-on-fail,
        #       but this constraint may not be required here and could be done in UI)
        {'source': ['initialized', 'ready'], 'dest': '=', 'trigger': 'usersettings_command',             'after': 'on_usersettings_command_issued'},  # noqa: E241

        {'source': 'ready',             'dest': 'testing',     'trigger': 'next',                        'after': 'on_next_command_issued'},        # noqa: E241
        {'source': 'ready',             'dest': 'unloading',   'trigger': 'unload',                      'after': 'on_unload_command_issued'},      # noqa: E241
        {'source': 'testing_completed', 'dest': 'ready',       'trigger': 'all_sitetests_complete',      'after': "on_allsitetestscomplete"},       # noqa: E241
        {'source': 'unloading',         'dest': 'initialized', 'trigger': 'all_siteunloads_complete',    'after': "on_allsiteunloadscomplete"},     # noqa: E241

        {'source': 'ready',             'dest': '=',           'trigger': 'getresults',                  'after': 'on_getresults_command'},         # noqa: E241
        {'source': '*',                 'dest': '=',           'trigger': 'getlogs',                     'after': 'on_getlogs_command'},            # noqa: E241
        {'source': '*',                 'dest': '=',           'trigger': 'getlogfile',                  'after': 'on_getlogfile_command'},         # noqa: E241

        {'source': '*',                 'dest': 'softerror',   'trigger': 'testapp_disconnected',        'after': 'on_disconnect_error'},           # noqa: E241
        {'source': '*',                 'dest': 'softerror',   'trigger': 'timeout',                     'after': 'on_timeout'},                    # noqa: E241
        {'source': '*',                 'dest': 'softerror',   'trigger': 'on_error',                    'after': 'on_error_occurred'},             # noqa: E241
        {'source': 'softerror',         'dest': 'connecting',  'trigger': 'reset',                       'after': 'on_reset_received'}              # noqa: E241

    ]

    """ MasterApplication """

    def __init__(self, configuration):
        sites = configuration['sites']
        super().__init__(sites)
        self.fsm = Machine(model=self,
                           states=MasterApplication.states,
                           transitions=MasterApplication.transitions,
                           initial="startup",
                           after_state_change='publish_state')
        self.configuration = configuration
        self.log = Logger('master')
        self.init(configuration)

        self.received_site_test_results = []
        self.received_sites_test_results = ResultsCollector(MAX_NUM_OF_TEST_PROGRAM_RESULTS)

        self.loaded_jobname = ""
        self.loaded_lot_number = ""
        self.error_message = ''
        self.logs = []
        self.prev_state = ''
        self.summary_counter = 0

        self._are_usersettings_required = True
        self._are_testresults_required = False
        self._is_logfile_required = False
        self._are_log_data_required = False
        self._is_status_reporting_required = True
        self._log_file_already_required = False
        self._log_file_information = None
        self._all_received_test_results = None
        self._log_information = None
        self.peripheral_controller = PeripheralController(get_plugin_manager(), self.connectionHandler)

    def _init_sites_result_collections(self, sites):
        for site in sites:
            self.received_sites_test_results[int(site)].set_size(300)

    def init(self, configuration: dict):
        self.__get_configuration(configuration)
        self.create_handler(self.broker_host, self.broker_port)

        self.siteStates = {site_id: CONTROL_STATE_UNKNOWN for site_id in self.configuredSites}
        self.pendingTransitionsControl = SequenceContainer([CONTROL_STATE_IDLE], self.configuredSites, lambda: self.all_sites_detected(),
                                                           lambda site, state: self.on_unexpected_control_state(site, state))
        self.pendingTransitionsTest = SequenceContainer([TEST_STATE_IDLE], self.configuredSites, lambda: self.all_siteloads_complete(),
                                                        lambda site, state: self.on_unexpected_testapp_state(site, state))
        self.init_user_settings()

        self.timeoutHandle = None
        self.arm_timeout(STARTUP_TIMEOUT, lambda: self.timeout("Not all sites connected."))

    def __get_configuration(self, configuration: dict):
        try:
            self.configuredSites = configuration['sites']
            # Sanity check for bad configurations:
            if len(self.configuredSites) == 0:
                self.log.log_message(LogLevel.Error(), 'Master got no sites assigned')
                sys.exit()

            self.device_id = configuration['device_id']
            self.broker_host = configuration['broker_host']
            self.broker_port = configuration['broker_port']
            self.enableTimeouts = configuration['enable_timeouts']
            self.env = configuration['environment']
        except KeyError as e:
            self.log.log_message(LogLevel.Error(), f'Master got invalid configuration: {e}')
            sys.exit()

    @property
    def user_settings_filepath(self):
        return self.configuration.get("user_settings_filepath")

    @property
    def persistent_user_settings_enabled(self):
        return self.user_settings_filepath is not None

    def init_user_settings(self):
        self.user_settings = self._load_usersettings()

    def _load_usersettings(self):
        if self.persistent_user_settings_enabled:
            try:
                user_settings = UserSettings.load_from_file(self.user_settings_filepath)
            except FileNotFoundError:
                user_settings = UserSettings.get_defaults()

            # always update file with hardcoded defaults (and create it if it does not exist)
            UserSettings.save_to_file(self.user_settings_filepath, user_settings, add_defaults=True)
        else:
            user_settings = UserSettings.get_defaults()

        return user_settings

    def modify_user_settings(self, settings):
        self.user_settings.update(self._extract_settings(settings))
        if self.persistent_user_settings_enabled:
            UserSettings.save_to_file(self.user_settings_filepath, self.user_settings, add_defaults=True)

        self._are_usersettings_required = True

    def _store_user_settings(self, settings):
        UserSettings.save_to_file(self.user_settings_filepath, settings, add_defaults=True)
        self.user_settings = settings
        self._are_usersettings_required = True

    def on_usersettings_command_issued(self, param_data: dict):
        settings = param_data['payload']
        self.modify_user_settings(settings)

    @staticmethod
    def _extract_settings(settings):
        modified_settings = UserSettings.get_defaults()
        for setting in settings:
            field = {setting['name']: {'active': setting['active'], 'value': int(setting['value']) if setting.get('value') else -1}}
            modified_settings.update(field)

        return modified_settings

    @property
    def external_state(self):
        return 'testing' if self.is_testing(allow_substates=True) else self.state

    def disarm_timeout(self):
        if self.enableTimeouts:
            if self.timeoutHandle is not None:
                self.timeoutHandle.cancel()
                self.timeoutHandle = None

    def arm_timeout(self, timeout_in_seconds: float, callback: Callable):
        if self.enableTimeouts:
            self.disarm_timeout()
            self.timeoutHandle = asyncio.get_event_loop().call_later(timeout_in_seconds, callback)

    def repost_state_if_connecting(self):
        # TODO: no reason to keep this ??
        if self.state == "connecting":
            self.publish_state()
            asyncio.get_event_loop().call_later(1, lambda: self.repost_state_if_connecting())

    def on_startup_done(self):
        self.repost_state_if_connecting()

    def on_timeout(self, message):
        self.error_message = message
        self.log.log_message(LogLevel.Error(), message)

    def on_disconnect_error(self, site_id, data):
        self.log.log_message(LogLevel.Error(), f'Master entered state error due to disconnect of site {site_id}')

    def on_unexpected_control_state(self, site_id, state):
        self.log.log_message(LogLevel.Warning(), f'Site {site_id} reported state {state}. This state is ignored during startup.')
        self.error_message = f'Site {site_id} reported state {state}'

    def on_unexpected_testapp_state(self, site_id, state):
        self.log.log_message(LogLevel.Warning(), f'TestApp for site {site_id} reported state {state}. This state is ignored during startup.')
        self.error_message = f'TestApp for site {site_id} reported state {state}'

    def on_error_occurred(self, message):
        self.log.log_message(LogLevel.Error(), f'Master entered state error, reason: {message}')
        self.error_message = message

    def on_load_error(self):
        self.log.log_message(LogLevel.Warning(), self.error_message)

    def on_allsitesdetected(self):
        # Trap any controls that misbehave and move out of the idle state.
        # In this case we want to move to error as well
        self.pendingTransitionsControl = SequenceContainer([CONTROL_STATE_IDLE], self.configuredSites, lambda: None,
                                                           lambda site, state: self.on_error(f"Bad statetransition of control {site} during sync to {state}"))

        self.error_message = ''
        self.disarm_timeout()

    def publish_state(self, site_id=None, param_data=None):
        if self.prev_state == self.state:
            return

        self.prev_state = self.state
        self.log.log_message(LogLevel.Info(), f'Master state is {self.state}')
        self.connectionHandler.publish_state(self.external_state)
        self._is_status_reporting_required = True

    def on_loadcommand_issued(self, param_data: dict):
        jobname = param_data['lot_number']
        self.loaded_jobname = str(jobname)

        # TODO: HACK for quick testing/development: allow to specify the
        # testappzip mock variant with the lot number, and use hardcoded variant by default,
        # so we dont have to modify the XML for now
        thetestzipname = 'sleepmock'  # use trivial zip mock implementation by default
        if isinstance(jobname, str) and '|' in jobname:
            jobname, thetestzipname = jobname.split('|')

        self.loaded_lot_number = str(jobname)

        jobformat = self.configuration.get('jobformat')
        parser = parser_factory.CreateParser(jobformat)
        source = parser_factory.CreateDataSource(jobname,
                                                 self.configuration,
                                                 parser,
                                                 self.log)

        if self.configuration.get('skip_jobdata_verification', False):
            data = {"DEBUG_OPTION": "no content because skip_jobdata_verification enabled"}
        else:
            param_data = source.retrieve_data()
            if param_data is None:
                self.error_message = "Failed to execute load command"
                self.load_error()
                return

            if not source.verify_data(param_data):
                self.error_message = "Malformed jobfile"
                self.load_error()
                return

            data = source.get_test_information(param_data)
            self.log.log_message(LogLevel.Debug(), f'testprogram information: {data}')

        self.arm_timeout(LOAD_TIMEOUT, lambda: self.timeout("not all sites loaded the testprogram"))
        self.pendingTransitionsControl = SequenceContainer([CONTROL_STATE_LOADING, CONTROL_STATE_BUSY], self.configuredSites, lambda: None,
                                                           lambda site, state: self.on_error(f"Bad statetransition of control {site} during load to {state}"))
        self.pendingTransitionsTest = SequenceContainer([TEST_STATE_IDLE], self.configuredSites, lambda: self.all_siteloads_complete(),
                                                        lambda site, state: self.on_error(f"Bad statetransition of testapp {site} during load to {state}"))
        self.error_message = ''

        self.connectionHandler.send_load_test_to_all_sites(self.get_test_parameters(data))
        self._store_user_settings(UserSettings.get_defaults())

    @staticmethod
    def get_test_parameters(data):
        # TODO: workaround until we specify the connection to the server or even mount the project locally
        from pathlib import Path
        import os
        return {
            'testapp_script_path': os.path.join(os.path.basename(os.fspath(Path(data['PROGRAM_DIR'])))),
            'testapp_script_args': ['--verbose', '--thetestzip_name', 'example1'],
            'cwd': os.path.dirname(data['PROGRAM_DIR']),
            'XML': data                                                                 # optional/unused for now
        }

    def on_allsiteloads_complete(self, paramData=None):
        self.error_message = ''
        self.disarm_timeout()

        self._stdf_aggregator = StdfTestResultAggregator(self.device_id + ".Master", self.loaded_lot_number, self.loaded_jobname)
        self._stdf_aggregator.write_header_records()

    def on_next_command_issued(self, paramData: dict):
        self.received_site_test_results = []
        self.arm_timeout(TEST_TIMEOUT, lambda: self.timeout("not all sites completed the active test"))
        self.pendingTransitionsTest = SequenceContainer([TEST_STATE_TESTING, TEST_STATE_IDLE], self.configuredSites, lambda: None,
                                                        lambda site, state: self.on_error(f"Bad statetransition of testapp during test"))
        self.error_message = ''
        self.connectionHandler.send_next_to_all_sites(self.user_settings)

    def on_unload_command_issued(self, param_data: dict):
        self.arm_timeout(UNLOAD_TIMEOUT, lambda: self.timeout("not all sites unloaded the testprogram"))
        self.pendingTransitionsControl = SequenceContainer([CONTROL_STATE_IDLE], self.configuredSites, lambda: self.all_siteunloads_complete(),
                                                           lambda site, state: self.on_error(f"Bad statetransition of control {site} during unload to {state}"))
        self.pendingTransitionsTest = SequenceContainer([TEST_STATE_TERMINATED], self.configuredSites, lambda: None, lambda site, state: None)
        self.error_message = ''
        self.connectionHandler.send_terminate_to_all_sites()

    def on_reset_received(self, param_data: dict):
        self.arm_timeout(RESET_TIMEOUT, lambda: self.timeout("not all sites unloaded the testprogram"))
        self.pendingTransitionsControl = SequenceContainer([CONTROL_STATE_IDLE], self.configuredSites, lambda: self.all_sites_detected(),
                                                           lambda site, state: self.on_unexpected_control_state(site, state))
        self.error_message = ''
        self.connectionHandler.send_reset_to_all_sites()

    def on_allsiteunloadscomplete(self):
        self.disarm_timeout()

        self.received_sites_test_results.clear()
        self.loaded_lot_number = ''

    def on_allsitetestscomplete(self):
        self.disarm_timeout()
        self.handle_reset()

    def on_site_test_result_received(self, site_id, param_data):
        self._write_stdf_data(param_data['payload'])
        self.received_site_test_results.append(param_data)
        self.received_sites_test_results.append(param_data['payload'])

    def _write_stdf_data(self, stdf_data):
        self._stdf_aggregator.append_test_results(stdf_data)

    def create_handler(self, host, port):
        self.connectionHandler = MasterConnectionHandler(host, port, self.configuredSites, self.device_id, self)

    def on_control_status_changed(self, siteid: str, status_msg: dict):
        newstatus = status_msg['state']

        if(status_msg['interface_version'] != INTERFACE_VERSION):
            self.log.log_message(LogLevel.Error(), f'Bad interface version on site {siteid}')
            self.bad_interface_version()

        try:
            if(self.siteStates[siteid] != newstatus):
                self.log.log_message(LogLevel.Info(), f'Control {siteid} state is {newstatus}')
                self.siteStates[siteid] = newstatus
                self.pendingTransitionsControl.trigger_transition(siteid, newstatus)
        except KeyError:
            self.on_error(f"Site id received: {siteid} is not configured")

    def on_testapp_status_changed(self, siteid: str, status_msg: dict):
        newstatus = status_msg['state']
        self.log.log_message(LogLevel.Info(), f'Testapp {siteid} state is {newstatus}')
        if self.is_testing(allow_substates=True) and newstatus == TEST_STATE_IDLE:
            self.handle_status_idle(siteid)

        self.pendingTransitionsTest.trigger_transition(siteid, newstatus)

    def on_testapp_testresult_changed(self, siteid: str, status_msg: dict):
        if self.is_testing(allow_substates=True):
            self.handle_testresult(siteid, status_msg)
            self.on_site_test_result_received(siteid, status_msg)
        else:
            self.on_error(f"Received unexpected testresult from site {siteid}")

    def on_testapp_testsummary_changed(self, status_msg: dict):
        self._stdf_aggregator.append_test_summary(status_msg['payload'])
        self.summary_counter += 1

        if self.summary_counter == len(self.configuredSites):
            self._stdf_aggregator.finalize()
            self._stdf_aggregator.write_footer_records()
            self._stdf_aggregator = None
            self.summary_counter = 0

    def on_testapp_resource_changed(self, siteid: str, resource_request_msg: dict):
        self.handle_resource_request(siteid, resource_request_msg)

    def on_peripheral_response_received(self, response):
        self.peripheral_controller.on_response_received(response)

    def apply_resource_config(self, resource_request: dict, on_resource_config_applied_callback: Callable):
        resource_id = resource_request['periphery_type']

        async def periphery_io_control_task():
            try:
                return_value = await self.periphery_io_control(resource_request)
                self.connectionHandler.publish_resource_config(resource_id, return_value)
                on_resource_config_applied_callback()
            except asyncio.TimeoutError:
                # Applying the resource configuration ran into a timeout.
                # We err-out in this case.
                self.on_error(f"Failed to control resource {resource_id}. Reason: Timeout.")

        asyncio.get_event_loop().create_task(periphery_io_control_task())

    async def periphery_io_control(self, resource_request):
        # ToDo: What if the requested resource is not available?
        resource_id = resource_request['periphery_type']
        ioctl_name = resource_request['ioctl_name']
        parameters = resource_request['parameters']
        return await self.peripheral_controller.device_io_control(resource_id, ioctl_name, parameters)

    def dispatch_command(self, json_data):
        cmd = json_data.get('command')
        try:
            {
                'load': lambda param_data: self.load_command(param_data),
                'next': lambda param_data: self.next(param_data),
                'unload': lambda param_data: self.unload(param_data),
                'reset': lambda param_data: self.reset(param_data),
                'usersettings': lambda param_data: self.usersettings_command(param_data),
                'getresults': lambda param_data: self.getresults(param_data),
                'getlogs': lambda param_data: self.getlogs(param_data),
                'getlogfile': lambda param_data: self.getlogfile(param_data),
            }[cmd](json_data)
        except Exception as e:
            self.log.log_message(LogLevel.Error(), f'Failed to execute command {cmd}: {e}')

    def on_getlogs_command(self, _):
        self._are_log_data_required = True

    def on_getresults_command(self, _):
        self._are_testresults_required = True

    def on_getlogfile_command(self, _):
        self._is_logfile_required = True

    async def _mqtt_loop_ctx(self, app):
        self.connectionHandler.start()
        app['mqtt_handler'] = self.connectionHandler  # TODO: temporarily exposed so websocket can publish

        yield

        app['mqtt_handler'] = None
        await self.connectionHandler.stop()

    def on_new_connection(self):
        self._are_usersettings_required = True
        self._is_status_reporting_required = True

    async def _master_background_task(self, app):
        try:
            while True:
                ws_comm_handler = app['ws_comm_handler']
                if ws_comm_handler is None:
                    await asyncio.sleep(1)
                    continue

                if not ws_comm_handler.is_ws_connection_established():
                    self.log.clear_logs()

                await self._handle_sending_data(ws_comm_handler)
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            pass

    async def _handle_sending_data(self, ws_comm_handler):
        if self._is_status_reporting_required:
            await ws_comm_handler.send_status_to_all(self.external_state, self.error_message)
            self._is_status_reporting_required = False

        # TODO: ATE-227, sync with UI Team
        for test_result in self.received_site_test_results:
            await ws_comm_handler.send_testresults_to_all(test_result)
            self.received_site_test_results = []

        if self._are_usersettings_required and self.user_settings:
            await ws_comm_handler.send_user_settings(self._generate_usersettings_message(self.user_settings))
            self._are_usersettings_required = False

        if self._are_testresults_required:
            NonBlockingCall(self._get_all_test_results)
            self._are_testresults_required = False

        if self._all_received_test_results:
            await ws_comm_handler.send_testresults_from_all_site(self._all_received_test_results)
            self._all_received_test_results = None

        if self._are_log_data_required:
            NonBlockingCall(self._get_log_information)
            self._are_log_data_required = False
        else:
            if self.log.are_logs_available():
                await ws_comm_handler.send_logs(self._generate_logs(self.log.get_current_logs()))

        if self._log_information:
            await ws_comm_handler.send_logs(self._log_information)
            self._log_information = None

        if self._is_logfile_required:
            NonBlockingCall(self._get_file_content)
            self._is_logfile_required = False

        if self._log_file_information:
            await ws_comm_handler.send_logfile(self._log_file_information)
            self._log_file_information = None

    def _get_file_content(self):
        self._log_file_information = self.log.get_log_file_information()

    def _get_all_test_results(self):
        self._all_received_test_results = list(self.received_sites_test_results.get_data())

    def _get_log_information(self):
        self._log_information = self._generate_logs(self.log.get_logs())

    @staticmethod
    def _generate_logs(logs):
        structured_logs = []
        for log in logs:
            line = log.split('|')
            structured_logs.append({'date': line[0], 'type': line[1], 'description': line[2].strip()})

        return structured_logs

    @staticmethod
    def _generate_usersettings_message(usersettings):
        settings = []
        for usersetting, value in usersettings.items():
            settings.append({'name': usersetting, 'active': value['active'], 'value': int(value['value'])})

        return settings

    async def _master_background_task_ctx(self, app):
        task = asyncio.create_task(self._master_background_task(app))

        yield

        task.cancel()
        await task

    def run(self):
        app = web.Application()
        app['master_app'] = self

        # initialize static file path from config (relative paths are interpreted
        # relative to the current working directory).
        # TODO: the default value of the static file path (here and config template) should
        #       not be based on the development folder structure and simply be './mini-sct-gui'.
        webui_static_path = self.configuration.get('webui_static_path', './src/ATE/ui/angular/mini-sct-gui/dist/mini-sct-gui')
        static_file_path = os.path.realpath(webui_static_path)

        webservice_setup_app(app, static_file_path)
        app.cleanup_ctx.append(self._mqtt_loop_ctx)
        app.cleanup_ctx.append(self._master_background_task_ctx)

        host = self.configuration.get('webui_host', 'localhost')
        port = self.configuration.get('webui_port', 8081)
        web.run_app(app, host=host, port=port)