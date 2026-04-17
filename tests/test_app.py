from datetime import datetime, timezone
from unittest.mock import patch

import app as app_module
import pytest


class FakeS3Client:
    def __init__(self):
        self.deleted_keys = []
        self.put_requests = []

    def head_bucket(self, Bucket):
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def put_object(self, **kwargs):
        self.put_requests.append(kwargs)
        return {"ETag": "fake-etag"}

    def list_objects_v2(self, Bucket, Prefix, MaxKeys):
        return {
            "IsTruncated": False,
            "Contents": [
                {
                    "Key": f"{Prefix}example.txt",
                    "Size": 42,
                    "LastModified": datetime(2026, 1, 1, tzinfo=timezone.utc),
                }
            ],
        }

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        return f"https://example.com/{Params['Key']}?expires={ExpiresIn}"

    def delete_object(self, Bucket, Key):
        self.deleted_keys.append(Key)
        return {}


@pytest.fixture
def client(monkeypatch):
    fake_s3 = FakeS3Client()
    monkeypatch.setenv("S3_BUCKET", "unit-test-bucket")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    with patch.object(app_module.boto3, "client", return_value=fake_s3):
        flask_app = app_module.create_app()

    with flask_app.test_client() as test_client:
        yield test_client


def test_health_endpoint(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"


def test_info_endpoint_lists_new_routes(client):
    response = client.get("/info")
    data = response.get_json()

    assert response.status_code == 200
    assert "GET /s3/list?prefix=demo/&limit=20" in data["endpoints"]
    assert "POST /s3/upload-json" in data["endpoints"]
    assert "DELETE /s3/object?key=demo/file.txt" in data["endpoints"]


def test_s3_list_endpoint(client):
    response = client.get("/s3/list?prefix=demo/&limit=10")
    data = response.get_json()

    assert response.status_code == 200
    assert data["count"] == 1
    assert data["objects"][0]["key"] == "demo/example.txt"


def test_presign_requires_key(client):
    response = client.get("/s3/presign-get")
    assert response.status_code == 400


def test_delete_object_endpoint(client):
    response = client.delete("/s3/object?key=demo/old-file.txt")
    data = response.get_json()

    assert response.status_code == 200
    assert data["status"] == "deleted"


def test_upload_json_requires_content_field(client):
    response = client.post("/s3/upload-json", json={"key": "demo/payload.json"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "field 'content' is required"


def test_upload_json_success(client):
    response = client.post(
        "/s3/upload-json",
        json={
            "key": "demo/payload.json",
            "content": {"environment": "production", "version": 1},
        },
    )
    data = response.get_json()

    assert response.status_code == 201
    assert data["status"] == "uploaded"
    assert data["key"] == "demo/payload.json"
