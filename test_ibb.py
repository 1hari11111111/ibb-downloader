"""Offline tests for URL policy and durable SQLite state."""
import importlib
import sqlite3
from pathlib import Path


def test_profile_url_and_media_url_policy():
    scraper = importlib.import_module("scraper")
    assert scraper.username_from_url("https://68gayathri.imgbb.com/?page=3") == "68gayathri"
    assert scraper.username_from_url("https://ibb.co/user/68gayathri") == "68gayathri"
    assert scraper.normalize_media_url("//i.ibb.co/example/image.gif") == "https://i.ibb.co/example/image.gif"
    assert scraper.normalize_media_url("http://example.com/file.jpg") is None
    assert scraper.normalize_media_url("https://evil.example/file.jpg") is None


def test_html_listing_response_is_decoded():
    scraper = importlib.import_module("scraper")
    import json
    from urllib.parse import quote

    record = {"id_encoded": "abc123", "image": {"filename": "clip.webp", "url": "https://i.ibb.co/x/clip.webp"}, "url_viewer": "https://ibb.co/abc123"}
    markup = '<div class="list-item" data-object="' + quote(json.dumps(record)) + '"></div>'
    assert scraper._records_from_listing({"status_code": 200, "html": markup}) == [record]


def test_authenticated_profile_api_request_includes_token_and_path(monkeypatch):
    scraper = importlib.import_module("scraper")

    class Response:
        status_code = 200
        headers = {}
        url = "https://example.imgbb.com/"
        text = '<input name="auth_token" value="profile-token">'

        def json(self):
            return {"images": []}

        def raise_for_status(self):
            return None

    class Session:
        def __init__(self):
            self.calls = []
            self._imgbb_profile_token = ""

        def get(self, url, **kwargs):
            self.calls.append((url, kwargs))
            return Response()

    session = Session()
    monkeypatch.setattr(scraper, "REQUEST_TIMEOUT", 1)
    scraper.fetch_page(session, "example", 3, "cursor")
    params = session.calls[-1][1]["params"]
    assert params["auth_token"] == "profile-token"
    assert params["pathname"] == "/"
    assert params["page"] == 3
    assert params["seek"] == "cursor"


def test_fixed_size_page_without_cursor_is_not_treated_as_complete(monkeypatch):
    scraper = importlib.import_module("scraper")

    class Response:
        status_code = 200
        headers = {}
        url = "https://example.imgbb.com/"
        text = ""

        def __init__(self, page):
            self.page = page

        def json(self):
            record = {
                "id_encoded": f"id-{self.page}",
                "image": {
                    "filename": f"image-{self.page}.jpg",
                    "url": f"https://i.ibb.co/x/image-{self.page}.jpg",
                },
                "url_viewer": f"https://ibb.co/id-{self.page}",
            }
            return {"images": [record] * 32}

        def raise_for_status(self):
            return None

    class Session:
        _imgbb_profile_token = "profile-token"

        def get(self, url, **kwargs):
            return Response(kwargs["params"]["page"])

    monkeypatch.setattr(scraper, "REQUEST_TIMEOUT", 1)
    items, next_seek, complete = scraper.fetch_page(Session(), "example", 1, None)
    assert len(items) == 1
    assert next_seek is None
    assert complete is False


def test_empty_page_without_cursor_is_complete(monkeypatch):
    scraper = importlib.import_module("scraper")

    class Response:
        status_code = 200
        headers = {}
        url = "https://example.imgbb.com/"
        text = ""

        def json(self):
            return {"images": []}

        def raise_for_status(self):
            return None

    class Session:
        _imgbb_profile_token = "profile-token"

        def get(self, url, **kwargs):
            return Response()

    monkeypatch.setattr(scraper, "REQUEST_TIMEOUT", 1)
    _, next_seek, complete = scraper.fetch_page(Session(), "example", 2, None)
    assert next_seek is None
    assert complete is True


def test_job_status_persists_exception_as_text(tmp_path: Path, monkeypatch):
    db = importlib.import_module("db")
    database = tmp_path / "status.sqlite"
    monkeypatch.setattr(db, "DB_FILE", str(database))
    db.init_db()
    job_id = db.create_job("https://example.imgbb.com/", "example")
    db.job_status(job_id, "failed", RuntimeError("HTTP 400"))
    assert db.get_job(job_id)["last_error"] == "HTTP 400"


def test_database_transitions_are_idempotent(tmp_path: Path, monkeypatch):
    db = importlib.import_module("db")
    database = tmp_path / "state.sqlite"
    monkeypatch.setattr(db, "DB_FILE", str(database))
    db.init_db()
    job_id = db.create_job("https://example.imgbb.com/", "example")
    item = {"page_url": "https://ibb.co/view", "direct_url": "https://i.ibb.co/a.jpg", "filename": "a.jpg", "media_type": "photo"}
    assert db.add_items(job_id, [item]) == 1
    assert db.add_items(job_id, [item]) == 0
    claimed = db.claim(job_id)
    assert claimed is not None
    db.downloaded(claimed["id"], str(tmp_path / "a.jpg"))
    assert db.sent(claimed["id"], 123)
    assert not db.sent(claimed["id"], 123)
    row = db.get_job(job_id)
    assert row["discovered"] == 1
    assert row["sent"] == 1
