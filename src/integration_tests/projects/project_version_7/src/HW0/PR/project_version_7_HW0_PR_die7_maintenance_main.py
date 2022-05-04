
from math import inf, nan
import common
import sys
import os
from ate_test_app.sequencers.SequencerBase import SequencerBase
from ate_test_app.sequencers.CommandLineParser import CommandLineParser
from ate_test_app.sequencers.binning.BinStrategyFactory import create_bin_strategy
from ate_test_app.stages_sequence_generator.stages_sequence_generator import StagesSequenceGenerator
from ate_test_app.sequencers.mqtt.MqttConnection import MqttConnection
from ate_test_app.sequencers.harness.HarnessFactory import create_harness


if __name__ == '__main__':
    params = CommandLineParser(sys.argv)
    test_program_name, _ = os.path.splitext(__file__)
    bin_table_name = f'{test_program_name}_binning.json'
    bin_table_path = os.path.join(os.path.dirname(__file__), bin_table_name)

    execution_strategy_name = f'{test_program_name}_execution_strategy.json'
    execution_strategy_path = os.path.join(os.path.dirname(__file__), execution_strategy_name)

    execution_strategy = StagesSequenceGenerator(execution_strategy_path)
    bin_strategy = create_bin_strategy(params.strategytype, bin_table_path, test_program_name)

    program_name = os.path.basename(__file__).replace(".py", "")
    sequencer = SequencerBase(program_name, bin_strategy)

    from project_version_7_HW0_PR_die7_maintenance_main_auto_script import AutoScript
    auto_script = AutoScript()
    source = f"TestApp{params.site_id}"

    mqtt = MqttConnection(params)
    harness_strategy = create_harness(params.strategytype, mqtt.get_mqtt_client(), program_name)
    context = common.make_context(source, params, sequencer, auto_script, execution_strategy, mqtt, harness_strategy)

    from t7_1.t7_1 import t7_1
    _ate_var_t7_1_1 = t7_1("t7_1_1", 60000, 100, context)
    _ate_var_t7_1_1.ip.set_parameter('Temperature', 'static', 25, -40.0, 170.0, 0, context, True)
    _ate_var_t7_1_1.ip.set_parameter('in_1', 'static', 10.0, 10.0, 100.0, -3, context, False)
    _ate_var_t7_1_1.op.set_parameter('out_1', 101, -50.0, 50.0, 11, 2, 't7_1_1')
    _ate_var_t7_1_1.op.set_parameter('out_2', 102, -100.0, 0.0, 11, 2, 't7_1_1')
    sequencer.register_test(_ate_var_t7_1_1)


    # Start MQTT using the sequencer.
    # Note that "run()" will
    # only return when the program should terminate.
    context.harness.run()
    context.get_logger().cleanup()