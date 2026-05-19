from __future__ import annotations

import os
from pathlib import Path

from wangchuan._protocol.layer_contract import LayerCapability, LayerHealth, LayerRequest, LayerResponse
from wangchuan.facade import capabilities, health, invoke, version


def _set_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))



def test_facade_version_is_stable():
    assert version() == "3.0.0"



def test_facade_health_returns_layer_health(tmp_path, monkeypatch):
    _set_home(tmp_path, monkeypatch)

    payload = health()

    assert isinstance(payload, LayerHealth)
    data = payload.to_dict()
    assert data["layer"] == "wangchuan"
    assert data["version"] == "3.0.0"
    assert isinstance(data["ok"], bool)
    assert data["status"]
    assert Path(data["checks"]["db_path"]).name == "index.sqlite"
    assert Path(data["checks"]["state_root"]).parts[-2:] == ("state", "wangchuan")
    assert "user_healthcheck_status" in data["checks"]



def test_facade_capabilities_include_stable_operations():
    payload = capabilities()

    assert isinstance(payload, LayerCapability)
    data = payload.to_dict()
    assert data["layer"] == "wangchuan"
    assert data["version"] == "3.0.0"
    assert set([
        "remember",
        "recall",
        "recall_raw",
        "recall_scars",
        "status",
        "healthcheck",
        "task_resume",
        "paths",
    ]).issubset(set(data["stable"]))
    assert set(data["stable"]).issubset(set(data["operations"]))
    assert "wangchuan.v3.*" in data["internal_only"]



def test_facade_invoke_recall_returns_structured_response(tmp_path, monkeypatch):
    _set_home(tmp_path, monkeypatch)

    request = LayerRequest(
        layer="wangchuan",
        operation="recall",
        payload={"query": "冰美式", "limit": 3},
        trace_id="trace-recall",
    )

    response = invoke(request)

    assert isinstance(response, LayerResponse)
    data = response.to_dict()
    assert data["layer"] == "wangchuan"
    assert data["operation"] == "recall"
    assert data["ok"] is True
    assert data["error"] is None
    assert data["trace_id"] == "trace-recall"
    assert isinstance(data["data"]["items"], list)
    assert "stable_operations" in data["metadata"]
    assert "recall" in data["metadata"]["stable_operations"]



def test_facade_unsupported_operation_returns_structured_error(tmp_path, monkeypatch):
    _set_home(tmp_path, monkeypatch)

    request = LayerRequest(
        layer="wangchuan",
        operation="bad_op",
        trace_id="trace-bad-op",
    )

    response = invoke(request)

    assert isinstance(response, LayerResponse)
    data = response.to_dict()
    assert data["ok"] is False
    assert data["operation"] == "bad_op"
    assert data["trace_id"] == "trace-bad-op"
    assert data["error"]["code"] == "unsupported_operation"
    assert "supported" in data["error"]["details"]
    assert "recall" in data["error"]["details"]["supported"]



def test_facade_layer_mismatch_returns_structured_error(tmp_path, monkeypatch):
    _set_home(tmp_path, monkeypatch)

    request = LayerRequest(
        layer="not-wangchuan",
        operation="recall",
        payload={"query": "冰美式"},
        trace_id="trace-mismatch",
    )

    response = invoke(request)

    data = response.to_dict()
    assert data["ok"] is False
    assert data["error"]["code"] == "layer_mismatch"
    assert data["trace_id"] == "trace-mismatch"
