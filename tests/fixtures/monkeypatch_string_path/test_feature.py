def test_patch(monkeypatch):
    monkeypatch.setattr("target_old.load", lambda: "patched")
