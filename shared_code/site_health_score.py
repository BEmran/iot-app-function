import datetime
from decimal import Decimal

from shared_code.iot_logic import get_sql_connection


VIEW_NAME = "dbo.vw_DASHBOARD_SiteHealthScore"


def serialize_value(value):
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%SZ")

    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, bytes):
        return value.decode("utf-8", errors="ignore")

    return value


def parse_int_param(req, name, default_value, min_value, max_value):
    raw_value = req.params.get(name)

    if raw_value is None or str(raw_value).strip() == "":
        return default_value

    try:
        value = int(raw_value)
    except ValueError:
        return default_value

    if value < min_value:
        return min_value

    if value > max_value:
        return max_value

    return value


def parse_optional_text_param(req, name):
    raw_value = req.params.get(name)

    if raw_value is None:
        return None

    value = str(raw_value).strip()

    if value == "":
        return None

    return value


def row_to_dict(cursor, row):
    columns = [col[0] for col in cursor.description]
    return {
        columns[i]: serialize_value(row[i])
        for i in range(len(columns))
    }


def get_site_health_score_data(req):
    """
    Returns dashboard-ready Site Health Score data.

    Query parameters:
    - limit: max number of rows to return. Default 50.
    - environment: optional environment filter.
    - siteCode: optional site filter.
    - status: optional health status filter.

    Source view:
    - dbo.vw_DASHBOARD_SiteHealthScore
    """

    limit = parse_int_param(
        req=req,
        name="limit",
        default_value=50,
        min_value=1,
        max_value=500
    )

    environment = parse_optional_text_param(req, "environment")
    site_code = parse_optional_text_param(req, "siteCode")
    status = parse_optional_text_param(req, "status")

    conn = get_sql_connection()
    cursor = conn.cursor()

    try:
        sql = f"""
            SELECT TOP ({limit})
                SiteCode,
                SiteName,
                Environment,
                DeviceId,
                HealthScore,
                HealthStatus,
                MainReason,
                RecommendedAction,

                LastDeviceHeartbeatUtc,
                LastHeartbeatIngestedUtc,
                HeartbeatAgeMin,
                LatestSlaveStatus,

                OpenIncidents,
                IncidentsLast24h,
                IncidentsLast7Days,

                LongestRecoveryMinLast7Days,
                FailedRebootCountLast24h,
                LastFailedRebootUtc,

                ViewGeneratedUtc
            FROM {VIEW_NAME}
            WHERE
                (%s IS NULL OR Environment = %s)
                AND (%s IS NULL OR SiteCode = %s)
                AND (%s IS NULL OR HealthStatus = %s)
            ORDER BY
                HealthScore ASC,
                OpenIncidents DESC,
                IncidentsLast24h DESC,
                HeartbeatAgeMin DESC;
        """

        cursor.execute(
            sql,
            (
                environment,
                environment,
                site_code,
                site_code,
                status,
                status
            )
        )

        rows = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        data = []

        for row in rows:
            data.append({
                "siteCode": row.get("SiteCode"),
                "siteName": row.get("SiteName"),
                "environment": row.get("Environment"),
                "deviceId": row.get("DeviceId"),

                "healthScore": int(row.get("HealthScore") or 0),
                "healthStatus": row.get("HealthStatus"),
                "mainReason": row.get("MainReason"),
                "recommendedAction": row.get("RecommendedAction"),

                "lastDeviceHeartbeatUtc": row.get("LastDeviceHeartbeatUtc"),
                "lastHeartbeatIngestedUtc": row.get("LastHeartbeatIngestedUtc"),
                "heartbeatAgeMin": row.get("HeartbeatAgeMin"),
                "latestSlaveStatus": row.get("LatestSlaveStatus"),

                "openIncidents": int(row.get("OpenIncidents") or 0),
                "incidentsLast24h": int(row.get("IncidentsLast24h") or 0),
                "incidentsLast7Days": int(row.get("IncidentsLast7Days") or 0),

                "longestRecoveryMinLast7Days": row.get("LongestRecoveryMinLast7Days"),
                "failedRebootCountLast24h": int(row.get("FailedRebootCountLast24h") or 0),
                "lastFailedRebootUtc": row.get("LastFailedRebootUtc"),

                "viewGeneratedUtc": row.get("ViewGeneratedUtc")
            })

        cards = {
            "totalReturned": len(data),
            "healthy": sum(1 for item in data if item.get("healthStatus") == "Healthy"),
            "watch": sum(1 for item in data if item.get("healthStatus") == "Watch"),
            "needsAttention": sum(1 for item in data if item.get("healthStatus") == "Needs Attention"),
            "critical": sum(1 for item in data if item.get("healthStatus") == "Critical"),
            "lowestHealthScore": min([item["healthScore"] for item in data], default=None),
            "highestRiskSite": data[0]["siteCode"] if data else None
        }

        now_utc = datetime.datetime.utcnow()

        return {
            "status": "success",
            "count": len(data),
            "filters": {
                "limit": limit,
                "environment": environment,
                "siteCode": site_code,
                "status": status
            },
            "cards": cards,
            "data": data,
            "refreshedUtc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "refreshedAst": (
                now_utc + datetime.timedelta(hours=3)
            ).strftime("%Y-%m-%dT%H:%M:%S")
        }

    finally:
        cursor.close()
        conn.close()