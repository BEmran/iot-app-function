import json
import os
import azure.functions as func

from shared_code.iot_logic import get_sql_connection, send_email


def _to_bool(value: str) -> bool:
    return str(value).lower() in ["1", "true", "yes", "y"]


def main(req: func.HttpRequest) -> func.HttpResponse:
    alert_to = req.params.get("to") or os.environ.get("AlertEmailTo")
    incident_type_filter = req.params.get("incidentType")
    dry_run = _to_bool(req.params.get("dryRun", "false"))

    try:
        limit = int(req.params.get("limit", "10"))
        if limit < 1:
            limit = 1
        if limit > 50:
            limit = 50
    except Exception:
        limit = 10

    if not alert_to and not dry_run:
        return func.HttpResponse(
            json.dumps({
                "status": "config_error",
                "error": "AlertEmailTo is missing and no 'to' query parameter was provided."
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )

    conn = get_sql_connection()
    cursor = conn.cursor()

    sent = 0
    failed = 0
    dry_run_count = 0
    details = []

    try:
        # -------------------------
        # Open incident alerts
        # -------------------------
        where_open = "State = 'Open' AND LastAlertSentUtc IS NULL"
        params_open = []

        if incident_type_filter:
            where_open += " AND IncidentType = %s"
            params_open.append(incident_type_filter)

        cursor.execute(f"""
            SELECT TOP ({limit})
                IncidentId,
                DeviceId,
                IncidentType,
                State,
                StartUtc,
                DetectedUtc,
                AutoActionType,
                AutoActionResultCode
            FROM dbo.Incidents
            WHERE {where_open}
            ORDER BY IncidentId ASC
        """, tuple(params_open))

        open_incidents = cursor.fetchall()

        for row in open_incidents:
            (
                incident_id,
                device_id,
                incident_type,
                state,
                start_utc,
                detected_utc,
                auto_action_type,
                auto_action_result_code,
            ) = row

            subject = f"IoT Alert: {incident_type} on {device_id}"

            body = f"""
            <html>
            <body>
                <p><strong>An incident has been detected on your IoT monitoring system.</strong></p>

                <table cellpadding="6" style="border-collapse:collapse;">
                    <tr><td><strong>Device</strong></td><td>{device_id}</td></tr>
                    <tr><td><strong>Incident Type</strong></td><td>{incident_type}</td></tr>
                    <tr><td><strong>State</strong></td><td style="color:red;">{state}</td></tr>
                    <tr><td><strong>Started UTC</strong></td><td>{start_utc}</td></tr>
                    <tr><td><strong>Detected UTC</strong></td><td>{detected_utc}</td></tr>
                    <tr><td><strong>Incident ID</strong></td><td>{incident_id}</td></tr>
                    <tr><td><strong>Auto Action</strong></td><td>{auto_action_type or 'None'}</td></tr>
                    <tr><td><strong>Action Result</strong></td><td>{auto_action_result_code if auto_action_result_code is not None else 'N/A'}</td></tr>
                </table>

                <p>Please investigate and take corrective action if needed.</p>
                <p><em>-- IoT Monitoring System</em></p>
            </body>
            </html>
            """

            if dry_run:
                dry_run_count += 1
                details.append({
                    "IncidentId": incident_id,
                    "type": "open_alert",
                    "status": "dry_run",
                    "DeviceId": device_id,
                    "IncidentType": incident_type
                })
                continue

            try:
                send_email(alert_to, subject, body)

                cursor.execute("""
                    UPDATE dbo.Incidents
                    SET LastAlertSentUtc = GETUTCDATE()
                    WHERE IncidentId = %s
                """, (incident_id,))
                conn.commit()

                sent += 1
                details.append({
                    "IncidentId": incident_id,
                    "type": "open_alert",
                    "status": "sent",
                    "to": alert_to,
                    "DeviceId": device_id,
                    "IncidentType": incident_type
                })

            except Exception as email_ex:
                failed += 1
                details.append({
                    "IncidentId": incident_id,
                    "type": "open_alert",
                    "status": "failed",
                    "error": str(email_ex)
                })

        # -------------------------
        # Recovery incident alerts
        # -------------------------
        where_recovered = "State = 'Recovered' AND RecoveryAlertSentUtc IS NULL"
        params_recovered = []

        if incident_type_filter:
            where_recovered += " AND IncidentType = %s"
            params_recovered.append(incident_type_filter)

        cursor.execute(f"""
            SELECT TOP ({limit})
                IncidentId,
                DeviceId,
                IncidentType,
                State,
                StartUtc,
                RecoveryUtc,
                DurationSec
            FROM dbo.Incidents
            WHERE {where_recovered}
            ORDER BY IncidentId ASC
        """, tuple(params_recovered))

        recovered_incidents = cursor.fetchall()

        for row in recovered_incidents:
            (
                incident_id,
                device_id,
                incident_type,
                state,
                start_utc,
                recovery_utc,
                duration_sec,
            ) = row

            subject = f"IoT Recovered: {incident_type} on {device_id}"

            body = f"""
            <html>
            <body>
                <p><strong>The following incident has been resolved.</strong></p>

                <table cellpadding="6" style="border-collapse:collapse;">
                    <tr><td><strong>Device</strong></td><td>{device_id}</td></tr>
                    <tr><td><strong>Incident Type</strong></td><td>{incident_type}</td></tr>
                    <tr><td><strong>State</strong></td><td style="color:green;">{state}</td></tr>
                    <tr><td><strong>Started UTC</strong></td><td>{start_utc}</td></tr>
                    <tr><td><strong>Recovered UTC</strong></td><td>{recovery_utc}</td></tr>
                    <tr><td><strong>Duration</strong></td><td>{duration_sec} seconds</td></tr>
                    <tr><td><strong>Incident ID</strong></td><td>{incident_id}</td></tr>
                </table>

                <p><em>-- IoT Monitoring System</em></p>
            </body>
            </html>
            """

            if dry_run:
                dry_run_count += 1
                details.append({
                    "IncidentId": incident_id,
                    "type": "recovery_alert",
                    "status": "dry_run",
                    "DeviceId": device_id,
                    "IncidentType": incident_type
                })
                continue

            try:
                send_email(alert_to, subject, body)

                cursor.execute("""
                    UPDATE dbo.Incidents
                    SET RecoveryAlertSentUtc = GETUTCDATE()
                    WHERE IncidentId = %s
                """, (incident_id,))
                conn.commit()

                sent += 1
                details.append({
                    "IncidentId": incident_id,
                    "type": "recovery_alert",
                    "status": "sent",
                    "to": alert_to,
                    "DeviceId": device_id,
                    "IncidentType": incident_type
                })

            except Exception as email_ex:
                failed += 1
                details.append({
                    "IncidentId": incident_id,
                    "type": "recovery_alert",
                    "status": "failed",
                    "error": str(email_ex)
                })

        return func.HttpResponse(
            json.dumps({
                "status": "ok",
                "dry_run": dry_run,
                "sent": sent,
                "failed": failed,
                "dry_run_count": dry_run_count,
                "incident_type_filter": incident_type_filter,
                "details": details
            }, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
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
        cursor.close()
        conn.close()
