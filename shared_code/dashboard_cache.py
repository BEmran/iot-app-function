import datetime
from shared_code.iot_logic import get_sql_connection


def serialize_value(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    return value


def row_to_dict(cursor, row):
    columns = [col[0] for col in cursor.description]
    return {
        columns[i]: serialize_value(row[i])
        for i in range(len(columns))
    }


def refresh_operational_dashboard_cache():
    conn = get_sql_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("EXEC dbo.usp_RefreshIoTOperationalDashboardCache")
        conn.commit()

        return {
            "status": "ok",
            "message": "Operational dashboard cache refreshed successfully."
        }

    finally:
        cursor.close()
        conn.close()


def get_operational_dashboard_data():
    conn = get_sql_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            SELECT
                SummaryId,
                TotalDevices,
                OnlineDevices,
                SlaveOfflineDevices,
                SlaveDownDevices,
                DisconnectedDevices,
                NoHeartbeatDevices,
                StaleHeartbeatDevices,
                TotalOpenIncidents,
                SlaveOfflineIncidents,
                SlaveDownIncidents,
                IoTDisconnectedIncidents,
                LatestHeartbeatUtc,
                LatestHeartbeatAst,
                RefreshedUtc,
                RefreshedAst
            FROM dbo.IoTOperationalDashboardSummary
            ORDER BY SummaryId
        """)
        summary = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT
                DeviceId,
                SiteId,
                SiteCode,
                SiteName,
                Environment,
                RpiIp,
                SlaveIp,
                ProvisioningStatus,
                LastDeviceUtcTs,
                LastDeviceAstTs,
                LastHeartbeatUtc,
                LastHeartbeatAst,
                SequenceNumber,
                SlaveStatus,
                CurrentStatus,
                SecondsSinceLastHeartbeat,
                HeartbeatAgeMinutes,
                OpenIncidentCount,
                OldestOpenIncidentUtc,
                OldestOpenIncidentAst,
                LatestDetectedUtc,
                LatestDetectedAst,
                LatestOpenIncidentId,
                LatestOpenIncidentType,
                LatestOpenIncidentStartUtc,
                LatestOpenIncidentStartAst,
                LatestOpenIncidentDetectedUtc,
                LatestOpenIncidentDetectedAst,
                LatestOpenIncidentAgeSec,
                LatestOpenIncidentAgeMin,
                AutoActionTriggered,
                AutoActionType,
                AutoActionUtc,
                AutoActionAst,
                AutoActionResultCode,
                AutoActionResultMessage,
                RecommendedAction,
                SortRank,
                RefreshedUtc,
                RefreshedAst
            FROM dbo.IoTOperationalDashboardDevices
            ORDER BY SortRank ASC, HeartbeatAgeMinutes DESC
        """)
        devices = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT
                IncidentId,
                DeviceId,
                SiteId,
                SiteCode,
                SiteName,
                IncidentType,
                State,
                StartUtc,
                StartAst,
                DetectedUtc,
                DetectedAst,
                RecoveryUtc,
                RecoveryAst,
                DurationSec,
                IncidentAgeSec,
                IncidentAgeMin,
                AckBy,
                AckUtc,
                AckAst,
                Notes,
                LastAlertSentUtc,
                LastAlertSentAst,
                AutoActionTriggered,
                AutoActionType,
                AutoActionUtc,
                AutoActionAst,
                AutoActionResultCode,
                AutoActionResultMessage,
                RecommendedAction,
                RefreshedUtc,
                RefreshedAst
            FROM dbo.IoTOperationalDashboardOpenIncidents
            ORDER BY IncidentAgeSec DESC
        """)
        open_incidents = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        cursor.execute("""
            SELECT
                DeviceId,
                SiteCode,
                SiteName,
                CurrentStatus,
                SlaveStatus,
                LastHeartbeatUtc,
                LastHeartbeatAst,
                SecondsSinceLastHeartbeat,
                HeartbeatAgeMinutes,
                OpenIncidentCount,
                LatestOpenIncidentType,
                RecommendedAction,
                RefreshedUtc,
                RefreshedAst
            FROM dbo.IoTOperationalDashboardStaleHeartbeats
            ORDER BY HeartbeatAgeMinutes DESC
        """)
        stale_heartbeats = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        return {
            "status": "ok",
            "summary": summary,
            "devices": devices,
            "openIncidents": open_incidents,
            "staleHeartbeats": stale_heartbeats
        }

    finally:
        cursor.close()
        conn.close()