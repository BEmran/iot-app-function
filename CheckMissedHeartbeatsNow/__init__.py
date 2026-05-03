import json
import logging
from datetime import datetime, timezone

import azure.functions as func

from shared_code.iot_logic import get_sql_connection


INCIDENT_TYPE = "IOT_DEVICE_DISCONNECTED"


def main(req: func.HttpRequest) -> func.HttpResponse:
    conn = None
    cursor = None

    try:
        threshold_minutes = int(req.params.get("thresholdMinutes", "3"))
        device_id_filter = req.params.get("deviceId")
        dry_run = req.params.get("dryRun", "false").lower() == "true"

        conn = get_sql_connection()
        cursor = conn.cursor(as_dict=True)

        # Devices table is kept as the source of configured devices.
        # Last heartbeat is taken from HeartbeatEvents.
        if device_id_filter:
            cursor.execute("""
                SELECT
                    d.DeviceId,
                    MAX(h.IngestedUtc) AS LastHeartbeatUtc
                FROM dbo.Devices d
                LEFT JOIN dbo.HeartbeatEvents h
                    ON h.DeviceId = d.DeviceId
                WHERE d.DeviceId = %s
                GROUP BY d.DeviceId
            """, (device_id_filter,))
        else:
            cursor.execute("""
                SELECT
                    d.DeviceId,
                    MAX(h.IngestedUtc) AS LastHeartbeatUtc
                FROM dbo.Devices d
                LEFT JOIN dbo.HeartbeatEvents h
                    ON h.DeviceId = d.DeviceId
                GROUP BY d.DeviceId
            """)

        devices = cursor.fetchall()

        opened = 0
        recovered = 0
        unchanged = 0
        details = []

        for d in devices:
            device_id = d["DeviceId"]
            last_hb = d["LastHeartbeatUtc"]

            # SQL-side current UTC and threshold decision keeps timing consistent.
            cursor.execute("""
                SELECT
                    SYSUTCDATETIME() AS NowUtc,
                    DATEADD(minute, -%s, SYSUTCDATETIME()) AS ThresholdUtc
            """, (threshold_minutes,))
            t = cursor.fetchone()
            now_utc = t["NowUtc"]
            threshold_utc = t["ThresholdUtc"]

            is_missing = (last_hb is None) or (last_hb < threshold_utc)

            # Check currently open disconnected incident
            cursor.execute("""
                SELECT TOP 1 IncidentId
                FROM dbo.Incidents
                WHERE DeviceId = %s
                  AND IncidentType = %s
                  AND State = 'Open'
                ORDER BY IncidentId DESC
            """, (device_id, INCIDENT_TYPE))
            open_incident = cursor.fetchone()

            if is_missing and not open_incident:
                # Create OPEN event only once while missing
                if not dry_run:
                    cursor.execute("""
                        INSERT INTO dbo.IncidentEvents
                            (DeviceId, IncidentType, EventType, EventUtc, StartUtc, DetectedUtc)
                        VALUES
                            (%s, %s, 'OPEN', SYSUTCDATETIME(), %s, SYSUTCDATETIME())
                    """, (device_id, INCIDENT_TYPE, last_hb if last_hb else now_utc))
                opened += 1
                details.append({
                    "DeviceId": device_id,
                    "action": "OPEN_EVENT_CREATED" if not dry_run else "DRY_RUN_OPEN_EVENT",
                    "IncidentType": INCIDENT_TYPE,
                    "LastHeartbeatUtc": str(last_hb),
                    "ThresholdUtc": str(threshold_utc)
                })

            elif (not is_missing) and open_incident:
                # Create RECOVER event when heartbeat becomes recent again
                if not dry_run:
                    cursor.execute("""
                        INSERT INTO dbo.IncidentEvents
                            (DeviceId, IncidentType, EventType, EventUtc, StartUtc, DetectedUtc)
                        VALUES
                            (%s, %s, 'RECOVER', SYSUTCDATETIME(), NULL, NULL)
                    """, (device_id, INCIDENT_TYPE))
                recovered += 1
                details.append({
                    "DeviceId": device_id,
                    "action": "RECOVER_EVENT_CREATED" if not dry_run else "DRY_RUN_RECOVER_EVENT",
                    "IncidentType": INCIDENT_TYPE,
                    "LastHeartbeatUtc": str(last_hb),
                    "ThresholdUtc": str(threshold_utc)
                })

            else:
                unchanged += 1
                details.append({
                    "DeviceId": device_id,
                    "action": "NO_CHANGE",
                    "IncidentType": INCIDENT_TYPE,
                    "is_missing": is_missing,
                    "open_incident_exists": bool(open_incident),
                    "LastHeartbeatUtc": str(last_hb),
                    "ThresholdUtc": str(threshold_utc)
                })

        if not dry_run:
            conn.commit()
        else:
            conn.rollback()

        return func.HttpResponse(
            json.dumps({
                "status": "ok",
                "dry_run": dry_run,
                "threshold_minutes": threshold_minutes,
                "incident_type": INCIDENT_TYPE,
                "opened": opened,
                "recovered": recovered,
                "unchanged": unchanged,
                "details": details
            }, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        logging.exception("check-missed-heartbeats-now failed")
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