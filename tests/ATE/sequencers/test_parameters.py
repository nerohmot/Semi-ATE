import pytest

from ATE.sequencers.TestParameters import InputParameter, OutputParameter
from ATE.sequencers.Result import Result


def test_can_create_input_parameter():
    ip = InputParameter("Test", False, 100.0)
    assert(ip is not None)


def test_getter_will_return_value():
    ip = InputParameter("Test", False, 117.0)
    assert(ip() == 117.0)


def test_can_create_output_parameter():
    op = OutputParameter("Op", 0, 10, 20, 30, 40)
    assert(op is not None)


def test_can_write_output_parameter():
    op = OutputParameter("Op", 0, 10, 20, 30, 40)
    op.write(25)
    assert(op._measurement == 25)


def test_output_parameter_is_pass_yields_false_if_out_of_spec():
    op = OutputParameter("Op", 0, 10, 20, 30, 40)
    op.set_bin(0, Result.Fail())
    op.write(-5)
    assert(op.get_testresult()[0] is Result.Fail())
    op.write(45)
    assert(op.get_testresult()[0] is Result.Fail())


def test_output_parameter_is_pass_yields_false_if_out_of_testlimit():
    op = OutputParameter("Op", 0, 10, 20, 30, 40)
    op.set_bin(0, Result.Fail())
    op.write(9)
    assert(op.get_testresult()[0] is Result.Fail())
    op.write(31)
    assert(op.get_testresult()[0] is Result.Fail())


def test_output_parameter_is_pass_yields_true_if_within_testlimit():
    op = OutputParameter("Op", 0, 10, 20, 30, 40)
    op.set_bin(0, Result.Fail())
    op.write(25)
    assert(op.get_testresult()[0] is Result.Pass())


def test_set_parameters_throws_exception_if_ltl_larger_than_utl():
    with pytest.raises(ValueError):
        op = OutputParameter("Op", 0, 10, 20, 30, 40)
        op.set_limits(0, 30, 20)


def test_set_parameters_throws_exception_if_lowerlimit_violates_specs():
    with pytest.raises(ValueError):
        op = OutputParameter("Op", 0, 10, 20, 30, 40)
        op.set_limits(0, -10, 20)


def test_set_parameters_throws_exception_if_upperlimit_violates_specs():
    with pytest.raises(ValueError):
        op = OutputParameter("Op", 0, 10, 20, 30, 40)
        op.set_limits(0, 10, 110)


def test_output_parameter_with_no_limit_returns_inconclusive():
    import numpy as np
    op = OutputParameter("Op", np.NaN, - np.inf, 0, np.NaN, np.inf)
    op.set_bin(0, Result.Fail())
    op.write(25)
    assert(op.get_testresult()[0] is Result.Inconclusive())


def test_set_test_description():
    op = OutputParameter("Op", 0, 10, 20, 30, 40)
    op.set_test_description('test_1')
    assert(op._test_description == 'test_1')
    assert(op._get_PTR_test_name() == 'test_1.Op')


def test_generate_empty_tsr():
    op = OutputParameter("Op", 0, 10, 20, 30, 40)
    record = op.generate_tsr_record(1, 1, -1)
    assert(record['EXEC_CNT'] == 0)
    assert(record['TEST_TIM'] == -1)


def test_generate_tsr():
    op = OutputParameter("Op", 0, 10, 20, 30, 40)
    op.write(1.0)
    op.get_testresult()

    op.write(1.0)
    op.get_testresult()
    record = op.generate_tsr_record(1, 1, 2.0)

    assert(record['EXEC_CNT'] == 2)
    assert(record['TEST_TIM'] == 2.0)