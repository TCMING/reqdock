import base64
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from test_workbench import (
    client as client,
    upstream as upstream,
    environment,
    submit,
    finish,
)
from api_post.resources import inject

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/lS8AAAAASUVORK5CYII="
)


def resource(client, **extra):
    r = client.post(
        "/api/resources", json={"name": "fixture image", "kind": "image", **extra}
    )
    assert r.status_code == 200, r.text
    return r.json()


def upload(client, r, content=PNG, filename="fixture.png"):
    return client.post(
        f"/api/resources/{r['id']}/file",
        params={"revision": r["revision"], "filename": filename},
        content=content,
        headers={"Content-Type": "application/octet-stream"},
    )


def test_files_versions_range_archive_and_dedup(client):
    a = resource(client)
    b = upload(client, a)
    assert b.status_code == 200, b.text
    b = b.json()
    assert b["revision"] == 2 and b["file"]["mime"] == "image/png"
    assert client.get(f"/api/resources/{a['id']}?revision=1").json()["file"] is None
    r = client.get(
        f"/api/resources/{a['id']}/file?revision=2", headers={"Range": "bytes=0-7"}
    )
    assert r.status_code == 206 and r.content == PNG[:8]
    assert upload(client, a).status_code == 400
    c = upload(client, resource(client)).json()
    assert c["file"]["sha256"] == b["file"]["sha256"]
    root = client.app.state.store.root
    assert len(list((root / "resources/objects").glob("*/*"))) == 1
    assert (
        client.post(f"/api/resources/{a['id']}/archive?revision=2").status_code == 200
    )
    assert client.get(f"/api/resources/{a['id']}/file?revision=2").content == PNG
    assert client.get("/api/resources").json()["total"] == 1


def test_bad_files_and_quota(client, monkeypatch):
    from api_post import resources

    r = resource(client)
    assert upload(client, r, b'<svg onload="evil()"/>', "x.png").status_code == 400
    assert upload(client, r, PNG, "x.html").status_code == 400
    monkeypatch.setitem(resources.LIMITS, "image", 10)
    assert upload(client, r).status_code == 400
    assert not list((client.app.state.store.root / "resources/tmp").glob("*.part"))


def test_signed_links_preserved_and_scope_explicit(client):
    r = resource(
        client,
        addresses=[
            {
                "purpose": "external",
                "url": "https://example.com/a.png?token=private-signature",
                "network_scope": "public",
            }
        ],
    )
    assert "private-signature" not in json.dumps(r)
    a = r["addresses"][0]
    changed = client.put(
        "/api/resources/" + r["id"],
        json={
            "name": "renamed",
            "kind": "image",
            "revision": 1,
            "addresses": [
                {
                    "id": a["id"],
                    "purpose": "external",
                    "url": None,
                    "network_scope": "public",
                    "trusted": True,
                }
            ],
        },
    ).json()
    manager = client.app.state.resources
    assert "private-signature" in manager.private_url(changed, changed["addresses"][0])
    assert client.get("/api/domains").json()[0]["host"] == "example.com"
    binding = {"resource_id": r["id"], "revision": 2, "pointer": "/url", "mode": "auto"}
    with pytest.raises(ValueError, match="没有"):
        manager.resolve({}, [binding], {"network_scope": "internal"}, [])
    known = []
    body, snapshots = manager.resolve({}, [binding], {"network_scope": "public"}, known)
    assert "private-signature" in body["url"] and "private-signature" not in json.dumps(
        snapshots
    )
    assert known == [body["url"]]


def test_binding_saved_and_wire_record(client, upstream):
    r = upload(client, resource(client)).json()
    binding = {
        "resource_id": r["id"],
        "revision": r["revision"],
        "pointer": "/content/0/image_url",
        "mode": "base64",
    }
    case = client.post(
        "/api/cases",
        json={
            "request": {
                "name": "media fixture",
                "path": "/echo",
                "body": "{}",
                "resource_bindings": [binding],
            }
        },
    )
    assert case.status_code == 200, case.text
    case = case.json()
    assert client.get("/api/cases/" + case["id"]).json()["request"][
        "resource_bindings"
    ] == [binding]
    env = environment(client, upstream)
    run = finish(
        client, submit(client, env, body="{}", resource_bindings=[binding])["id"]
    )
    assert run["state"] == "completed", run
    assert run["resource_snapshots"][0]["sha256"] == r["file"]["sha256"]
    # Fixture echo is redacted too: the base64 payload must not bloat persisted evidence.
    assert base64.b64encode(PNG).decode() not in json.dumps(run)
    assert (
        client.put(
            "/api/cases/" + case["id"],
            json={"revision": 99, "request": case["request"]},
        ).status_code
        == 400
    )


def test_download_trust_and_complete(client):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(PNG)))
            self.end_headers()
            self.wfile.write(PNG)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        address = {
            "purpose": "internal",
            "url": f"http://127.0.0.1:{server.server_port}/image.png",
            "network_scope": "local",
        }
        r = resource(client, addresses=[address])

        def start(r):
            return client.post(
                "/api/resources/" + r["id"] + "/download",
                json={"revision": r["revision"], "address_id": r["addresses"][0]["id"]},
            )

        assert start(r).status_code == 400
        r = client.put(
            "/api/resources/" + r["id"],
            json={
                "name": r["name"],
                "kind": "image",
                "revision": 1,
                "addresses": [{**address, "trusted": True}],
            },
        ).json()
        j = start(r).json()
        for _ in range(100):
            j = client.get("/api/resource-jobs/" + j["id"]).json()
            if j["state"] not in {"running", "queued"}:
                break
            time.sleep(0.03)
        assert j["state"] == "completed", j
        value = client.get("/api/resources/" + r["id"]).json()
        assert value["file"]["source"] == "download"
        assert value["addresses"][0]["verified_sha256"] == value["file"]["sha256"]
        assert client.get("/api/resources/" + r["id"] + "/file").content == PNG
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


def test_pointer_prototype_and_gaps():
    for pointer in ["/__proto__/x", "/a/5/url", "/a/~2bad"]:
        with pytest.raises(ValueError):
            inject({}, pointer, "x")
    value = {}
    inject(value, "/a/0/url", "x")
    assert value == {"a": [{"url": "x"}]}

def test_resource_list_paths_filename_search_and_three_link_types(client, upstream):
    from pathlib import Path
    addresses = [
        {'purpose': purpose, 'url': upstream+'/'+purpose+'.png', 'network_scope': 'local'}
        for purpose in ['internal', 'external', 'third_party']
    ]
    item = resource(client, addresses=addresses)
    assert upload(client, item, filename='list-only-file.png').status_code == 200
    result = client.get('/api/resources', params={'search': 'list-only-file.png'}).json()
    assert result['total'] == 1
    row = result['items'][0]
    path = Path(row['local_path'])
    assert path.is_file() and path.is_relative_to(client.app.state.store.root/'resources'/'objects')
    assert not row['file_missing']
    assert {a['purpose'] for a in row['addresses']} == {'internal', 'external', 'third_party'}
    env = environment(client, upstream)
    run = finish(client, submit(client, env, body='{}', resource_bindings=[{
        'resource_id': item['id'], 'revision': row['revision'], 'pointer': '/link', 'mode': 'third_party'}])['id'])
    assert run['state'] == 'completed'
    assert run['resource_snapshots'][0]['mode'] == 'third_party'
    assert json.loads(run['request']['body'])['link'] == upstream+'/third_party.png'
    duplicate = resource(client, name='duplicate check')
    assert client.put('/api/resources/'+duplicate['id'], json={
        'name': duplicate['name'], 'kind': 'image', 'revision': duplicate['revision'],
        'addresses': [addresses[2], addresses[2]],
    }).status_code == 400
    path.unlink()
    missing = client.get('/api/resources', params={'search': 'list-only-file.png'}).json()['items'][0]
    assert missing['file_missing'] and missing['local_path'] is None

def test_unified_url_binding_and_legacy_multiple_urls_preserved(client, upstream):
    manager=client.app.state.resources
    for purpose in ['internal','external','third_party']:
        item=resource(client, addresses=[{'purpose':purpose,'url':upstream+'/asset.png','network_scope':'local'}])
        body,snapshots=manager.resolve({},[{'resource_id':item['id'],'revision':item['revision'],'pointer':'/url','mode':'url'}],{'network_scope':'local'},[])
        assert body['url']==upstream+'/asset.png' and snapshots[0]['mode']=='url'
    item=resource(client, addresses=[{'purpose':p,'url':upstream+'/'+p+'.png','network_scope':'local'} for p in ['internal','external']])
    with pytest.raises(ValueError,match='一个明确的URL'):
        manager.resolve({},[{'resource_id':item['id'],'revision':item['revision'],'pointer':'/url','mode':'url'}],{'network_scope':'local'},[])
    body,_=manager.resolve({},[{'resource_id':item['id'],'revision':item['revision'],'pointer':'/url','mode':'internal'}],{'network_scope':'local'},[])
    assert body['url']==upstream+'/internal.png'
