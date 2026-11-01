from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from test_workbench import client as client, upstream as upstream, environment, submit, finish
from api_post.storage import Store


def case(client, name="example fixture"):
    response = client.post("/api/cases", json={"request": {"name": name, "path": "/echo"}})
    assert response.status_code == 200
    return response.json()


def test_examples_persist_deduplicate_and_keep_full_body(client, upstream):
    c = case(client)
    run = finish(client, submit(client, environment(client, upstream), case_id=c["id"],
                               headers={"X-Test-Header": "keep"},
                               body='{"token":"private-fixture-secret","message":"example"}')["id"])
    base = "/api/cases/" + c["id"] + "/examples"
    assert client.get(base).json() == []
    saved = client.post(base, json={"run_id": run["id"]})
    assert saved.status_code == 200, saved.text
    item = saved.json()
    assert client.post(base, json={"run_id": run["id"]}).json()["id"] == item["id"]
    assert len(client.get(base).json()) == 1
    detail = client.get(base + "/" + item["id"]).json()["run"]
    assert "private-fixture-secret" not in str(detail)
    assert "example" in detail["response_preview"]
    assert detail["response_headers"] and detail["request"]["case_id"] == c["id"]
    # Editing a case does not rewrite historical results or their association.
    assert client.put("/api/cases/"+c["id"], json={"revision":c["revision"],
        "request":{**c["request"], "name":"edited example fixture"}}).status_code == 200
    body = "full-body-start" + "x" * 300000 + "full-body-end"
    (client.app.state.store.path("runs", run["id"])/"response.body.txt").write_text(body, encoding="utf-8")
    assert client.get(base+"/"+item["id"]).json()["run"]["response_preview"] == body
    reopened = Store(client.app.state.store.root)
    try:
        assert reopened.response_examples(c["id"])[0]["id"] == item["id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            assert len({v["id"] for v in pool.map(lambda _: reopened.save_response_example(c["id"],run["id"]),range(8))}) == 1
    finally:
        reopened.close()
    assert len(client.get(base).json()) == 1


def test_examples_validate_ownership_state_and_auth(client, upstream):
    c, other = case(client), case(client,"other fixture")
    env = environment(client, upstream)
    run = finish(client, submit(client, env, case_id=c["id"],path="/sse",stream=True)["id"])
    base = "/api/cases/"+c["id"]+"/examples"
    assert client.post("/api/cases/"+other["id"]+"/examples",json={"run_id":run["id"]}).status_code == 400
    item = client.post(base,json={"run_id":run["id"]}).json()
    assert client.get(base+"/"+item["id"]).json()["run"]["is_sse"]
    assert client.get("/api/cases/"+other["id"]+"/examples/"+item["id"]).status_code == 404
    assert client.get(base,headers={"Authorization":""}).status_code == 401
    assert client.post(base,json={"run_id":run["id"]},headers={"Authorization":""}).status_code == 401
    assert client.get(base+"/"+item["id"],headers={"Authorization":""}).status_code == 401
    assert client.post(base,json={"run_id":"../../x"}).status_code == 422
    assert client.post(base,json={"run_id":str(uuid4())}).status_code == 404
    store = client.app.state.store
    store.update_run({**store.run(run["id"]),"state":"running"})
    assert client.post(base,json={"run_id":run["id"]}).status_code == 400
    store.update_run({**store.run(run["id"]),"state":"failed","status_code":None})
    (store.path("runs",run["id"])/"stream.events.ndjson").unlink()
    assert client.post(base,json={"run_id":run["id"]}).status_code == 400
