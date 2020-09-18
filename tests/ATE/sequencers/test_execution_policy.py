import pytest

from ATE.sequencers import SequencerBase
from ATE.sequencers.DutTestCaseABC import DutTestCaseBase
from ATE.sequencers.ExecutionPolicy import SingleShotExecutionPolicy


@pytest.fixture
def sequencer():
    return CbCountingSequencer()


class BooleanTest(DutTestCaseBase):
    def __init__(self, returnValue=True):
        self.returnValue = returnValue
        self.ran = False

    def run(self, site_num):
        self.ran = self.returnValue
        return (self.returnValue, 0, [])

    def aggregate_test_result(self, site_num):
        pass

    def do(self):
        pass


class CbCountingSequencer(SequencerBase):
    def __init__(self):
        super().__init__()
        self.aftertest_calls = 0
        self.aftercycle_calls = 0

    def after_test_cb(self, test_index, test_result):
        self.aftertest_calls += 1
        return test_result[0]

    def after_cycle_cb(self, execution_time, test_index, test_result):
        self.aftercycle_calls += 1


def test_single_shot_calls_all_tests(sequencer):
    test1 = BooleanTest()
    test2 = BooleanTest()
    sequencer.register_test(test1)
    sequencer.register_test(test2)
    sequencer.run(SingleShotExecutionPolicy())
    assert(test1.ran)
    assert(test2.ran)


def test_single_shot_calls_callbacks_for_each_test(sequencer):
    test1 = BooleanTest()
    test2 = BooleanTest()
    sequencer.register_test(test1)
    sequencer.register_test(test2)
    sequencer.run(SingleShotExecutionPolicy())
    assert(sequencer.aftertest_calls == 2)


def test_sequencer_aborts_execution_if_after_test_cb_yields_False(sequencer):
    test1 = BooleanTest(False)
    test2 = BooleanTest(False)
    sequencer.register_test(test1)
    sequencer.register_test(test2)
    sequencer.run(SingleShotExecutionPolicy())
    assert(test1.ran is False)
    assert(sequencer.aftertest_calls == 1)