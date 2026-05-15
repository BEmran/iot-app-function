import datetime
from decimal import Decimal

from shared_code.iot_logic import get_sql_connection


VIEW_NAME = "dbo.vw_DASHBOARD_DowntimeImpactDetail"


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


def seconds_to_minutes(value):
    if value is None:
        return 0

    return round(float(value) / 60.0, 2)


def get_downtime_impact_by_site_data(req):
    """
    Returns dashboard-ready Downtime Impact by Site KPI.

    Query parameters:
    - days: number of days to look back. Default 7.
    - limit: number of ranked sites to return. Default 10.
    - siteCode: optional site filter.
    - environment: optional environment filter.

    Source view:
    - dbo.vw_DASHBOARD_DowntimeImpactDetail
    """

    days = parse_int_param(
        req=req,
        name="days",
        default_value=7,
        min_value=1,
        max_value=365
    )

    limit = parse_int_param(
        req=req,
        name="limit",
        default_value=10,
        min_value=1,
        max_value=500
    )

    site_code = parse_optional_text_param(req, "siteCode")
    environment = parse_optional_text_param(req, "environment")

    conn = get_sql_connection()
    cursor = conn.cursor()

    try:
        sql = f"""
            DECLARE @fromUtc datetime2 = DATEADD(DAY, -%s, SYSUTCDATETIME());

            WITH FilteredIncidents AS
            (
                SELECT
                    SiteCode,
                    SiteName,
                    Environment,
                    DeviceId,
                    IncidentId,
                    IncidentType,
                    State,
                    StartUtc,
                    RecoveryUtc,
                    ISNULL(EffectiveDowntimeSec, 0) AS EffectiveDowntimeSec,
                    ISNULL(CurrentUnresolvedDurationSec, 0) AS CurrentUnresolvedDurationSec,
                    ISNULL(IsOpenIncident, 0) AS IsOpenIncident
                FROM {VIEW_NAME}
                WHERE
                    StartUtc >= @fromUtc
                    AND (%s IS NULL OR SiteCode = %s)
                    AND (%s IS NULL OR Environment = %s)
            ),
            SiteAggregation AS
            (
                SELECT
                    SiteCode,
                    SiteName,
                    Environment,

                    COUNT(*) AS IncidentCount,

                    SUM(EffectiveDowntimeSec) AS TotalDowntimeSec,

                    AVG(CAST(EffectiveDowntimeSec AS decimal(18,2))) AS AvgDowntimePerIncidentSec,

                    MAX(EffectiveDowntimeSec) AS LongestIncidentDurationSec,

                    SUM(
                        CASE
                            WHEN IsOpenIncident = 1
                            THEN CurrentUnresolvedDurationSec
                            ELSE 0
                        END
                    ) AS CurrentUnresolvedDurationSec,

                    SUM(
                        CASE
                            WHEN IsOpenIncident = 1
                            THEN 1
                            ELSE 0
                        END
                    ) AS OpenIncidentCount,

                    MAX(StartUtc) AS LastIncidentUtc
                FROM FilteredIncidents
                GROUP BY
                    SiteCode,
                    SiteName,
                    Environment
            ),
            RankedSites AS
            (
                SELECT
                    SiteCode,
                    SiteName,
                    Environment,
                    IncidentCount,
                    TotalDowntimeSec,
                    AvgDowntimePerIncidentSec,
                    LongestIncidentDurationSec,
                    CurrentUnresolvedDurationSec,
                    OpenIncidentCount,
                    LastIncidentUtc,

                    CASE
                        WHEN OpenIncidentCount > 0 THEN 'Action Required'
                        WHEN TotalDowntimeSec >= 3600 THEN 'High Downtime'
                        WHEN TotalDowntimeSec >= 900 THEN 'Moderate Downtime'
                        WHEN IncidentCount > 0 THEN 'Monitor'
                        ELSE 'Normal'
                    END AS Status,

                    CASE
                        WHEN OpenIncidentCount > 0 THEN
                            'Site has open incident(s). Immediate operational follow-up is required.'

                        WHEN TotalDowntimeSec >= 3600 THEN
                            'Site caused high downtime during the selected period. Investigate root cause and prioritize maintenance.'

                        WHEN LongestIncidentDurationSec >= 1800 THEN
                            'Site had at least one long outage. Review recovery process and local device/network stability.'

                        WHEN IncidentCount >= 5 THEN
                            'Site has repeated incidents but limited downtime. Monitor for recurring local issues.'

                        ELSE
                            'No major downtime impact during the selected period. Continue monitoring.'
                    END AS ExecutiveInterpretation,

                    ROW_NUMBER() OVER
                    (
                        ORDER BY
                            TotalDowntimeSec DESC,
                            OpenIncidentCount DESC,
                            IncidentCount DESC,
                            LastIncidentUtc DESC
                    ) AS DowntimeRank
                FROM SiteAggregation
            )
            SELECT TOP ({limit})
                SiteCode,
                SiteName,
                Environment,
                IncidentCount,
                TotalDowntimeSec,
                AvgDowntimePerIncidentSec,
                LongestIncidentDurationSec,
                CurrentUnresolvedDurationSec,
                OpenIncidentCount,
                LastIncidentUtc,
                Status,
                ExecutiveInterpretation,
                DowntimeRank
            FROM RankedSites
            ORDER BY
                DowntimeRank ASC;
        """

        cursor.execute(
            sql,
            (
                days,
                site_code,
                site_code,
                environment,
                environment
            )
        )

        rows = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        data = []

        for row in rows:
            total_downtime_sec = row.get("TotalDowntimeSec") or 0
            avg_downtime_sec = row.get("AvgDowntimePerIncidentSec") or 0
            longest_duration_sec = row.get("LongestIncidentDurationSec") or 0
            current_unresolved_sec = row.get("CurrentUnresolvedDurationSec") or 0

            data.append({
                "downtimeRank": row.get("DowntimeRank"),
                "siteCode": row.get("SiteCode"),
                "siteName": row.get("SiteName"),
                "environment": row.get("Environment"),

                "incidentCount": int(row.get("IncidentCount") or 0),

                "totalDowntimeSec": int(total_downtime_sec),
                "totalDowntimeMin": seconds_to_minutes(total_downtime_sec),

                "avgDowntimePerIncidentSec": float(avg_downtime_sec),
                "avgDowntimePerIncidentMin": seconds_to_minutes(avg_downtime_sec),

                "longestIncidentDurationSec": int(longest_duration_sec),
                "longestIncidentDurationMin": seconds_to_minutes(longest_duration_sec),

                "currentUnresolvedDurationSec": int(current_unresolved_sec),
                "currentUnresolvedDurationMin": seconds_to_minutes(current_unresolved_sec),

                "openIncidentCount": int(row.get("OpenIncidentCount") or 0),
                "lastIncidentUtc": row.get("LastIncidentUtc"),

                "status": row.get("Status"),
                "executiveInterpretation": row.get("ExecutiveInterpretation")
            })

        cards = {
            "sitesReturned": len(data),
            "totalIncidentCount": sum(item["incidentCount"] for item in data),
            "totalDowntimeMin": round(sum(item["totalDowntimeMin"] for item in data), 2),
            "totalOpenIncidents": sum(item["openIncidentCount"] for item in data),
            "highestDowntimeSite": data[0]["siteCode"] if data else None,
            "highestDowntimeMin": data[0]["totalDowntimeMin"] if data else 0
        }

        now_utc = datetime.datetime.utcnow()

        return {
            "status": "success",
            "periodDays": days,
            "filters": {
                "siteCode": site_code,
                "environment": environment,
                "limit": limit
            },
            "cards": cards,
            "data": data,
            "rowCount": len(data),
            "refreshedUtc": now_utc.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "refreshedAst": (
                now_utc + datetime.timedelta(hours=3)
            ).strftime("%Y-%m-%dT%H:%M:%S")
        }

    finally:
        cursor.close()
        conn.close()