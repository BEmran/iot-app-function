import json
import logging
import os
import datetime
import smtplib
import pymssql
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

HEARTBEAT_LOSS_THRESHOLD_MINUTES = 3


def utc_now():
    return datetime.datetime.utcnow()


def get_sql_connection():
    return pymssql.connect(
        server=os.environ["SqlServer"],
        user=os.environ["SqlUser"],
        password=os.environ["SqlPassword"],
        database=os.environ["SqlDatabase"],
        port=1433,
        tds_version="7.4"
    )


def get_iothub_registry_manager():
    from azure.iot.hub import IoTHubRegistryManager

    cs = os.environ["IoTHubServiceConnectionString"]
    return IoTHubRegistryManager.from_connection_string(cs)

def invoke_reboot_slave(device_id: str):
    from azure.iot.hub.models import CloudToDeviceMethod

    registry_manager = get_iothub_registry_manager()

    method = CloudToDeviceMethod(
        method_name="reboot_slave",
        payload={},
        response_timeout_in_seconds=30,
        connect_timeout_in_seconds=30,
    )

    response = registry_manager.invoke_device_method(device_id, method)

    return {
        "status": getattr(response, "status", None),
        "payload": getattr(response, "payload", None),
    }


def send_email(to_address: str, subject: str, html_body: str):
    smtp_host = os.environ.get("AlertSmtpHost", "smtp-mail.outlook.com")
    smtp_port = int(os.environ.get("AlertSmtpPort", "587"))
    smtp_user = os.environ["AlertEmailFrom"]
    smtp_password = os.environ["AlertEmailPassword"]

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_address
    msg.attach(MIMEText(html_body, "html"))

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_password)
        server.sendmail(smtp_user, to_address, msg.as_string())


def handle_open_event(cursor, conn, device_id, incident_type, event_utc, start_utc, detected_utc):
    cursor.execute(
        "SELECT COUNT(*) FROM dbo.Incidents WHERE DeviceId = %s AND IncidentType = %s AND State = 'Open'",
        (device_id, incident_type)
    )

    if cursor.fetchone()[0] > 0:
        return

    start_utc_to_store = start_utc or event_utc
    detected_utc_to_store = detected_utc or event_utc

    cursor.execute("""
        INSERT INTO dbo.Incidents (DeviceId, IncidentType, StartUtc, DetectedUtc, State)
        VALUES (%s, %s, %s, %s, 'Open')
    """, (device_id, incident_type, start_utc_to_store, detected_utc_to_store))

    conn.commit()

    if incident_type == "SLAVE_OFFLINE":
        result_status = None
        result_payload = None

        try:
            reboot_result = invoke_reboot_slave(device_id)
            result_status = reboot_result.get("status")
            result_payload = json.dumps(reboot_result.get("payload")) if reboot_result.get("payload") is not None else None
        except Exception as reboot_ex:
            result_status = -1
            result_payload = json.dumps({"error": str(reboot_ex)})

        cursor.execute("""
            UPDATE dbo.Incidents
            SET AutoActionTriggered = 1,
                AutoActionType = 'reboot_slave',
                AutoActionUtc = GETUTCDATE(),
                AutoActionResultCode = %s,
                AutoActionResultMessage = %s
            WHERE DeviceId = %s AND IncidentType = %s AND State = 'Open'
        """, (result_status, result_payload, device_id, incident_type))

        cursor.execute("""
            INSERT INTO dbo.CommandAudit
                (DeviceId, MethodName, RequestedBy, RequestedUtc, Payload, ResultCode, ResultMessage, CompletedUtc)
            VALUES (%s, 'reboot_slave', 'auto-function', GETUTCDATE(), %s, %s, %s, GETUTCDATE())
        """, (device_id, "{}", result_status, result_payload))

        conn.commit()


def handle_recover_event(cursor, conn, device_id, incident_type, event_utc):
    cursor.execute("""
        SELECT TOP 1 IncidentId, StartUtc
        FROM dbo.Incidents
        WHERE DeviceId = %s AND IncidentType = %s AND State = 'Open'
        ORDER BY IncidentId DESC
    """, (device_id, incident_type))

    row = cursor.fetchone()

    if not row:
        return

    incident_id = row[0]

    cursor.execute("""
        UPDATE dbo.Incidents
        SET RecoveryUtc = %s,
            DurationSec = DATEDIFF(second, StartUtc, %s),
            State = 'Recovered'
        WHERE IncidentId = %s
    """, (event_utc, event_utc, incident_id))

    conn.commit()


def parse_json_lines(content: str):
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    records = []

    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            logging.warning(f"Skipping invalid JSON line: {line}")

    return records

def apply_status_based_recovery_rules(cursor, conn):
    """
    Fallback recovery logic based directly on HeartbeatEvents.

    Rules:
    1. IOT_DEVICE_DISCONNECTED recovers when any newer heartbeat arrives.
    2. SLAVE_OFFLINE recovers when a newer heartbeat says ONLINE.
    3. SLAVE_OFFLINE transitions/closes when a newer heartbeat says DOWN.
    4. SLAVE_DOWN recovers when a newer heartbeat says ONLINE.
    5. SLAVE_DOWN transitions/closes when a newer heartbeat says OFFLINE.

    This prevents old open incidents from remaining open when the device state
    has already changed but no matching RECOVER event was generated.
    """

    recovered = {
        "iot_device_disconnected": 0,
        "slave_offline_to_online": 0,
        "slave_offline_to_down": 0,
        "slave_down_to_online": 0,
        "slave_down_to_offline": 0
    }

    # ------------------------------------------------------------
    # Rule 1:
    # IOT_DEVICE_DISCONNECTED closes when any newer heartbeat arrives.
    # ------------------------------------------------------------
    cursor.execute("""
        UPDATE i
        SET
            RecoveryUtc = h.RecoveryUtc,
            DurationSec = DATEDIFF(second, i.StartUtc, h.RecoveryUtc),
            State = 'Recovered'
        FROM dbo.Incidents i
        CROSS APPLY
        (
            SELECT TOP 1
                hb.DeviceUtcTs AS RecoveryUtc
            FROM dbo.HeartbeatEvents hb
            WHERE
                hb.DeviceId = i.DeviceId
                AND hb.DeviceUtcTs > i.StartUtc
            ORDER BY hb.DeviceUtcTs ASC
        ) h
        WHERE
            i.State = 'Open'
            AND i.IncidentType = 'IOT_DEVICE_DISCONNECTED';
    """)

    recovered["iot_device_disconnected"] = cursor.rowcount
    conn.commit()

    # ------------------------------------------------------------
    # Rule 2:
    # SLAVE_OFFLINE closes when newer heartbeat says ONLINE.
    # ------------------------------------------------------------
    cursor.execute("""
        UPDATE i
        SET
            RecoveryUtc = h.RecoveryUtc,
            DurationSec = DATEDIFF(second, i.StartUtc, h.RecoveryUtc),
            State = 'Recovered'
        FROM dbo.Incidents i
        CROSS APPLY
        (
            SELECT TOP 1
                hb.DeviceUtcTs AS RecoveryUtc
            FROM dbo.HeartbeatEvents hb
            WHERE
                hb.DeviceId = i.DeviceId
                AND hb.DeviceUtcTs > i.StartUtc
                AND UPPER(hb.SlaveStatus) = 'ONLINE'
            ORDER BY hb.DeviceUtcTs ASC
        ) h
        WHERE
            i.State = 'Open'
            AND i.IncidentType = 'SLAVE_OFFLINE';
    """)

    recovered["slave_offline_to_online"] = cursor.rowcount
    conn.commit()

    # ------------------------------------------------------------
    # Rule 3:
    # SLAVE_OFFLINE closes when newer heartbeat says DOWN.
    # This means the condition changed/escalated from OFFLINE to DOWN.
    # A separate SLAVE_DOWN incident may be opened by the normal detection logic.
    # ------------------------------------------------------------
    cursor.execute("""
        UPDATE i
        SET
            RecoveryUtc = h.RecoveryUtc,
            DurationSec = DATEDIFF(second, i.StartUtc, h.RecoveryUtc),
            State = 'Recovered'
        FROM dbo.Incidents i
        CROSS APPLY
        (
            SELECT TOP 1
                hb.DeviceUtcTs AS RecoveryUtc
            FROM dbo.HeartbeatEvents hb
            WHERE
                hb.DeviceId = i.DeviceId
                AND hb.DeviceUtcTs > i.StartUtc
                AND UPPER(hb.SlaveStatus) = 'DOWN'
            ORDER BY hb.DeviceUtcTs ASC
        ) h
        WHERE
            i.State = 'Open'
            AND i.IncidentType = 'SLAVE_OFFLINE';
    """)

    recovered["slave_offline_to_down"] = cursor.rowcount
    conn.commit()

    # ------------------------------------------------------------
    # Rule 4:
    # SLAVE_DOWN closes when newer heartbeat says ONLINE.
    # ------------------------------------------------------------
    cursor.execute("""
        UPDATE i
        SET
            RecoveryUtc = h.RecoveryUtc,
            DurationSec = DATEDIFF(second, i.StartUtc, h.RecoveryUtc),
            State = 'Recovered'
        FROM dbo.Incidents i
        CROSS APPLY
        (
            SELECT TOP 1
                hb.DeviceUtcTs AS RecoveryUtc
            FROM dbo.HeartbeatEvents hb
            WHERE
                hb.DeviceId = i.DeviceId
                AND hb.DeviceUtcTs > i.StartUtc
                AND UPPER(hb.SlaveStatus) = 'ONLINE'
            ORDER BY hb.DeviceUtcTs ASC
        ) h
        WHERE
            i.State = 'Open'
            AND i.IncidentType = 'SLAVE_DOWN';
    """)

    recovered["slave_down_to_online"] = cursor.rowcount
    conn.commit()

    # ------------------------------------------------------------
    # Rule 5:
    # SLAVE_DOWN closes when newer heartbeat says OFFLINE.
    # This means the condition changed from DOWN to OFFLINE.
    # A separate SLAVE_OFFLINE incident may be opened by the normal detection logic.
    # ------------------------------------------------------------
    cursor.execute("""
        UPDATE i
        SET
            RecoveryUtc = h.RecoveryUtc,
            DurationSec = DATEDIFF(second, i.StartUtc, h.RecoveryUtc),
            State = 'Recovered'
        FROM dbo.Incidents i
        CROSS APPLY
        (
            SELECT TOP 1
                hb.DeviceUtcTs AS RecoveryUtc
            FROM dbo.HeartbeatEvents hb
            WHERE
                hb.DeviceId = i.DeviceId
                AND hb.DeviceUtcTs > i.StartUtc
                AND UPPER(hb.SlaveStatus) = 'OFFLINE'
            ORDER BY hb.DeviceUtcTs ASC
        ) h
        WHERE
            i.State = 'Open'
            AND i.IncidentType = 'SLAVE_DOWN';
    """)

    recovered["slave_down_to_offline"] = cursor.rowcount
    conn.commit()

    recovered["total_recovered_by_status_rules"] = sum(recovered.values())

    return recovered