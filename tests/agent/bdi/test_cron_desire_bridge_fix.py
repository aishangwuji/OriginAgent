"""Tests for CronDesireBridge priority fix."""
import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Any

from OriginAgent.bdi.cron_desire_bridge import CronDesireBridge
from OriginAgent.bdi.desire_store import DesireStore
from OriginAgent.bdi.models import DesirePriority, DesireStatus


@dataclass
class FakeSchedule:
    kind: str = "at"
    at_ms: int = 0


@dataclass 
class FakePayload:
    message: str = "test message"


@dataclass
class FakeJob:
    id: str = "job1"
    name: str = "test job"
    payload: FakePayload = field(default_factory=FakePayload)
    schedule: FakeSchedule = field(default_factory=FakeSchedule)


class TestCronDesireBridgePriority:
    @pytest.fixture
    def store(self):
        with tempfile.TemporaryDirectory() as td:
            yield DesireStore(Path(td))

    @pytest.fixture
    def observation_store(self):
        mock = MagicMock()
        mock.get_link_by_job_id.return_value = None
        mock.save_link.return_value = True
        return mock

    def test_creates_desire_with_medium_priority(self, store, observation_store):
        """on_cron_job_created creates Desire with MEDIUM priority, not NORMAL."""
        bridge = CronDesireBridge(store, observation_store, enabled=True)
        job = FakeJob()
        
        desire_id = bridge.on_cron_job_created(job, session_key="sess", owner_id="user")
        
        assert desire_id == "cron:job1"
        desire = store.get("cron:job1")
        assert desire is not None
        assert desire.priority == DesirePriority.MEDIUM

    def test_no_attribute_error(self, store, observation_store):
        """on_cron_job_created does not raise AttributeError."""
        bridge = CronDesireBridge(store, observation_store, enabled=True)
        job = FakeJob()
        
        # Should not raise AttributeError
        bridge.on_cron_job_created(job, session_key="sess", owner_id="user")
