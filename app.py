import logging
import os
import time
import json
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

    def bucket_not_configured_response():
        return jsonify({"error": "S3_BUCKET is not configured"}), 400

    def parse_bounded_int(raw_value: Any, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(value, maximum))

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
                    "GET /aws/identity",
                    "GET /s3/check",
                    "GET /s3/object-head?key=<object-key>",
                    "GET /s3/list?prefix=demo/&limit=20",
                    "GET /s3/presign-get?key=<object-key>&expires=300",
                    "GET /s3/presign-put?key=<object-key>&expires=300&contentType=text/plain",
                    "POST /s3/upload-demo?key=demo/file.txt",
                    "POST /s3/upload-json",
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
            return jsonify({"bucket": bucket_name, "key": object_key, "status": "uploaded"}), 201
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to upload demo object")
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
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.get("/s3/list")
    def list_objects():
        if not bucket_name:
            return bucket_not_configured_response()

        prefix = request.args.get("prefix", "demo/")
        max_keys = parse_bounded_int(request.args.get("limit"), default=20, minimum=1, maximum=100)

        try:
            response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=prefix, MaxKeys=max_keys)
            objects = [
                {
                    "key": item["Key"],
                    "size": item["Size"],
                    "lastModified": item["LastModified"].isoformat(),
                }
                for item in response.get("Contents", [])
            ]
            return jsonify(
                {
                    "bucket": bucket_name,
                    "prefix": prefix,
                    "count": len(objects),
                    "truncated": response.get("IsTruncated", False),
                    "objects": objects,
                }
            )
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to list objects in s3://%s/%s", bucket_name, prefix)
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
            return jsonify({"bucket": bucket_name, "key": object_key, "status": "deleted"}), 200
        except (ClientError, BotoCoreError) as exc:
            logger.exception("Failed to delete object from s3://%s/%s", bucket_name, object_key)
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
