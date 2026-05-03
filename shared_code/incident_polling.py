import datetime
import logging

from shared_code.iot_logic import (
    get_sql_connection,
    handle_open_event,
    handle_recover_event,
)


BATCH_SIZE = 50
STALE_PROCESSING_MINUTES = 10


def process_incident_events_once():
    """
    Poll dbo.IncidentEvents for unprocessed rows and process them.

    Expected columns:
      - EventId
      - DeviceId
      - IncidentType
      - EventType
      - EventUtc
      - CreatedUtc
      - StartUtc
      - DetectedUtc
      - ProcessedUtc
      - ProcessingError
      - ProcessingStartedUtc
      - ProcessingAttempts
    """

    conn = get_sql_connection()
    cursor = conn.cursor()

    processed = 0
    failed = 0
    skipped = 0
    details = []

    try:
        cursor.execute("""
            SELECT TOP (%s)
                EventId,
                DeviceId,
                IncidentType,
                EventType,
                EventUtc,
                StartUtc,
                DetectedUtc
            FROM dbo.IncidentEvents WITH (READPAST)
            WHERE ProcessedUtc IS NULL
              AND (
                    ProcessingStartedUtc IS NULL
                    OR ProcessingStartedUtc < DATEADD(minute, -%s, SYSUTCDATETIME())
                  )
            ORDER BY EventUtc ASC, EventId ASC
        """, (BATCH_SIZE, STALE_PROCESSING_MINUTES))

        rows = cursor.fetchall()

        if not rows:
            return {
                "status": "ok",
                "message": "No unprocessed incident events found.",
                "processed": 0,
                "failed": 0,
                "skipped": 0,
                "details": []
            }

        for row in rows:
            event_id = row[0]
            device_id = row[1]
            incident_type = row[2]
            event_type = row[3]
            event_utc = row[4]
            start_utc = row[5]
            detected_utc = row[6]

            try:
                cursor.execute("""
                    UPDATE dbo.IncidentEvents
                    SET ProcessingStartedUtc = SYSUTCDATETIME(),
                        ProcessingAttempts = ProcessingAttempts + 1,
                        ProcessingError = NULL
                    WHERE EventId = %s
                      AND ProcessedUtc IS NULL
                """, (event_id,))
                conn.commit()

                if not device_id or not incident_type or not event_type:
                    skipped += 1

                    cursor.execute("""
                        UPDATE dbo.IncidentEvents
                        SET ProcessedUtc = SYSUTCDATETIME(),
                            ProcessingError = %s
                        WHERE EventId = %s
                    """, ("Skipped: missing DeviceId, IncidentType, or EventType", event_id))
                    conn.commit()

                    details.append({
                        "EventId": event_id,
                        "status": "skipped",
                        "reason": "missing required fields"
                    })
                    continue

                event_type_upper = str(event_type).upper()

                if event_type_upper == "OPEN":
                    handle_open_event(
                        cursor,
                        conn,
                        device_id,
                        incident_type,
                        event_utc,
                        start_utc,
                        detected_utc
                    )

                elif event_type_upper == "RECOVER":
                    handle_recover_event(
                        cursor,
                        conn,
                        device_id,
                        incident_type,
                        event_utc
                    )

                else:
                    skipped += 1

                    cursor.execute("""
                        UPDATE dbo.IncidentEvents
                        SET ProcessedUtc = SYSUTCDATETIME(),
                            ProcessingError = %s
                        WHERE EventId = %s
                    """, (f"Skipped: unsupported EventType {event_type}", event_id))
                    conn.commit()

                    details.append({
                        "EventId": event_id,
                        "status": "skipped",
                        "reason": f"unsupported EventType {event_type}"
                    })
                    continue

                cursor.execute("""
                    UPDATE dbo.IncidentEvents
                    SET ProcessedUtc = SYSUTCDATETIME(),
                        ProcessingError = NULL
                    WHERE EventId = %s
                """, (event_id,))
                conn.commit()

                processed += 1

                details.append({
                    "EventId": event_id,
                    "status": "processed",
                    "DeviceId": device_id,
                    "IncidentType": incident_type,
                    "EventType": event_type
                })

            except Exception as row_ex:
                failed += 1
                error_text = str(row_ex)

                logging.error(
                    f"Failed to process EventId={event_id}: {error_text}"
                )

                cursor.execute("""
                    UPDATE dbo.IncidentEvents
                    SET ProcessingError = %s
                    WHERE EventId = %s
                """, (error_text, event_id))
                conn.commit()

                details.append({
                    "EventId": event_id,
                    "status": "failed",
                    "error": error_text
                })

        return {
            "status": "ok",
            "message": "Incident event polling completed.",
            "processed": processed,
            "failed": failed,
            "skipped": skipped,
            "details": details
        }

    finally:
        cursor.close()
        conn.close()
