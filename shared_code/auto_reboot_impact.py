import datetime
from decimal import Decimal

from shared_code.iot_logic import get_sql_connection


VIEW_NAME = "dbo.vw_PBI_AutoRebootImpactDetail"


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


def to_bool(value):
    if value is None:
        return None

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        return value == 1

    text = str(value).strip().lower()

    if text in ("1", "true", "yes", "y"):
        return True

    if text in ("0", "false", "no", "n"):
        return False

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
    result = {}

    for i, column_name in enumerate(columns):
        value = serialize_value(row[i])

        if column_name in ("RebootCommandSucceeded", "RecoveredAfterReboot"):
            value = to_bool(value)

        result[column_name] = value

    return result


def normalize_card_value(value, default_value=0):
    if value is None:
        return default_value

    if isinstance(value, Decimal):
        return float(value)

    return value


def get_auto_reboot_impact_data(req):
    """
    Returns dashboard-ready Auto-Reboot Impact KPI data.

    Query parameters:
    - days: number of days to look back. Default 7.
    - siteCode: optional site filter.
    - environment: optional environment filter.
    - limit: number of detail records to return. Default 20.

    Source:
    - dbo.vw_PBI_AutoRebootImpactDetail
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
        default_value=20,
        min_value=1,
        max_value=500
    )

    site_code = parse_optional_text_param(req, "siteCode")
    environment = parse_optional_text_param(req, "environment")

    conn = get_sql_connection()
    cursor = conn.cursor()

    try:
        summary_sql = f"""
            DECLARE @fromUtc datetime2 = DATEADD(DAY, -%s, SYSUTCDATETIME());

            SELECT
                COUNT(*) AS AutoRebootAttempts,

                SUM(
                    CASE
                        WHEN RebootCommandSucceeded = 1
                        THEN 1 ELSE 0
                    END
                ) AS SuccessfulRebootCommands,

                SUM(
                    CASE
                        WHEN RebootCommandSucceeded = 0
                        THEN 1 ELSE 0
                    END
                ) AS FailedRebootCommands,

                SUM(
                    CASE
                        WHEN RecoveredAfterReboot = 1
                        THEN 1 ELSE 0
                    END
                ) AS RecoveredAfterReboot,

                CAST(
                    100.0 *
                    SUM(CASE WHEN RebootCommandSucceeded = 1 THEN 1 ELSE 0 END)
                    / NULLIF(COUNT(*), 0)
                    AS decimal(5,2)
                ) AS AutoRebootSuccessPercent,

                AVG(
                    CASE
                        WHEN RecoveredAfterReboot = 1
                        THEN CAST(RecoveryTimeAfterRebootSec AS decimal(18,2)) / 60.0
                        ELSE NULL
                    END
                ) AS AvgRecoveryTimeAfterRebootMin

            FROM {VIEW_NAME}
            WHERE
                RebootRequestedUtc >= @fromUtc
                AND (%s IS NULL OR SiteCode = %s)
                AND (%s IS NULL OR Environment = %s);
        """

        cursor.execute(
            summary_sql,
            (
                days,
                site_code,
                site_code,
                environment,
                environment
            )
        )

        summary_row = cursor.fetchone()

        cards = {
            "autoRebootAttempts": 0,
            "successfulRebootCommands": 0,
            "failedRebootCommands": 0,
            "autoRebootSuccessPercent": 0,
            "recoveredAfterReboot": 0,
            "avgRecoveryTimeAfterRebootMin": None
        }

        if summary_row:
            cards = {
                "autoRebootAttempts": int(normalize_card_value(summary_row[0], 0) or 0),
                "successfulRebootCommands": int(normalize_card_value(summary_row[1], 0) or 0),
                "failedRebootCommands": int(normalize_card_value(summary_row[2], 0) or 0),
                "recoveredAfterReboot": int(normalize_card_value(summary_row[3], 0) or 0),
                "autoRebootSuccessPercent": float(normalize_card_value(summary_row[4], 0) or 0),
                "avgRecoveryTimeAfterRebootMin": (
                    None
                    if summary_row[5] is None
                    else round(float(normalize_card_value(summary_row[5])), 2)
                )
            }

        detail_sql = f"""
            DECLARE @fromUtc datetime2 = DATEADD(DAY, -%s, SYSUTCDATETIME());

            SELECT TOP ({limit})
                SiteCode,
                SiteName,
                Environment,
                DeviceId,
                IncidentId,
                IncidentType,
                IncidentStartUtc,
                IncidentDetectedUtc,
                RecoveryUtc,
                IncidentState,
                RebootRequestedUtc,
                RebootCompletedUtc,
                ResultCode,
                ResultMessage,
                RebootCommandSucceeded,
                RecoveredAfterReboot,
                RecoveryTimeAfterRebootSec,
                RecoveryTimeAfterRebootMin,
                RebootImpactInterpretation
            FROM {VIEW_NAME}
            WHERE
                RebootRequestedUtc >= @fromUtc
                AND (%s IS NULL OR SiteCode = %s)
                AND (%s IS NULL OR Environment = %s)
            ORDER BY
                RebootRequestedUtc DESC;
        """

        cursor.execute(
            detail_sql,
            (
                days,
                site_code,
                site_code,
                environment,
                environment
            )
        )

        raw_rows = [row_to_dict(cursor, row) for row in cursor.fetchall()]

        data = []

        for row in raw_rows:
            data.append({
                "siteCode": row.get("SiteCode"),
                "siteName": row.get("SiteName"),
                "environment": row.get("Environment"),
                "deviceId": row.get("DeviceId"),
                "incidentId": row.get("IncidentId"),
                "incidentType": row.get("IncidentType"),
                "incidentStartUtc": row.get("IncidentStartUtc"),
                "incidentDetectedUtc": row.get("IncidentDetectedUtc"),
                "recoveryUtc": row.get("RecoveryUtc"),
                "incidentState": row.get("IncidentState"),
                "rebootRequestedUtc": row.get("RebootRequestedUtc"),
                "rebootCompletedUtc": row.get("RebootCompletedUtc"),
                "resultCode": row.get("ResultCode"),
                "resultMessage": row.get("ResultMessage"),
                "rebootCommandSucceeded": row.get("RebootCommandSucceeded"),
                "recoveredAfterReboot": row.get("RecoveredAfterReboot"),
                "recoveryTimeAfterRebootSec": row.get("RecoveryTimeAfterRebootSec"),
                "recoveryTimeAfterRebootMin": row.get("RecoveryTimeAfterRebootMin"),
                "rebootImpactInterpretation": row.get("RebootImpactInterpretation")
            })

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