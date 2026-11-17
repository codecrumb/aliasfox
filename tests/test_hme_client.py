import json
import time

import httpx
import pytest

from aliasfox.apple.hme import Alias, HmeClient, HmeError, SessionExpired
from aliasfox.sessions import AppleSessionState

BASE = "https://p99-maildomainws.icloud.com"
SECRET = "SECRETCOOKIEVALUE123"


def make_state(**kw):
    return AppleSessionState(
        cookies={"X-APPLE-WEBAUTH-USER": SECRET, "other": "v2"},
        webservices_url=BASE,
        dsid=kw.get("dsid", "12345"),
    )


def ok(result):
    return httpx.Response(200, json={"success": True, "result": result})


def client_for(handler, state=None, **kw):
    state = state or make_state()
    return HmeClient(state, transport=httpx.MockTransport(handler), **kw), state


def entry(i=0, label="shop"):
    return {
        "anonymousId": f"id{i}",
        "hme": f"a{i}@icloud.com",
        "label": label,
        "note": "n",
        "isActive": True,
        "forwardToEmail": "me@x.com",
        "origin": "ON_DEMAND",
        "domain": "d.com",
        "createTimestamp": 1700000000000 + i,
    }


def test_list_parses_aliases():
    seen = {}

    def handler(req):
        seen["req"] = req
        return ok({"hmeEmails": [entry(1, "Shöp 🛍️ 店"), {"anonymousId": "bare"}]})

    c, _ = client_for(handler)
    aliases = c.list()
    r = seen["req"]
    assert r.method == "GET"
    assert r.url.path == "/v2/hme/list"
    assert "dsid" not in r.url.params
    assert r.url.query == b""
    assert r.headers["origin"] == "https://www.icloud.com"
    assert r.headers["referer"] == "https://www.icloud.com/"
    assert r.headers["content-type"] == "text/plain"
    assert "X-APPLE-WEBAUTH-USER=" + SECRET in r.headers["cookie"]
    a = aliases[0]
    assert isinstance(a, Alias)
    assert (a.anonymous_id, a.hme, a.note, a.is_active) == ("id1", "a1@icloud.com", "n", True)
    assert (a.forward_to, a.origin, a.domain) == ("me@x.com", "ON_DEMAND", "d.com")
    assert a.label == "Shöp 🛍️ 店"
    assert a.created_ms == 1700000000001
    assert a.raw["hme"] == "a1@icloud.com"
    b = aliases[1]
    assert b.anonymous_id == "bare" and b.hme == "" and b.label == ""
    assert b.created_ms is None and b.is_active is False


def test_list_10000_aliases_under_one_second():
    payload = {
        "success": True,
        "result": {"hmeEmails": [entry(i, f"label {i} 🦊 ünï") for i in range(10000)]},
    }
    c, _ = client_for(lambda req: httpx.Response(200, json=payload))
    start = time.perf_counter()
    aliases = c.list()
    elapsed = time.perf_counter() - start
    assert len(aliases) == 10000
    assert aliases[9999].label == "label 9999 🦊 ünï"
    assert elapsed < 1.0


def test_generate_returns_hme():
    seen = {}

    def handler(req):
        seen["req"] = req
        return ok({"hme": "gen@icloud.com"})

    c, _ = client_for(handler)
    assert c.generate() == "gen@icloud.com"
    assert seen["req"].url.path == "/v1/hme/generate"
    assert json.loads(seen["req"].content) == {"langCode": "en-us"}


def test_reserve_sends_expected_body():
    seen = {}

    def handler(req):
        seen["req"] = req
        return ok({"hmeEmail": entry(7, "L")})

    c, _ = client_for(handler)
    a = c.reserve("x@icloud.com", "L", "note")
    r = seen["req"]
    assert r.method == "POST"
    assert r.url.path == "/v1/hme/reserve"
    assert r.headers["content-type"] == "text/plain"
    assert json.loads(r.content) == {"hme": "x@icloud.com", "label": "L", "note": "note"}
    assert a.anonymous_id == "id7"


def test_reserve_accepts_the_alias_under_a_different_key():
    c, _ = client_for(lambda req: ok({"reservedEmail": entry(7, "L")}))
    assert c.reserve("a7@icloud.com", "L").anonymous_id == "id7"


def test_reserve_without_an_alias_in_the_response_looks_it_up_in_the_list(caplog):
    caplog.set_level("INFO")
    paths = []

    def handler(req):
        paths.append((req.method, req.url.path))
        if req.url.path == "/v1/hme/reserve":
            return ok({"weirdKey": "SECRET-VALUE", "count": 1})
        return ok({"hmeEmails": [entry(3), entry(7, "L")]})

    c, _ = client_for(handler)
    a = c.reserve("a7@icloud.com", "L")
    assert a.anonymous_id == "id7" and a.label == "L"
    assert paths == [("POST", "/v1/hme/reserve"), ("GET", "/v2/hme/list")]
    # the diagnostic names the keys so the real response shape can be learned, never the values
    assert "weirdKey" in caplog.text and "count" in caplog.text
    assert "SECRET-VALUE" not in caplog.text


def test_reserve_that_really_did_nothing_still_raises():
    def handler(req):
        if req.url.path == "/v1/hme/reserve":
            return ok({})
        return ok({"hmeEmails": [entry(3)]})

    c, _ = client_for(handler)
    with pytest.raises(HmeError, match="reserve returned no alias"):
        c.reserve("a7@icloud.com", "L")


def test_reserve_failure_message_names_the_response_keys_but_not_values():
    def handler(req):
        if req.url.path == "/v1/hme/reserve":
            return ok({"hmeEmail": None, "token": "SECRET-VALUE"})
        return ok({"hmeEmails": []})

    c, _ = client_for(handler)
    with pytest.raises(HmeError) as ei:
        c.reserve("a7@icloud.com", "L")
    msg = str(ei.value)
    assert "hmeEmail" in msg and "token" in msg and "SECRET-VALUE" not in msg


def test_update_metadata_and_forward_to_bodies():
    reqs = []

    def handler(req):
        reqs.append(req)
        return ok({})

    c, _ = client_for(handler)
    c.update_metadata("id1", "lbl", "nt")
    c.update_forward_to("me@x.com")
    assert reqs[0].url.path == "/v1/hme/updateMetaData"
    assert json.loads(reqs[0].content) == {"anonymousId": "id1", "label": "lbl", "note": "nt"}
    assert reqs[1].url.path == "/v1/hme/updateForwardTo"
    assert json.loads(reqs[1].content) == {"forwardToEmail": "me@x.com"}


def test_set_active_hits_deactivate_or_reactivate():
    paths = []

    def handler(req):
        paths.append((req.url.path, json.loads(req.content)))
        return ok({})

    c, _ = client_for(handler)
    c.set_active("id1", False)
    c.set_active("id1", True)
    assert paths == [
        ("/v1/hme/deactivate", {"anonymousId": "id1"}),
        ("/v1/hme/reactivate", {"anonymousId": "id1"}),
    ]


def test_delete_deactivates_then_deletes_on_failure():
    paths = []

    def handler(req):
        paths.append(req.url.path)
        if paths == ["/v1/hme/delete"]:
            return httpx.Response(200, json={"success": False, "error": {"errorCode": 1, "errorMessage": "active"}})
        return ok({})

    c, _ = client_for(handler)
    c.delete("id1")
    assert paths == ["/v1/hme/delete", "/v1/hme/deactivate", "/v1/hme/delete"]


def test_delete_raises_second_error_if_retry_fails():
    n = {"delete": 0}

    def handler(req):
        if req.url.path.endswith("/delete"):
            n["delete"] += 1
            code = n["delete"]
            return httpx.Response(200, json={"success": False, "error": {"errorCode": code, "errorMessage": "nope"}})
        return ok({})

    c, _ = client_for(handler)
    with pytest.raises(HmeError) as ei:
        c.delete("id1")
    assert ei.value.code == 2


def test_success_false_raises_hme_error_with_code():
    def handler(req):
        return httpx.Response(200, json={"success": False, "error": {"errorCode": -41015, "errorMessage": "limit"}})

    c, _ = client_for(handler)
    with pytest.raises(HmeError) as ei:
        c.generate()
    assert not isinstance(ei.value, SessionExpired)
    assert ei.value.code == -41015
    assert "limit" in str(ei.value)


@pytest.mark.parametrize("status", [401, 421])
def test_401_and_421_raise_session_expired(status):
    c, _ = client_for(lambda req: httpx.Response(status, text="body"))
    with pytest.raises(SessionExpired):
        c.list()


def test_other_http_error_has_status_and_no_body():
    c, _ = client_for(lambda req: httpx.Response(500, text="LEAKY BODY"))
    with pytest.raises(HmeError) as ei:
        c.list()
    assert ei.value.code == 500
    assert "LEAKY BODY" not in str(ei.value)


def test_set_cookie_rotation_calls_on_state_change_with_new_cookies():
    calls = []

    def handler(req):
        return httpx.Response(
            200,
            json={"success": True, "result": {"hmeEmails": []}},
            headers=[("set-cookie", "X-APPLE-WEBAUTH-USER=rotated; Path=/; Secure"), ("set-cookie", "newone=zz; Path=/")],
        )

    c, state = client_for(handler, on_state_change=lambda s: calls.append(dict(s.cookies)))
    c.list()
    assert state.cookies == {"X-APPLE-WEBAUTH-USER": "rotated", "other": "v2", "newone": "zz"}
    assert calls == [state.cookies]


def test_no_state_change_callback_when_cookies_unchanged():
    calls = []

    def handler(req):
        return httpx.Response(
            200,
            json={"success": True, "result": {"hmeEmails": []}},
            headers=[("set-cookie", "other=v2; Path=/")],
        )

    c, _ = client_for(handler, on_state_change=calls.append)
    c.list()
    assert calls == []


def test_no_dsid_param_when_unknown():
    seen = {}

    def handler(req):
        seen["req"] = req
        return ok({"hmeEmails": []})

    c, _ = client_for(handler, state=make_state(dsid=None))
    c.list()
    assert "dsid" not in seen["req"].url.params


def test_errors_do_not_contain_cookie_values():
    def run(handler, call):
        c, _ = client_for(handler)
        with pytest.raises(HmeError) as ei:
            call(c)
        e = ei.value
        text = " ".join([str(e), repr(e), repr(e.args), str(e.code)])
        assert SECRET not in text
        assert "cookie" not in text.lower()

    run(lambda r: httpx.Response(401, text=SECRET), lambda c: c.list())
    run(lambda r: httpx.Response(500, text=SECRET), lambda c: c.list())
    run(
        lambda r: httpx.Response(200, json={"success": False, "error": {"errorCode": 1, "errorMessage": "bad"}}),
        lambda c: c.generate(),
    )

    def boom(req):
        raise httpx.ConnectError("cannot connect", request=req)

    run(boom, lambda c: c.list())


@pytest.mark.parametrize(
    "attrs",
    ["Max-Age=0", "Max-Age=-5", "Expires=Thu, 01 Jan 1970 00:00:00 GMT"],
)
def test_expiring_set_cookie_deletes_cookie_and_signals_change(attrs):
    calls = []

    def handler(req):
        return httpx.Response(
            200,
            json={"success": True, "result": {"hmeEmails": []}},
            headers=[("set-cookie", f"other=gone; Path=/; {attrs}")],
        )

    c, state = client_for(handler, on_state_change=lambda s: calls.append(dict(s.cookies)))
    c.list()
    assert "other" not in state.cookies
    assert len(calls) == 1 and "other" not in calls[0]


def test_empty_set_cookie_value_deletes_cookie():
    def handler(req):
        return httpx.Response(
            200,
            json={"success": True, "result": {"hmeEmails": []}},
            headers=[("set-cookie", "other=; Path=/")],
        )

    c, state = client_for(handler)
    c.list()
    assert "other" not in state.cookies


def test_deleting_absent_cookie_is_not_a_change():
    calls = []

    def handler(req):
        return httpx.Response(
            200,
            json={"success": True, "result": {"hmeEmails": []}},
            headers=[("set-cookie", "nothere=; Max-Age=0")],
        )

    c, _ = client_for(handler, on_state_change=calls.append)
    c.list()
    assert calls == []


def test_list_raw_returns_result_dict_with_top_level_keys():
    def handler(req):
        return ok({"hmeEmails": [entry(1)], "selectedForwardTo": "x", "nextPage": "p"})

    c, _ = client_for(handler)
    raw = c.list_raw()
    assert set(raw) == {"hmeEmails", "selectedForwardTo", "nextPage"}


@pytest.mark.parametrize("exc", [UnicodeEncodeError("ascii", "x", 0, 1, SECRET), ValueError(SECRET)])
def test_unicode_and_value_errors_become_hme_error_without_values(exc):
    def boom(req):
        raise exc

    c, _ = client_for(boom)
    with pytest.raises(HmeError) as ei:
        c.list()
    text = " ".join([str(ei.value), repr(ei.value), repr(ei.value.args)])
    assert SECRET not in text
    assert ei.value.__cause__ is None
    assert ei.value.__suppress_context__


def test_unencodable_cookie_value_becomes_hme_error():
    state = make_state()
    state.cookies["bad"] = "café€\ud800"
    c, _ = client_for(lambda r: ok({}), state)
    with pytest.raises(HmeError) as ei:
        c.list()
    assert "€" not in str(ei.value)
