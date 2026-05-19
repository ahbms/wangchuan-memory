from __future__ import annotations


def _reset_memory_singleton() -> None:
    import wangchuan.memory_api as memory_api

    memory_api._memory = None



def test_remember_and_recall_cover_fact_preference_and_lesson(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))
    _reset_memory_singleton()

    from wangchuan import Memory

    memory = Memory()

    fact = memory.remember("用户住在石家庄", importance=0.6, tags=["fact"])
    preference = memory.remember("用户偏好简洁回复", importance=0.9, tags=["preference"])
    lesson = memory.remember_lesson({"content": "配置变更前先做验证", "status": "active"})

    assert fact["success"] is True
    assert preference["success"] is True
    assert lesson["success"] is True

    fact_results = memory.recall("石家庄", limit=5)
    assert fact_results
    assert any("石家庄" in item["content"] for item in fact_results)
    assert all("recall_explain" in item for item in fact_results)

    preference_results = memory.recall("简洁回复", limit=5)
    assert preference_results
    assert preference_results[0]["content"] == "用户偏好简洁回复"
    assert "recall_explain" in preference_results[0]

    lesson_results = memory.recall_scars("配置变更 验证", limit=5)
    assert lesson_results
    assert any("配置变更前先做验证" in item["content"] for item in lesson_results)
    assert all("recall_explain" in item for item in lesson_results)



def test_recall_limit_and_empty_result_behavior(tmp_path, monkeypatch):
    monkeypatch.setenv("WANGCHUAN_HOME", str(tmp_path))
    _reset_memory_singleton()

    from wangchuan import Memory

    memory = Memory()
    for idx in range(5):
        written = memory.remember(
            f"用户喜欢的饮品 {idx}：冰美式",
            importance=0.7,
            tags=["preference", f"drink-{idx}"],
        )
        assert written["success"] is True

    limited = memory.recall("冰美式", limit=2)
    assert isinstance(limited, list)
    assert len(limited) == 2
    assert all("recall_explain" in item for item in limited)

    assert memory.recall("火星坐标紫外线协议", limit=3) == []
