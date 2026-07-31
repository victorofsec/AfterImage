import io
import os
import tempfile

from PIL import Image
import pytest


TEST_ROOT = tempfile.TemporaryDirectory(prefix="afterimage-tests-")
os.environ["IP_HASH_SECRET"] = "test-only-ip-hash-secret"
os.environ["DATABASE_PATH"] = os.path.join(TEST_ROOT.name, "test.db")
os.environ["UPLOAD_FOLDER"] = os.path.join(TEST_ROOT.name, "uploads")

import app as application  # noqa: E402


@pytest.fixture(autouse=True)
def clean_database():
    connection = application.connect_db()
    connection.executescript(
        """
        DELETE FROM view_sessions;
        DELETE FROM blocked_viewers;
        DELETE FROM view_logs;
        DELETE FROM images;
        """
    )
    connection.commit()
    connection.close()
    yield


@pytest.fixture
def client():
    application.app.config.update(TESTING=True)
    return application.app.test_client()


def png_file(width=120, height=60):
    content = io.BytesIO()
    Image.new("RGB", (width, height), "#ff5a3d").save(content, "PNG")
    content.seek(0)
    return content


def upload_image(client, remote_addr="192.0.2.10", slices="60"):
    response = client.post(
        "/upload",
        data={
            "duration_ms": "5000",
            "slice_count": slices,
            "image": (png_file(), "test.png"),
        },
        content_type="multipart/form-data",
        environ_base={"REMOTE_ADDR": remote_addr},
    )
    assert response.status_code == 200
    return application.db_execute("SELECT id FROM images", fetch=True, one=True)[0]


def test_language_detection_and_manual_override(client):
    response = client.get("/", headers={"Accept-Language": "en-US,en;q=0.9"})
    assert response.status_code == 200
    assert b'<html lang="en">' in response.data
    assert b"Generate private link" in response.data

    response = client.get("/language/fr?next=/", follow_redirects=True)
    assert b'<html lang="fr">' in response.data
    assert b"G\xc3\xa9n\xc3\xa9rer le lien priv\xc3\xa9" in response.data


def test_pledge_is_required_and_ip_is_blocked(client):
    image_id = upload_image(client)
    viewer_ip = {"REMOTE_ADDR": "192.0.2.20"}

    rejected = client.post(f"/api/session/{image_id}", json={}, environ_base=viewer_ip)
    assert rejected.status_code == 400
    assert rejected.get_json()["error"] == "pledge_required"

    accepted = client.post(
        f"/api/session/{image_id}",
        json={"pledge_accepted": True},
        environ_base=viewer_ip,
    )
    assert accepted.status_code == 200
    assert accepted.get_json()["max_visible"] == 20

    repeated = client.post(
        f"/api/session/{image_id}",
        json={"pledge_accepted": True},
        environ_base=viewer_ip,
    )
    assert repeated.status_code == 410


def test_fragments_are_private_ordered_and_single_use(client):
    image_id = upload_image(client)
    viewer_ip = {"REMOTE_ADDR": "192.0.2.30"}
    config = client.post(
        f"/api/session/{image_id}",
        json={"pledge_accepted": True},
        environ_base=viewer_ip,
    ).get_json()
    authorization = {"Authorization": f"Bearer {config['token']}"}

    assert client.get(f"{config['frame_url']}/0", environ_base=viewer_ip).status_code == 404
    assert client.get(
        f"{config['frame_url']}/1", headers=authorization, environ_base=viewer_ip
    ).status_code == 410

    first = client.get(
        f"{config['frame_url']}/0", headers=authorization, environ_base=viewer_ip
    )
    assert first.status_code == 200
    assert first.mimetype == "image/png"
    assert first.headers["Cache-Control"].startswith("no-store")
    assert client.get(
        f"{config['frame_url']}/0", headers=authorization, environ_base=viewer_ip
    ).status_code == 410


def test_original_uploads_are_not_public(client):
    response = client.get("/uploads/anything.png")
    assert response.status_code == 404
    assert "default-src 'none'" in response.headers["Content-Security-Policy"]
