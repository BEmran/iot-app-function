import datetime
from decimal import Decimal

from shared_code.iot_logic import get_sql_connection


VIEW_NAME = "dbo.vw_Dashboard_TopProblemSites"


def serialize_value(value):
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, Decimal):
        return float(value)
    return value


def row_to_dict(cursor, row):
    columns = [col[0] for col in cursor.description]
    return {
        columns[i]: serialize_value(row[i])
        for i in range(len(columns))
    }


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


def get_top_problem_sites_data(req):
    """
    Reads the dashboard-ready SQL view for top problem/repeated incident sites.

    Expected SQL view columns:
    - SiteCode
    - SiteName
    - IncidentsLast24h
    - IncidentsLast7Days
    - MainIncidentType
    - MainIncidentTypeCount
    - OpenIncidents
    - TotalDowntimeMin
    - LastIncidentUtc
    - Recommendation
    - ProblemRank
    """

    limit = parse_int_param(
        req=req,
        name="limit",
        default_value=10,
        min_value=1,
        max_value=100
    )

    min_incidents_7_days = parse_int_param(
        req=req,
        name="minIncidents7Days",
        default_value=0,
        min_value=0,
        max_value=100000
    )

    conn = get_sql_connection()
    cursor = conn.cursor()

    try:
        sql = f"""
            SELECT TOP ({limit})
                SiteCode,
                SiteName,
                IncidentsLast24h,
                IncidentsLast7Days,
                MainIncidentType,
                MainIncidentTypeCount,
                OpenIncidents,
                TotalDowntimeMin,
                LastIncidentUtc,
                DATEADD(HOUR, 3, LastIncidentUtc) AS LastIncidentAst,
                Recommendation,
                ProblemRank
            FROM {VIEW_NAME}
            WHERE IncidentsLast7Days >= %s
            ORDER BY ProblemRank ASC;
        """

        cursor.execute(sql, (min_incidents_7_days,))
        sites = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        summary = {
            "TotalSitesReturned": len(sites),
            "TotalOpenIncidents": sum(int(site.get("OpenIncidents") or 0) for site in sites),
            "TotalIncidentsLast24h": sum(int(site.get("IncidentsLast24h") or 0) for site in sites),
            "TotalIncidentsLast7Days": sum(int(site.get("IncidentsLast7Days") or 0) for site in sites),
            "TotalDowntimeMin": sum(float(site.get("TotalDowntimeMin") or 0) for site in sites)
        }

        return {
            "status": "ok",
            "message": "Top problem sites KPI retrieved successfully.",
            "filters": {
                "limit": limit,
                "minIncidents7Days": min_incidents_7_days
            },
            "summary": summary,
            "sites": sites,
            "refreshedUtc": datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            "refreshedAst": (
                datetime.datetime.utcnow() + datetime.timedelta(hours=3)
            ).strftime("%Y-%m-%d %H:%M:%S")
        }

    finally:
        cursor.close()
        conn.close()