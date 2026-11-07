from flakehunt import dashboard


def test_dashboard_escapes_untrusted_text():
    row = {"id": 1, "nodeid": "t.py::t[<script>alert(1)</script>]", "timestamp": "now", "profile": "order",
           "min_order": '["a", "b"]', "traceback": "</pre><img src=x onerror=alert(1)>",
           "ai_explanation": "<script>steal()</script>", "kind": "ORDER-DEPENDENT", "fail_runs": "3/8",
           "repro_rate": "3/3", "fix_diff": "+<b>x</b>"}
    html = dashboard.render([row])
    assert "<script>" not in html and "<img" not in html and "<b>x</b>" not in html
    assert "&lt;script&gt;" in html


def test_dashboard_empty_state():
    assert "No flaky failures" in dashboard.render([])
