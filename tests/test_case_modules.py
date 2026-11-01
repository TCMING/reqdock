from test_workbench import client as client


def case(client, name, group):
    response = client.post(
        "/api/cases",
        json={"group": group, "request": {"name": name, "path": "/echo", "body": "{}"}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_application_boundaries_base_prompt_gpt_aliases_and_descendants(client):
    a = case(client, "base veo", "ibrain接口测试/base/视频生成/veo")
    case(client, "base seedance", "ibrain接口测试/base/视频生成/seedance")
    case(client, "base direct", "ibrain接口测试/base")
    case(client, "base chat", "ibrain接口测试/base/openai对话")
    case(client, "gpt A", "ibrain接口测试/GPT工程/模型A")
    case(client, "gpt B", "Ibrain-GPT-Backend模块/模型B")
    case(client, "prompt veo", "ibrain接口测试/Prompt工程/视频生成/veo")
    base = client.get("/api/cases", params={"module": "base"}).json()
    assert base["total"] == 4
    assert all(m["name"] != "Ibrain-GPT-Backend模块" for m in base["modules"])
    assert next(m for m in base["modules"] if m["id"] == "gpt")["count"] == 2
    assert client.get("/api/cases", params={"module": "gpt"}).json()["total"] == 2
    assert client.get("/api/cases", params={"module": "prompt"}).json()["total"] == 1
    assert base["group_labels"]["ibrain接口测试/base/视频生成"] == "视频生成"
    assert base["group_labels"]["ibrain接口测试/base"] == "未分类"
    video = client.get(
        "/api/cases", params={"module": "base", "group": "ibrain接口测试/base/视频生成"}
    ).json()
    assert video["total"] == 2
    assert (
        client.get("/api/cases", params={"module": "base", "group": "视频生成"}).json()[
            "total"
        ]
        == 2
    )
    assert (
        client.get(
            "/api/cases", params={"module": "base", "group": "ibrain接口测试/base"}
        ).json()["total"]
        == 1
    )
    assert (
        client.get("/api/cases", params={"module": "root:ibrain接口测试"}).json()[
            "total"
        ]
        == 0
    )
    assert client.get("/api/cases/" + a["id"]).json()["group"] == a["group"]
    assert all(item["modules"][0]["name"] == "base" for item in base["items"])


def test_module_counts_deduplicate_multigroup_cases_and_match_literal_names(client):
    a = case(client, "multiple models", "ibrain接口测试/base/视频生成/veo")
    # Index fixture with multiple model folders belonging to the same application.
    store = client.app.state.store
    store.index_case(
        {
            **a,
            "groups": [
                "ibrain接口测试/base/视频生成/veo",
                "ibrain接口测试/base/视频生成/seedance",
                "Ibrain-GPT-Backend模块/模型A",
            ],
        }
    )
    case(client, "literal", "literal%_/模型")
    case(client, "different", "literalXY/模型")
    result = client.get("/api/cases", params={"module": "base", "limit": 1}).json()
    assert result["total"] == 1 and len(result["items"]) == 1
    assert next(m for m in result["modules"] if m["id"] == "base")["count"] == 1
    assert (
        client.get(
            "/api/cases",
            params={"module": "base", "group": "ibrain接口测试/base/视频生成"},
        ).json()["total"]
        == 1
    )
    assert client.get("/api/cases", params={"module": "literal%_"}).json()["total"] == 1
    assert (
        client.get("/api/cases", params={"module": "root:闲时调度器"}).json()["total"]
        == 0
    )
    assert client.get("/api/cases", params={"module": "nonexistent"}).status_code == 400
