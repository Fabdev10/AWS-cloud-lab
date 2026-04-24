from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

# Ensure project root is importable in CI before importing app module.
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

import app as app_module
import pytest


class FakeS3Client:
    def __init__(self):
        self.deleted_keys = []
        self.put_requests = []
        self.objects = {
            "demo/example.txt": {
                "Body": b"hello from aws-cloud-lab",
                "ContentType": "text/plain",
                "Metadata": {"owner": "aws-cloud-lab"},
                "LastModified": datetime(2026, 1, 2, tzinfo=timezone.utc),
            },
            "demo/sample.json": {
                "Body": b'{"hello": "world"}',
                "ContentType": "application/json",
                "Metadata": {},
                "LastModified": datetime(2026, 1, 2, tzinfo=timezone.utc),
            }
        }

    def head_bucket(self, Bucket):
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def head_object(self, Bucket, Key):
        item = self.objects[Key]
        return {
            "ContentLength": len(item["Body"]),
            "ContentType": item["ContentType"],
            "ETag": '"fake-etag"',
            "LastModified": item["LastModified"],
            "Metadata": item["Metadata"],
        }

    def put_object(self, **kwargs):
        self.put_requests.append(kwargs)
        body = kwargs.get("Body", b"")
        if isinstance(body, str):
            body = body.encode("utf-8")

        self.objects[kwargs["Key"]] = {
            "Body": body,
            "ContentType": kwargs.get("ContentType", "application/octet-stream"),
            "Metadata": kwargs.get("Metadata", {}),
            "LastModified": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
        return {"ETag": "fake-etag"}

    def get_object(self, Bucket, Key):
        item = self.objects[Key]
        return {
            "Body": io.BytesIO(item["Body"]),
            "ContentType": item["ContentType"],
        }

    def list_objects_v2(self, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        all_matching_keys = sorted([key for key in self.objects if key.startswith(Prefix)])
        start_index = int(ContinuationToken) if ContinuationToken is not None else 0
        end_index = start_index + MaxKeys
        matching_keys = all_matching_keys[start_index:end_index]
        is_truncated = end_index < len(all_matching_keys)
        next_token = str(end_index) if is_truncated else None

        return {
            "IsTruncated": is_truncated,
            "NextContinuationToken": next_token,
            "Contents": [
                {
                    "Key": key,
                    "Size": len(self.objects[key]["Body"]),
                    "LastModified": self.objects[key]["LastModified"],
                    "StorageClass": "STANDARD",
                }
                for key in matching_keys
            ],
        }

    def generate_presigned_url(self, ClientMethod, Params, ExpiresIn):
        return f"https://example.com/{Params['Key']}?expires={ExpiresIn}"

    def delete_object(self, Bucket, Key):
        self.deleted_keys.append(Key)
        self.objects.pop(Key, None)
        return {}

    def copy_object(self, Bucket, CopySource, Key, **kwargs):
        source_key = CopySource["Key"]
        source_item = self.objects[source_key]
        metadata = kwargs.get("Metadata", source_item.get("Metadata", {}))
        content_type = kwargs.get("ContentType", source_item.get("ContentType", "application/octet-stream"))
        self.objects[Key] = {
            "Body": source_item["Body"],
            "ContentType": content_type,
            "Metadata": metadata,
            "LastModified": datetime(2026, 1, 2, tzinfo=timezone.utc),
        }
        return {}

    def delete_objects(self, Bucket, Delete):
        deleted = []
        for item in Delete.get("Objects", []):
            key = item.get("Key")
            if key in self.objects:
                self.objects.pop(key, None)
                deleted.append({"Key": key})
        return {"Deleted": deleted}

    def list_object_keys(self):
        return sorted(self.objects.keys())


class FakeStsClient:
    def get_caller_identity(self):
        return {
            "Account": "123456789012",
            "Arn": "arn:aws:sts::123456789012:assumed-role/aws-cloud-lab-task-role/frontend",
            "UserId": "AROA123EXAMPLE:frontend",
        }


@pytest.fixture
def client(monkeypatch):
    fake_s3 = FakeS3Client()
    fake_sts = FakeStsClient()
    monkeypatch.setenv("S3_BUCKET", "unit-test-bucket")
    monkeypatch.setenv("AWS_REGION", "eu-west-1")

    def fake_boto3_client(service_name, region_name=None):
        if service_name == "s3":
            return fake_s3
        if service_name == "sts":
            return fake_sts
        raise ValueError(f"Unsupported fake AWS service: {service_name}")

    with patch.object(app_module.boto3, "client", side_effect=fake_boto3_client):
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
    assert "GET /s3/list?prefix=demo/&limit=20&cursor=<token>" in data["endpoints"]
    assert "GET /s3/stats?prefix=demo/" in data["endpoints"]
    assert "POST /s3/upload-json" in data["endpoints"]
    assert "DELETE /s3/object?key=demo/file.txt" in data["endpoints"]
    assert "GET /metrics" in data["endpoints"]
    assert "GET /aws/identity" in data["endpoints"]
    assert "GET /s3/object-head?key=<object-key>" in data["endpoints"]
    assert "GET /s3/presign-put?key=<object-key>&expires=300&contentType=text/plain" in data["endpoints"]
    assert "GET /s3/object-json?key=<object-key>" in data["endpoints"]
    assert "POST /s3/copy-object" in data["endpoints"]
    assert "POST /s3/batch-delete" in data["endpoints"]
    assert "GET /audit/recent?limit=20" in data["endpoints"]


def test_aws_identity_endpoint(client):
    response = client.get("/aws/identity")
    data = response.get_json()

    assert response.status_code == 200
    assert data["account"] == "123456789012"
    assert data["region"] == "eu-west-1"


def test_metrics_endpoint(client):
    client.get("/health")
    response = client.get("/metrics")
    data = response.get_json()

    assert response.status_code == 200
    assert data["service"] == "aws-cloud-lab"
    assert data["requestCount"] >= 2
    assert data["routeHits"]["/health"] >= 1
    assert data["routeHits"]["/metrics"] >= 1


def test_object_head_requires_key(client):
    response = client.get("/s3/object-head")
    assert response.status_code == 400


def test_object_head_success(client):
    response = client.get("/s3/object-head?key=demo/example.txt")
    data = response.get_json()

    assert response.status_code == 200
    assert data["key"] == "demo/example.txt"
    assert data["size"] == len(b"hello from aws-cloud-lab")
    assert data["metadata"]["owner"] == "aws-cloud-lab"


def test_object_json_requires_key(client):
    response = client.get("/s3/object-json")
    assert response.status_code == 400


def test_object_json_rejects_non_json_object(client):
    response = client.get("/s3/object-json?key=demo/example.txt")
    assert response.status_code == 400
    assert response.get_json()["error"] == "object content is not valid UTF-8 JSON"


def test_s3_list_endpoint(client):
    response = client.get("/s3/list?prefix=demo/&limit=10")
    data = response.get_json()

    assert response.status_code == 200
    assert data["count"] == 2
    assert data["objects"][0]["key"] == "demo/example.txt"


def test_s3_list_pagination(client):
    first_response = client.get("/s3/list?prefix=demo/&limit=1")
    first_data = first_response.get_json()

    assert first_response.status_code == 200
    assert first_data["count"] == 1
    assert first_data["truncated"] is True
    assert first_data["nextCursor"] is not None

    second_response = client.get(f"/s3/list?prefix=demo/&limit=1&cursor={first_data['nextCursor']}")
    second_data = second_response.get_json()

    assert second_response.status_code == 200
    assert second_data["count"] == 1
    assert second_data["objects"][0]["key"] == "demo/sample.json"


def test_s3_stats_endpoint(client):
    response = client.get("/s3/stats?prefix=demo/")
    data = response.get_json()

    assert response.status_code == 200
    assert data["objectCount"] == 2
    assert data["totalBytes"] >= len(b"hello from aws-cloud-lab")
    assert data["storageClassAssumed"] == "STANDARD"
    assert data["largestObject"]["key"] in {"demo/example.txt", "demo/sample.json"}


def test_presign_requires_key(client):
    response = client.get("/s3/presign-get")
    assert response.status_code == 400


def test_presign_put_requires_key(client):
    response = client.get("/s3/presign-put")
    assert response.status_code == 400


def test_presign_put_success(client):
    response = client.get("/s3/presign-put?key=demo/upload.txt&expires=120&contentType=text/plain")
    data = response.get_json()

    assert response.status_code == 200
    assert data["key"] == "demo/upload.txt"
    assert data["method"] == "PUT"
    assert data["contentType"] == "text/plain"


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


def test_object_json_success(client):
    client.post(
        "/s3/upload-json",
        json={
            "key": "demo/config.json",
            "content": {"environment": "production", "version": 2},
        },
    )
    response = client.get("/s3/object-json?key=demo/config.json")
    data = response.get_json()

    assert response.status_code == 200
    assert data["key"] == "demo/config.json"
    assert data["content"]["environment"] == "production"


def test_copy_object_success(client):
    response = client.post(
        "/s3/copy-object",
        json={
            "sourceKey": "demo/example.txt",
            "destinationKey": "demo/example-copy.txt",
            "metadata": {"copied": True},
            "contentType": "text/plain",
        },
    )
    data = response.get_json()

    assert response.status_code == 201
    assert data["status"] == "copied"
    assert data["destinationKey"] == "demo/example-copy.txt"

    copied_head = client.get("/s3/object-head?key=demo/example-copy.txt").get_json()
    assert copied_head["metadata"]["copied"] == "True"


def test_copy_object_validation(client):
    response = client.post("/s3/copy-object", json={"sourceKey": "demo/example.txt"})
    assert response.status_code == 400


def test_batch_delete_success(client):
    client.post(
        "/s3/upload-json",
        json={"key": "demo/remove-1.json", "content": {"remove": 1}},
    )
    client.post(
        "/s3/upload-json",
        json={"key": "demo/remove-2.json", "content": {"remove": 2}},
    )

    response = client.post(
        "/s3/batch-delete",
        json={"keys": ["demo/remove-1.json", "demo/remove-2.json"]},
    )
    data = response.get_json()

    assert response.status_code == 200
    assert data["requested"] == 2
    assert data["deletedCount"] == 2


def test_batch_delete_validation(client):
    response = client.post("/s3/batch-delete", json={"keys": [""]})
    assert response.status_code == 400


def test_audit_recent_events(client):
    client.post(
        "/s3/upload-json",
        json={"key": "demo/audit.json", "content": {"ok": True}},
    )
    client.delete("/s3/object?key=demo/audit.json")

    response = client.get("/audit/recent?limit=5")
    data = response.get_json()

    assert response.status_code == 200
    assert data["count"] >= 2
    assert any(event["action"] == "upload-json" for event in data["events"])
    assert any(event["action"] == "delete-object" for event in data["events"])
