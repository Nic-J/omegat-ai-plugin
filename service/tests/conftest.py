from unittest.mock import patch

import pytest
from pydantic_ai.models.test import TestModel

import translation.agent as translator


@pytest.fixture(autouse=True)
def mock_external_apis():
    with translator.agent.override(model=TestModel(custom_output_text="(mocked ai translation)")):
        with patch("translation.prompt._load_style_rules", return_value=[]):
            yield
