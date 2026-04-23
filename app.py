import logging
import os
import time
import json
from collections import deque
from datetime import datetime, timezone
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from flask import Flask, jsonify, request


def create_app() -> Flask:
    app = Flask(__name__)

    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    logger = logging.getLogger("aws-cloud-lab")
    region = os.getenv("AWS_REGION", "eu-west-1")
    bucket_name = os.getenv("S3_BUCKET", "")
    s3_client = boto3.client("s3", region_name=region)
    sts_client = boto3.client("sts", region_name=region)
    app_started_at_utc = datetime.now(timezone.utc)
    app_started_monotonic = time.monotonic()
    request_count = 0
    route_hits: dict[str, int] = {}
    audit_events: deque[dict[str, Any]] = deque(maxlen=200)

    def bucket_not_configured_response():
        return jsonify({"error": "S3_BUCKET is not configured"}), 400

    def parse_bounded_int(raw_value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(value, maximum))

    def record_audit(action: str, status: str, key: str | None = None, detail: str | None = None) -> None:
        audit_events.append(
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "status": status,
                "key": key,
                "detail": detail,
            }
        )

    @app.before_request
    def collect_basic_metrics():
        nonlocal request_count
        request_count += 1
        route_key = request.path
        route_hits[route_key] = route_hits.get(route_key, 0) + 1

    @app.get("/")
    def index():
        logger.info("Root endpoint invoked")
        return jsonify(
            {
                "message": "AWS Cloud Lab is running",
                "service": "frontend",
                "region": region,
                "bucketConfigured": bool(bucket_name),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    @app.get("/info")
    def info():
        return jsonify(
            {
                "service": "aws-cloud-lab",
                "region": region,
                "bucket": bucket_name or None,
                "endpoints": [
                    "GET /",
                    "GET /info",
                    "GET /health",
                    "GET /metrics",
                    "GET /audit/recent?limit=20",
                    "GET /aws/identity",
                    "GET /s3/check",
                    "GET /s3/object-head?key=<object-key>",
                    "GET /s3/object-json?key=<object-key>",
                    "GET /s3/list?prefix=demo/&limit=20&cursor=<token>",
                    "GET /s3/stats?prefix=demo/",
                    "GET /s3/presign-get?key=<object-key>&expires=300",
                    "GET /s3/presign-put?key=<object-key>&expires=300&contentType=text/plain",
                    "POST /s3/upload-demo?key=demo/file.txt",
                    "POST /s3/upload-json",
                    "POST /s3/copy-object",
                    "POST /s3/batch-delete",
                    "DELETE /s3/object?key=demo/file.txt",
                    "GET /stress?seconds=20",
                ],
            }
        )

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @app.get("/metrics")
    def metrics():
        uptime_seconds = int(time.monotonic() - app_started_monotonic)
        return jsonify(
            {
                "service": "aws-cloud-lab",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "startedAt": app_started_at_utc.isoformat(),
                "uptimeSeconds": uptime_seconds,
                "requestCount": request_count,
                "routeHits": route_hits,
            }
        )

    @app.get("/audit/recent")
    def recent_audit_events():
        limit = parse_bounded_int(request.args.get("limit"), default=20, minimum=1, maximum=100)
        items = list(audit_events)[-limit:]
        items.reverse()
        return jsonify({"count": len(items), "events": items})

    @app.get("/aws/identity")
    def aws_identity():
        try:
            identity = sts_client.get_caller_identity()
            return jsonify(
                {
                    "account": identity.get("Account"),
                    "arn": identity.get("Arn"),
                    "userId": identity.get("UserId"),
                    "region": region,
                }
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to retrieve caller identity")
            return jsonify({"status": "error", "detail": str(exc)}), 500

    @app.get("/s3/check")
    def s3_check():
        if not bucket_name:
            return bucket_not_configured_response()

        try:
            s3_client.head_bucket(Bucket=bucket_name)
            logger.info("S3 bucket access check succeeded for %s", bucket_name)
            return jsonify({"bucket": bucket_name, "status": "reachable"})
        except (ClientError, BotoCoreError) as exc:
            logger.exception("S3 bucket access check failed")
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.get("/s3/object-head")
    def object_head():
        if not bucket_name:
            return bucket_not_configured_response()

        object_key = request.args.get("key", "").strip()
        if not object_key:
            return jsonify({"error": "query parameter 'key' is required"}), 400

        try:
            response = s3_client.head_object(Bucket=bucket_name, Key=object_key)
            last_modified = response.get("LastModified")
            return jsonify(
                {
                    "bucket": bucket_name,
                    "key": object_key,
                    "size": response.get("ContentLength"),
                    "contentType": response.get("ContentType"),
                    "etag": response.get("ETag"),
                    "lastModified": last_modified.isoformat() if last_modified else None,
                    "metadata": response.get("Metadata", {}),
                }
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to fetch metadata for s3://%s/%s", bucket_name, object_key)
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.get("/s3/object-json")
    def get_json_object():
        if not bucket_name:
            return bucket_not_configured_response()

        object_key = request.args.get("key", "").strip()
        if not object_key:
            return jsonify({"error": "query parameter 'key' is required"}), 400

        try:
            response = s3_client.get_object(Bucket=bucket_name, Key=object_key)
            raw_body = response["Body"].read()
            text_body = raw_body.decode("utf-8")
            parsed_json = json.loads(text_body)
            return jsonify(
                {
                    "bucket": bucket_name,
                    "key": object_key,
                    "contentType": response.get("ContentType"),
                    "content": parsed_json,
                }
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            return jsonify({"error": "object content is not valid UTF-8 JSON", "key": object_key}), 400
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to read JSON object from s3://%s/%s", bucket_name, object_key)
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.post("/s3/upload-demo")
    def upload_demo():
        if not bucket_name:
            return bucket_not_configured_response()

        object_key = request.args.get("key", f"demo/demo-{int(time.time())}.txt")
        body = (
            "AWS Cloud Lab demo object\n"
            f"uploaded_at={datetime.now(timezone.utc).isoformat()}\n"
            f"source_ip={request.headers.get('X-Forwarded-For', request.remote_addr)}\n"
        )

        try:
            s3_client.put_object(Bucket=bucket_name, Key=object_key, Body=body.encode("utf-8"))
            logger.info("Uploaded demo object to s3://%s/%s", bucket_name, object_key)
            record_audit("upload-demo", "success", key=object_key)
            return jsonify({"bucket": bucket_name, "key": object_key, "status": "uploaded"}), 201
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to upload demo object")
            record_audit("upload-demo", "error", key=object_key, detail=str(exc))
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.post("/s3/upload-json")
    def upload_json_document():
        if not bucket_name:
            return bucket_not_configured_response()

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        object_key_raw = payload.get("key", f"demo/json-{int(time.time())}.json")
        if not isinstance(object_key_raw, str) or not object_key_raw.strip():
            return jsonify({"error": "field 'key' must be a non-empty string when provided"}), 400
        object_key = object_key_raw.strip()

        if "content" not in payload:
            return jsonify({"error": "field 'content' is required"}), 400

        body = json.dumps(payload["content"], indent=2, sort_keys=True, default=str)

        try:
            s3_client.put_object(
                Bucket=bucket_name,
                Key=object_key,
                Body=body.encode("utf-8"),
                ContentType="application/json",
            )
            logger.info("Uploaded JSON document to s3://%s/%s", bucket_name, object_key)
            record_audit("upload-json", "success", key=object_key)
            return (
                jsonify(
                    {
                        "bucket": bucket_name,
                        "key": object_key,
                        "status": "uploaded",
                        "contentType": "application/json",
                    }
                ),
                201,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to upload JSON document")
            record_audit("upload-json", "error", key=object_key, detail=str(exc))
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.post("/s3/copy-object")
    def copy_object():
        if not bucket_name:
            return bucket_not_configured_response()

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        src_key = str(payload.get("sourceKey", "")).strip()
        dst_key = str(payload.get("destinationKey", "")).strip()
        if not src_key or not dst_key:
            return jsonify({"error": "fields 'sourceKey' and 'destinationKey' are required"}), 400

        metadata = payload.get("metadata")
        content_type = payload.get("contentType")

        copy_params: dict[str, Any] = {
            "Bucket": bucket_name,
            "CopySource": {"Bucket": bucket_name, "Key": src_key},
            "Key": dst_key,
        }

        if metadata is not None:
            if not isinstance(metadata, dict):
                return jsonify({"error": "field 'metadata' must be an object when provided"}), 400
            copy_params["Metadata"] = {str(k): str(v) for k, v in metadata.items()}
            copy_params["MetadataDirective"] = "REPLACE"

        if content_type is not None:
            if not isinstance(content_type, str) or not content_type.strip():
                return jsonify({"error": "field 'contentType' must be a non-empty string when provided"}), 400
            copy_params["ContentType"] = content_type.strip()
            copy_params["MetadataDirective"] = "REPLACE"

        try:
            s3_client.copy_object(**copy_params)
            record_audit("copy-object", "success", key=dst_key, detail=f"from:{src_key}")
            return jsonify({"bucket": bucket_name, "sourceKey": src_key, "destinationKey": dst_key, "status": "copied"}), 201
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to copy object from s3://%s/%s to s3://%s/%s", bucket_name, src_key, bucket_name, dst_key)
            record_audit("copy-object", "error", key=dst_key, detail=str(exc))
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.post("/s3/batch-delete")
    def batch_delete_objects():
        if not bucket_name:
            return bucket_not_configured_response()

        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return jsonify({"error": "request body must be a JSON object"}), 400

        keys_raw = payload.get("keys")
        if not isinstance(keys_raw, list) or not keys_raw:
            return jsonify({"error": "field 'keys' must be a non-empty array"}), 400

        keys = []
        for item in keys_raw:
            if not isinstance(item, str) or not item.strip():
                return jsonify({"error": "all 'keys' items must be non-empty strings"}), 400
            keys.append(item.strip())

        if len(keys) > 1000:
            return jsonify({"error": "field 'keys' supports up to 1000 items"}), 400

        try:
            response = s3_client.delete_objects(
                Bucket=bucket_name,
                Delete={"Objects": [{"Key": key} for key in keys], "Quiet": False},
            )
            deleted = [item.get("Key") for item in response.get("Deleted", [])]
            errors = response.get("Errors", [])
            status_code = 207 if errors else 200
            record_audit("batch-delete", "partial" if errors else "success", detail=f"requested:{len(keys)}")
            return (
                jsonify(
                    {
                        "bucket": bucket_name,
                        "requested": len(keys),
                        "deletedCount": len(deleted),
                        "deleted": deleted,
                        "errors": errors,
                    }
                ),
                status_code,
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed batch delete for bucket %s", bucket_name)
            record_audit("batch-delete", "error", detail=str(exc))
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.get("/s3/list")
    def list_objects():
        if not bucket_name:
            return bucket_not_configured_response()

        prefix = request.args.get("prefix", "demo/")
        max_keys = parse_bounded_int(request.args.get("limit"), default=20, minimum=1, maximum=1000)
        cursor = request.args.get("cursor", "").strip()

        try:
            list_params: dict[str, Any] = {
                "Bucket": bucket_name,
                "Prefix": prefix,
                "MaxKeys": max_keys,
            }
            if cursor:
                list_params["ContinuationToken"] = cursor

            response = s3_client.list_objects_v2(**list_params)
            objects = [
                {
                    "key": item["Key"],
                    "size": item["Size"],
                    "lastModified": item["LastModified"].isoformat(),
                    "storageClass": item.get("StorageClass", "STANDARD"),
                }
                for item in response.get("Contents", [])
            ]
            return jsonify(
                {
                    "bucket": bucket_name,
                    "prefix": prefix,
                    "count": len(objects),
                    "truncated": response.get("IsTruncated", False),
                    "nextCursor": response.get("NextContinuationToken"),
                    "objects": objects,
                }
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to list objects in s3://%s/%s", bucket_name, prefix)
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.get("/s3/stats")
    def s3_stats():
        if not bucket_name:
            return bucket_not_configured_response()

        prefix = request.args.get("prefix", "demo/")
        storage_price_per_gb = float(os.getenv("S3_STANDARD_STORAGE_PRICE_PER_GB", "0.023"))

        total_bytes = 0
        object_count = 0
        largest_object: dict[str, Any] | None = None
        continuation_token: str | None = None

        try:
            while True:
                list_params: dict[str, Any] = {
                    "Bucket": bucket_name,
                    "Prefix": prefix,
                    "MaxKeys": 1000,
                }
                if continuation_token:
                    list_params["ContinuationToken"] = continuation_token

                response = s3_client.list_objects_v2(**list_params)
                contents = response.get("Contents", [])

                for item in contents:
                    size = int(item.get("Size", 0))
                    total_bytes += size
                    object_count += 1

                    if largest_object is None or size > largest_object["size"]:
                        largest_object = {
                            "key": item.get("Key"),
                            "size": size,
                            "lastModified": item.get("LastModified").isoformat() if item.get("LastModified") else None,
                        }

                if not response.get("IsTruncated"):
                    break

                continuation_token = response.get("NextContinuationToken")
                if not continuation_token:
                    break

            total_gb = total_bytes / (1024 ** 3)
            estimated_monthly_cost_usd = round(total_gb * storage_price_per_gb, 4)

            return jsonify(
                {
                    "bucket": bucket_name,
                    "prefix": prefix,
                    "objectCount": object_count,
                    "totalBytes": total_bytes,
                    "totalGiB": round(total_gb, 6),
                    "storageClassAssumed": "STANDARD",
                    "estimatedMonthlyStorageCostUsd": estimated_monthly_cost_usd,
                    "largestObject": largest_object,
                    "sampledAt": datetime.now(timezone.utc).isoformat(),
                }
            )
        except ValueError:
            return jsonify({"error": "invalid S3_STANDARD_STORAGE_PRICE_PER_GB value"}), 500
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to calculate s3 stats for s3://%s/%s", bucket_name, prefix)
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.get("/s3/presign-get")
    def presign_get():
        if not bucket_name:
            return bucket_not_configured_response()

        object_key = request.args.get("key", "").strip()
        if not object_key:
            return jsonify({"error": "query parameter 'key' is required"}), 400

        expires_in = parse_bounded_int(request.args.get("expires"), default=300, minimum=60, maximum=3600)

        try:
            presigned_url = s3_client.generate_presigned_url(
                "get_object",
                Params={"Bucket": bucket_name, "Key": object_key},
                ExpiresIn=expires_in,
            )
            return jsonify(
                {
                    "bucket": bucket_name,
                    "key": object_key,
                    "expiresIn": expires_in,
                    "url": presigned_url,
                }
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to generate pre-signed URL for s3://%s/%s", bucket_name, object_key)
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.get("/s3/presign-put")
    def presign_put():
        if not bucket_name:
            return bucket_not_configured_response()

        object_key = request.args.get("key", "").strip()
        if not object_key:
            return jsonify({"error": "query parameter 'key' is required"}), 400

        expires_in = parse_bounded_int(request.args.get("expires"), default=300, minimum=60, maximum=3600)
        content_type = request.args.get("contentType", "application/octet-stream").strip()
        if not content_type:
            content_type = "application/octet-stream"

        try:
            presigned_url = s3_client.generate_presigned_url(
                "put_object",
                Params={"Bucket": bucket_name, "Key": object_key, "ContentType": content_type},
                ExpiresIn=expires_in,
            )
            return jsonify(
                {
                    "bucket": bucket_name,
                    "key": object_key,
                    "expiresIn": expires_in,
                    "method": "PUT",
                    "contentType": content_type,
                    "url": presigned_url,
                }
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to generate pre-signed PUT URL for s3://%s/%s", bucket_name, object_key)
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.delete("/s3/object")
    def delete_object():
        if not bucket_name:
            return bucket_not_configured_response()

        object_key = request.args.get("key", "").strip()
        if not object_key:
            return jsonify({"error": "query parameter 'key' is required"}), 400

        try:
            s3_client.delete_object(Bucket=bucket_name, Key=object_key)
            logger.info("Deleted object from s3://%s/%s", bucket_name, object_key)
            record_audit("delete-object", "success", key=object_key)
            return jsonify({"bucket": bucket_name, "key": object_key, "status": "deleted"}), 200
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to delete object from s3://%s/%s", bucket_name, object_key)
            record_audit("delete-object", "error", key=object_key, detail=str(exc))
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.get("/stress")
    def stress():
        requested_seconds = request.args.get("seconds", default=10, type=int)
        duration = max(1, min(requested_seconds, 20))
        logger.warning("CPU stress endpoint invoked for %s seconds", duration)

        stop_at = time.time() + duration
        counter = 0
        while time.time() < stop_at:
            counter += sum(number * number for number in range(2500))

        return jsonify(
            {
                "status": "completed",
                "seconds": duration,
                "counter": counter,
                "warning": "Use only for CloudWatch alarm demonstrations.",
            }
        )

    return app


app = create_app()
