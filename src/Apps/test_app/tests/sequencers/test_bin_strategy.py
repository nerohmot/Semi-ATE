import pytest
import os
from ate_test_app.sequencers.binning.BinStrategy import BinStrategy

CONFIG_FILE = os.path.join(os.path.dirname(__file__), 'binmapping.json')

BIN_TABLE = {'0': ['11', '12', '60002'], '1': ['1']}

NEW_HBIN1_BIN_TABLE = {'0': ['12', '60002'], '1': ['1'], '2': ['11']}
NEW_HBIN2_BIN_TABLE = {'0': ['12', '60002'], '1': ['1', '11']}


def test_read_bin_file_not_found():
    with pytest.raises(Exception):
        _ = BinStrategy('')


@pytest.fixture
def bin_strategy():
    return BinStrategy(CONFIG_FILE)


def test_map_soft_bin_to_hard_bin(bin_strategy: BinStrategy):
    assert bin_strategy.get_hard_bin(1) == 1


def test_generate_bin_table(bin_strategy: BinStrategy):
    assert bin_strategy.bin_mapping == BIN_TABLE


def test_set_new_hbin(bin_strategy: BinStrategy):
    bin_strategy.set_new_hbin(11, 2)
    assert bin_strategy.bin_mapping == NEW_HBIN1_BIN_TABLE


def test_set_new_hbin_but_hbin_exists_already(bin_strategy: BinStrategy):
    bin_strategy.set_new_hbin(11, 1)
    assert bin_strategy.bin_mapping == NEW_HBIN2_BIN_TABLE
