from unittest.mock import MagicMock

from tests.test_example_security import _load_example


def test_startup_recovers_disputes_in_the_current_window():
    watch = _load_example("voter/watch.py")
    client = MagicMock()
    client.w3.eth.block_number = 1000
    client.w3.eth.get_block.return_value = {"timestamp": 2000}
    client.policy.dispute_window.return_value = 600
    client._first_block_at_or_after.return_value = 700
    state = watch.initial_watch_state(client)
    client._first_block_at_or_after.assert_called_once_with(1400, 1000)
    assert state.last_block == 699


def test_startup_includes_genesis_when_it_is_inside_the_window():
    watch = _load_example("voter/watch.py")
    client = MagicMock()
    client.w3.eth.block_number = 1
    client.w3.eth.get_block.return_value = {"timestamp": 100}
    client.policy.dispute_window.return_value = 600
    client._first_block_at_or_after.return_value = 0
    assert watch.initial_watch_state(client).last_block == -1
