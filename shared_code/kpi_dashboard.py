import datetime
from decimal import Decimal

from shared_code.iot_logic import get_sql_connection


def serialize_value(value):
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, Decimal):
        return float(value)
    return value


def row_to_dict(cursor, row):
    columns = [col[0] for col in cursor.description]
    return {
        columns[i]: serialize_value(row[i])
        for i in range(len(columns))
    }


def map_incident_type_display(incident_type):
    mapping = {
        "SLAVE_OFFLINE": "OFFLINE",
        "SLAVE_DOWN": "DOWN",
        "IOT_DEVICE_DISCONNECTED": "DISCONNECTED",
    }

    if incident_type is None:
        return None

    return mapping.get(incident_type, incident_type)


def get_incident_kpi_dashboard_data():
    conn = get_sql_connection()
    cursor = conn.cursor()

    try:
        # ------------------------------------------------------------
        # 1. KPI summary
        # ------------------------------------------------------------
        cursor.execute("""
            SELECT
                COUNT(*) AS TotalIncidents,

                SUM(CASE WHEN State = 'Open' THEN 1 ELSE 0 END) AS OpenIncidents,

                SUM(CASE WHEN State = 'Recovered' THEN 1 ELSE 0 END) AS RecoveredIncidents,

                AVG(
                    CASE
                        WHEN State = 'Recovered'
                             AND DurationMin IS NOT NULL
                        THEN DurationMin
                    END
                ) AS AverageRecoveryTimeMin,

                CAST(
                    SUM(CASE WHEN IsAutoActionSuccess = 1 THEN 1 ELSE 0 END)
                    AS decimal(18,4)
                )
                /
                NULLIF(
                    CAST(
                        SUM(CASE WHEN IsAutoActionTriggered = 1 THEN 1 ELSE 0 END)
                        AS decimal(18,4)
                    ),
                    0
                ) AS RemoteResolutionRate

            FROM dbo.vw_PBI_IncidentKPI;
        """)

        summary_row = cursor.fetchone()

        if summary_row:
            summary = {
                "TotalIncidents": int(summary_row[0] or 0),
                "OpenIncidents": int(summary_row[1] or 0),
                "RecoveredIncidents": int(summary_row[2] or 0),
                "AverageRecoveryTimeMin": float(summary_row[3]) if summary_row[3] is not None else None,
                "RemoteResolutionRate": float(summary_row[4]) if summary_row[4] is not None else 0,
            }
        else:
            summary = {
                "TotalIncidents": 0,
                "OpenIncidents": 0,
                "RecoveredIncidents": 0,
                "AverageRecoveryTimeMin": None,
                "RemoteResolutionRate": 0,
            }

        # ------------------------------------------------------------
        # 2. Incidents by type
        # ------------------------------------------------------------
        cursor.execute("""
            SELECT
                IncidentType,
                COUNT(*) AS IncidentCount
            FROM dbo.vw_PBI_IncidentKPI
            GROUP BY IncidentType
            ORDER BY IncidentCount DESC;
        """)

        incidents_by_type = []

        for row in cursor.fetchall():
            incident_type = row[0]
            incidents_by_type.append({
                "IncidentType": incident_type,
                "IncidentTypeDisplay": map_incident_type_display(incident_type),
                "IncidentCount": int(row[1] or 0),
            })

        # ------------------------------------------------------------
        # 3. Incidents by site
        # ------------------------------------------------------------
        cursor.execute("""
            SELECT
                SiteCode,
                SiteName,
                COUNT(*) AS IncidentCount
            FROM dbo.vw_PBI_IncidentKPI
            GROUP BY SiteCode, SiteName
            ORDER BY IncidentCount DESC;
        """)

        incidents_by_site = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        # ------------------------------------------------------------
        # 4. Incident trend by date
        # ------------------------------------------------------------
        cursor.execute("""
            SELECT
                StartDate,
                COUNT(*) AS IncidentCount
            FROM dbo.vw_PBI_IncidentKPI
            GROUP BY StartDate
            ORDER BY StartDate ASC;
        """)

        incident_trend = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        # ------------------------------------------------------------
        # 5. Recent incidents
        # ------------------------------------------------------------
        cursor.execute("""
            SELECT TOP 100
                IncidentId,
                DeviceId,
                SiteId,
                SiteCode,
                SiteName,
                Environment,
                IncidentType,
                State,
                StartUtc,
                StartDate,
                DATEADD(HOUR, 3, StartUtc) AS StartAst,
                DetectedUtc,
                DATEADD(HOUR, 3, DetectedUtc) AS DetectedAst,
                RecoveryUtc,
                DATEADD(HOUR, 3, RecoveryUtc) AS RecoveryAst,
                DurationSec,
                DurationMin,
                DurationHours,
                AutoActionTriggered,
                AutoActionType,
                AutoActionUtc,
                DATEADD(HOUR, 3, AutoActionUtc) AS AutoActionAst,
                AutoActionResultCode,
                AutoActionResultMessage,
                IsRecovered,
                IsOpen,
                IsAutoActionTriggered,
                IsAutoActionSuccess
            FROM dbo.vw_PBI_IncidentKPI
            ORDER BY StartUtc DESC;
        """)

        recent_incidents = []

        for row in cursor.fetchall():
            item = row_to_dict(cursor, row)

            incident_type = item.get("IncidentType")
            item["IncidentTypeCode"] = incident_type
            item["IncidentTypeDisplay"] = map_incident_type_display(incident_type)

            recent_incidents.append(item)

        now_utc = datetime.datetime.utcnow()
        now_ast = now_utc + datetime.timedelta(hours=3)

        return {
            "status": "ok",
            "summary": summary,
            "incidentsByType": incidents_by_type,
            "incidentsBySite": incidents_by_site,
            "incidentTrend": incident_trend,
            "recentIncidents": recent_incidents,
            "refreshedUtc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
            "refreshedAst": now_ast.strftime("%Y-%m-%d %H:%M:%S"),
        }

    finally:
        cursor.close()
        conn.close()