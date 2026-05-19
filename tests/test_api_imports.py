from __future__ import annotations



def test_stable_api_imports_are_available():
    from wangchuan import Memory
    from wangchuan import remember, recall, recall_raw, recall_scars
    from wangchuan import status, healthcheck, task_resume
    from wangchuan.facade import version, health, capabilities, invoke

    assert Memory.__name__ == "Memory"
    assert callable(remember)
    assert callable(recall)
    assert callable(recall_raw)
    assert callable(recall_scars)
    assert callable(status)
    assert callable(healthcheck)
    assert callable(task_resume)
    assert callable(version)
    assert callable(health)
    assert callable(capabilities)
    assert callable(invoke)
