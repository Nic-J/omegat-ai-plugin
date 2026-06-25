from unittest.mock import patch

import pytest
from pydantic_ai.models.test import TestModel

import translation.agent as translator
from config import Settings, get_settings
from main import app


@pytest.fixture(autouse=True)
def mock_external_apis():
    with translator.agent.override(model=TestModel(custom_output_text="(mocked ai translation)")):
        with patch("translation.prompt._load_style_rules", return_value=[]):
            yield


@pytest.fixture(autouse=True)
def isolate_state_db(tmp_path):
    """Default every test to a throwaway state DB so /translate's TM cache
    (and any other state writes) never touch the real on-disk state.db.
    Tests that need a specific Settings can still override get_settings
    themselves; dependency_overrides simply gets reassigned.
    """
    settings = Settings(state_db_path=tmp_path / "state.db")
    app.dependency_overrides[get_settings] = lambda: settings
    yield
    app.dependency_overrides.clear()
