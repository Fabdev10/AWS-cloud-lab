import logging
import os
import time
from datetime import datetime, timezone

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

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"}), 200

    @app.get("/s3/check")
    def s3_check():
        if not bucket_name:
            return jsonify({"error": "S3_BUCKET is not configured"}), 400

        try:
            s3_client.head_bucket(Bucket=bucket_name)
            logger.info("S3 bucket access check succeeded for %s", bucket_name)
            return jsonify({"bucket": bucket_name, "status": "reachable"})
        except (ClientError, BotoCoreError) as exc:
            logger.exception("S3 bucket access check failed")
            return jsonify({"bucket": bucket_name, "status": "error", "detail": str(exc)}), 500

    @app.post("/s3/upload-demo")
    def upload_demo():
        if not bucket_name:
            return jsonify({"error": "S3_BUCKET is not configured"}), 400

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
