# Test Suite

Test cases for ERC8004Agent SDK based on `examples/basic_usage.py`.

## Installation

Install test dependencies:

```bash
# Using uv (recommended)
uv sync --extra dev
```

## Running Tests

Run all tests:

```bash
pytest
```

Run specific test file:

```bash
pytest tests/test_sdk.py
pytest tests/test_agent_uri.py
pytest tests/test_models.py
pytest tests/test_network.py
```

Run with verbose output:

```bash
pytest -v
```

## Test Coverage

The test suite covers:

- **test_sdk.py**: SDK initialization, agent registration, metadata operations, agent URI parsing
- **test_agent_uri.py**: Agent URI generation and parsing utilities
- **test_models.py**: AgentEndpoint model validation and conversion
- **test_network.py**: Network configuration and utilities

## Test Structure

Tests use `pytest` with `unittest.mock` for mocking blockchain interactions, so tests can run without requiring a live blockchain connection.
