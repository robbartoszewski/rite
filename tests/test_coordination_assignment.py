"""P2-3a: the Owner reads Manager status and assigns by label."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from rite_ai.coordination.assignment import (
    Assigned,
    NotAssigned,
    assign_to_manager,
    choose_manager,
    manager_views,
)
from rite_ai.coordination.heartbeat import publish_heartbeat, status_key
from rite_ai.coordination.local_backend import LocalStateLayer
from rite_ai.tickets import BackendError

NOW = datetime(2026, 9, 17, 12, 0, tzinfo=UTC)
WINDOW = dict(interval_minutes=10, stall_threshold=3)


def _layer(tmp_path: Path) -> LocalStateLayer:
    return LocalStateLayer(tmp_path / "state")


def _heartbeat(layer, name, in_flight, minutes_ago=0, workers=("w1",)):
    publish_heartbeat(
        layer,
        name,
        workers=list(workers),
        in_flight=in_flight,
        now=NOW - timedelta(minutes=minutes_ago),
    )


def test_the_least_loaded_manager_is_chosen(tmp_path):
    layer = _layer(tmp_path)
    _heartbeat(layer, "manager-alpha", in_flight=3)
    _heartbeat(layer, "manager-beta", in_flight=1)
    views = manager_views(layer, ["manager-alpha", "manager-beta"], now=NOW, **WINDOW)
    assert choose_manager(views).name == "manager-beta"


def test_a_tie_goes_to_config_order_not_to_a_timestamp(tmp_path):
    layer = _layer(tmp_path)
    _heartbeat(layer, "manager-beta", in_flight=2, minutes_ago=1)
    _heartbeat(layer, "manager-alpha", in_flight=2, minutes_ago=0)
    views = manager_views(layer, ["manager-alpha", "manager-beta"], now=NOW, **WINDOW)
    assert choose_manager(views).name == "manager-alpha"


def test_a_stalled_manager_is_never_chosen_and_says_why(tmp_path):
    layer = _layer(tmp_path)
    _heartbeat(layer, "manager-alpha", in_flight=9, minutes_ago=0)
    _heartbeat(layer, "manager-beta", in_flight=0, minutes_ago=45)
    views = manager_views(layer, ["manager-alpha", "manager-beta"], now=NOW, **WINDOW)
    assert choose_manager(views).name == "manager-alpha"
    beta = next(v for v in views if v.name == "manager-beta")
    assert not beta.assignable and "stalled" in beta.why_not


def test_a_manager_that_never_published_is_not_assumed_idle(tmp_path):
    layer = _layer(tmp_path)
    views = manager_views(layer, ["manager-alpha"], now=NOW, **WINDOW)
    assert choose_manager(views) is None
    assert "cannot tell" in views[0].why_not


def test_unreadable_status_is_not_assignable(tmp_path):
    layer = _layer(tmp_path)
    _heartbeat(layer, "manager-alpha", in_flight=0)
    read = layer.read_state(status_key("manager-alpha"))
    layer.write_state(status_key("manager-alpha"), b"{ truncated", read.version)
    views = manager_views(layer, ["manager-alpha"], now=NOW, **WINDOW)
    assert not views[0].assignable and choose_manager(views) is None


def test_assignment_is_a_label_on_the_ticket(tmp_path):
    layer = _layer(tmp_path)
    _heartbeat(layer, "manager-beta", in_flight=0)
    views = manager_views(layer, ["manager-beta"], now=NOW, **WINDOW)
    backend = MagicMock()
    backend.label.return_value = None
    result = assign_to_manager(backend, "ABC-12", views)
    assert isinstance(result, Assigned) and result.manager == "manager-beta"
    backend.label.assert_called_once_with("ABC-12", ["manager-beta"])


def test_a_backend_that_refuses_leaves_it_unassigned(tmp_path):
    layer = _layer(tmp_path)
    _heartbeat(layer, "manager-beta", in_flight=0)
    views = manager_views(layer, ["manager-beta"], now=NOW, **WINDOW)
    backend = MagicMock()
    backend.label.return_value = BackendError("JIRA said no")
    result = assign_to_manager(backend, "ABC-12", views)
    assert isinstance(result, NotAssigned) and "JIRA said no" in result.reason


def test_with_nobody_assignable_it_says_why_per_manager(tmp_path):
    layer = _layer(tmp_path)
    _heartbeat(layer, "manager-beta", in_flight=0, minutes_ago=60)
    views = manager_views(layer, ["manager-alpha", "manager-beta"], now=NOW, **WINDOW)
    backend = MagicMock()
    result = assign_to_manager(backend, "ABC-12", views)
    assert isinstance(result, NotAssigned)
    assert set(result.per_manager) == {"manager-alpha", "manager-beta"}
    backend.label.assert_not_called()
