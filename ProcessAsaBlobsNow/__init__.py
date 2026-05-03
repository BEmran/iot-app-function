import os
import json
import logging
from datetime import datetime, timezone, timedelta

import azure.functions as func
from azure.storage.blob import BlobServiceClient

from shared_code.iot_logic import get_sql_connection


def parse_dt(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    s = str(value).replace("Z", "+00:00")
    return datetime.fromisoformat(s)


def iter_json_lines(text):
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        yield json.loads(line)


def blob_already_processed(cursor, blob_name):
    cursor.execute("""
        SELECT COUNT(1)
        FROM dbo.ProcessedAsaBlobs
        WHERE BlobName = %s
    """, (blob_name,))
    return cursor.fetchone()[0] > 0


def mark_blob_processed(cursor, blob_name, records_count, blob_type):
    cursor.execute("""
        INSERT INTO dbo.ProcessedAsaBlobs
            (BlobName, BlobType, RecordsCount, ProcessedUtc)
        VALUES
            (%s, %s, %s, SYSUTCDATETIME())
    """, (blob_name, blob_type, records_count))


def insert_heartbeat(cursor, row):
    device_id = row.get("DeviceId")
    device_utc_ts = parse_dt(row.get("DeviceUtcTs"))
    sequence_number = row.get("SequenceNumber")
    slave_status = row.get("SlaveStatus")
    ingested_utc = parse_dt(row.get("IngestedUtc"))
    raw_payload = json.dumps(row)

    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1
            FROM dbo.HeartbeatEvents
            WHERE DeviceId = %s
              AND DeviceUtcTs = %s
              AND SequenceNumber = %s
        )
        BEGIN
            INSERT INTO dbo.HeartbeatEvents
                (DeviceId, DeviceUtcTs, SequenceNumber, SlaveStatus, IngestedUtc, RawPayload)
            VALUES
                (%s, %s, %s, %s, %s, %s)
        END
    """, (
        device_id, device_utc_ts, sequence_number,
        device_id, device_utc_ts, sequence_number, slave_status, ingested_utc, raw_payload
    ))


def insert_incident_event(cursor, row):
    device_id = row.get("DeviceId")
    incident_type = row.get("IncidentType")
    event_type = row.get("EventType")
    event_utc = parse_dt(row.get("EventUtc"))
    start_utc = parse_dt(row.get("StartUtc"))
    detected_utc = parse_dt(row.get("DetectedUtc"))

    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1
            FROM dbo.IncidentEvents
            WHERE DeviceId = %s
              AND IncidentType = %s
              AND EventType = %s
              AND EventUtc = %s
        )
        BEGIN
            INSERT INTO dbo.IncidentEvents
                (DeviceId, IncidentType, EventType, EventUtc, StartUtc, DetectedUtc)
            VALUES
                (%s, %s, %s, %s, %s, %s)
        END
    """, (
        device_id, incident_type, event_type, event_utc,
        device_id, incident_type, event_type, event_utc, start_utc, detected_utc
    ))


def list_recent_blobs(container_client, prefix, hours_back=3):
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours_back)

    blobs = []
    for blob in container_client.list_blobs(name_starts_with=prefix):
        if blob.last_modified and blob.last_modified >= cutoff:
            blobs.append(blob.name)

    return sorted(blobs)


def process_prefix(container_client, cursor, prefix, blob_type, insert_func, limit):
    processed_blobs = 0
    processed_records = 0
    skipped_blobs = 0
    details = []

    blobs = list_recent_blobs(container_client, prefix)

    for blob_name in blobs[:limit]:
        if blob_already_processed(cursor, blob_name):
            skipped_blobs += 1
            continue

        blob_client = container_client.get_blob_client(blob_name)
        text = blob_client.download_blob().readall().decode("utf-8")

        count = 0
        for row in iter_json_lines(text):
            insert_func(cursor, row)
            count += 1

        mark_blob_processed(cursor, blob_name, count, blob_type)

        processed_blobs += 1
        processed_records += count
        details.append({
            "blob": blob_name,
            "type": blob_type,
            "records": count
        })

    return {
        "prefix": prefix,
        "blob_type": blob_type,
        "processed_blobs": processed_blobs,
        "skipped_blobs": skipped_blobs,
        "processed_records": processed_records,
        "details": details
    }


def main(req: func.HttpRequest) -> func.HttpResponse:
    conn = None
    cursor = None

    try:
        limit = int(req.params.get("limit", "20"))
        dry_run = req.params.get("dryRun", "false").lower() == "true"

        storage_conn = os.environ["AsaBlobStorageConnectionString"]
        container_name = os.environ.get("AsaBlobContainer", "asa-output")

        if dry_run:
            return func.HttpResponse(
                json.dumps({
                    "status": "dry_run_ok",
                    "message": "Configuration exists. No blobs processed.",
                    "container": container_name
                }, indent=2),
                status_code=200,
                mimetype="application/json"
            )

        blob_service = BlobServiceClient.from_connection_string(storage_conn)
        container_client = blob_service.get_container_client(container_name)

        conn = get_sql_connection()
        cursor = conn.cursor()

        # Ensure tracking table exists
        cursor.execute("""
            IF OBJECT_ID('dbo.ProcessedAsaBlobs', 'U') IS NULL
            BEGIN
                CREATE TABLE dbo.ProcessedAsaBlobs (
                    BlobName NVARCHAR(500) NOT NULL PRIMARY KEY,
                    BlobType NVARCHAR(50) NOT NULL,
                    RecordsCount INT NOT NULL,
                    ProcessedUtc DATETIME2 NOT NULL DEFAULT SYSUTCDATETIME()
                );
            END
        """)
        conn.commit()

        heartbeat_result = process_prefix(
            container_client=container_client,
            cursor=cursor,
            prefix="heartbeat-events/",
            blob_type="heartbeat",
            insert_func=insert_heartbeat,
            limit=limit
        )

        incident_result = process_prefix(
            container_client=container_client,
            cursor=cursor,
            prefix="incident-events/",
            blob_type="incident",
            insert_func=insert_incident_event,
            limit=limit
        )

        conn.commit()

        return func.HttpResponse(
            json.dumps({
                "status": "ok",
                "heartbeat": heartbeat_result,
                "incident": incident_result
            }, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.exception("process-asa-blobs-now failed")
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass

        return func.HttpResponse(
            json.dumps({
                "status": "error",
                "error_type": type(e).__name__,
                "error": str(e)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )

    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass