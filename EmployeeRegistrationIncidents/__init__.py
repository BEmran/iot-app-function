import json
import logging
import azure.functions as func

from shared_code.iot_logic import get_sql_connection


BASE_INCIDENTS_SQL = """
SELECT TOP ({limit})
    *
FROM (
    SELECT
        V.ValidationId,
        V.EmpId,

        V.ValidationStatus,
        V.FailedChecks AS MainValidationError,

        V.Check8Digits,
        V.CheckAllowedPattern,
        V.CheckExistsInWFPro,

        V.SourceDatabase,
        V.SourceSchema,
        V.SourceTable,
        V.SourceColumn,

        V.FirstDetectedUtc,
        V.LastCheckedUtc,
        V.FirstInvalidUtc,
        V.LastInvalidUtc,

        V.AlertStatus,
        V.EmailSent,
        V.EmailSentUtc,

        V.IsResolved,
        V.ResolvedUtc,

        Q.QueueId,
        Q.QueueStatus,
        Q.AttemptCount,
        Q.LastAttemptUtc,
        Q.EmailTo,
        Q.EmailSubject,
        Q.ErrorMessage,
        Q.CreatedUtc AS QueueCreatedUtc,
        Q.SentUtc AS QueueSentUtc,

        CASE
            WHEN V.ValidationStatus = 'VALID' THEN 'RESOLVED'
            WHEN V.ValidationStatus = 'INVALID'
             AND ISNULL(V.EmailSent, 0) = 1 THEN 'ALERT_SENT'
            WHEN V.ValidationStatus = 'INVALID'
             AND Q.QueueStatus = 'PENDING' THEN 'PENDING_EMAIL'
            WHEN V.ValidationStatus = 'INVALID'
             AND Q.QueueStatus = 'FAILED' THEN 'EMAIL_FAILED'
            WHEN V.ValidationStatus = 'INVALID'
             AND Q.QueueStatus = 'PROCESSING' THEN 'PROCESSING_EMAIL'
            WHEN V.ValidationStatus = 'INVALID' THEN 'OPEN'
            ELSE 'UNKNOWN'
        END AS IncidentStatus,

        CASE
            WHEN V.FailedChecks LIKE '%more than 8 digits%'
              OR V.FailedChecks LIKE '%exactly 8 digits%'
                THEN 'length'
            WHEN V.FailedChecks LIKE '%allowed patterns%'
                THEN 'pattern'
            WHEN V.FailedChecks LIKE '%official employee directory%'
                THEN 'directory'
            ELSE 'other'
        END AS ErrorType

    FROM TAIoT.dbo.EmployeeRegistrationValidation AS V
    LEFT JOIN (
        SELECT
            Q1.*
        FROM TAIoT.dbo.EmployeeRegistrationAlertQueue AS Q1
        INNER JOIN (
            SELECT
                ValidationId,
                MAX(QueueId) AS LatestQueueId
            FROM TAIoT.dbo.EmployeeRegistrationAlertQueue
            GROUP BY ValidationId
        ) AS LatestQ
            ON Q1.ValidationId = LatestQ.ValidationId
           AND Q1.QueueId = LatestQ.LatestQueueId
    ) AS Q
        ON V.ValidationId = Q.ValidationId
    WHERE V.ValidationStatus = 'INVALID'
) AS X
WHERE 1 = 1
"""


GET_SUMMARY_SQL = """
SELECT
    COUNT(*) AS TotalValidationRecords,
    SUM(CASE WHEN ValidationStatus = 'VALID' THEN 1 ELSE 0 END) AS ValidCount,
    SUM(CASE WHEN ValidationStatus = 'INVALID' THEN 1 ELSE 0 END) AS InvalidCount,
    SUM(CASE WHEN ValidationStatus = 'INVALID' AND EmailSent = 1 THEN 1 ELSE 0 END) AS InvalidEmailSentCount,
    SUM(CASE WHEN ValidationStatus = 'INVALID' AND EmailSent = 0 THEN 1 ELSE 0 END) AS InvalidEmailNotSentCount,
    SUM(CASE WHEN ValidationStatus = 'INVALID' AND IsResolved = 1 THEN 1 ELSE 0 END) AS ResolvedInvalidCount
FROM TAIoT.dbo.EmployeeRegistrationValidation;
"""


GET_QUEUE_SUMMARY_SQL = """
SELECT
    SUM(CASE WHEN QueueStatus = 'PENDING' THEN 1 ELSE 0 END) AS PendingQueueCount,
    SUM(CASE WHEN QueueStatus = 'PROCESSING' THEN 1 ELSE 0 END) AS ProcessingQueueCount,
    SUM(CASE WHEN QueueStatus = 'SENT' THEN 1 ELSE 0 END) AS SentQueueCount,
    SUM(CASE WHEN QueueStatus = 'FAILED' THEN 1 ELSE 0 END) AS FailedQueueCount
FROM TAIoT.dbo.EmployeeRegistrationAlertQueue;
"""


def row_to_dict(cursor, row):
    columns = [col[0] for col in cursor.description]
    result = {}

    for i, col in enumerate(columns):
        value = row[i]

        if hasattr(value, "isoformat"):
            value = value.isoformat()

        result[col] = value

    return result


def fetch_all_dict(cursor):
    return [row_to_dict(cursor, row) for row in cursor.fetchall()]


def fetch_one_dict(cursor):
    row = cursor.fetchone()
    if not row:
        return None
    return row_to_dict(cursor, row)


def parse_limit(req):
    raw_limit = req.params.get("limit", "500")

    try:
        limit = int(raw_limit)
    except ValueError:
        limit = 500

    if limit < 1:
        limit = 1

    if limit > 5000:
        limit = 5000

    return limit


def build_incident_query(req):
    limit = parse_limit(req)

    sql = BASE_INCIDENTS_SQL.format(limit=limit)
    params = []

    emp_id = req.params.get("empId")
    status = req.params.get("status")
    error_type = req.params.get("error")

    if emp_id:
        sql += " AND X.EmpId = %s"
        params.append(emp_id.strip())

    if status:
        status = status.strip().upper()

        allowed_statuses = {
            "OPEN",
            "PENDING_EMAIL",
            "ALERT_SENT",
            "EMAIL_FAILED",
            "PROCESSING_EMAIL",
            "RESOLVED"
        }

        if status in allowed_statuses:
            sql += " AND X.IncidentStatus = %s"
            params.append(status)

    if error_type:
        error_type = error_type.strip().lower()

        allowed_errors = {
            "length",
            "pattern",
            "directory",
            "other"
        }

        if error_type in allowed_errors:
            sql += " AND X.ErrorType = %s"
            params.append(error_type)

    sql += """
ORDER BY
    X.FirstInvalidUtc DESC,
    X.ValidationId DESC;
"""

    return sql, params, limit


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("EmployeeRegistrationIncidents endpoint started.")

    try:
        conn = get_sql_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(GET_SUMMARY_SQL)
            validation_summary = fetch_one_dict(cursor)

            cursor.execute(GET_QUEUE_SUMMARY_SQL)
            queue_summary = fetch_one_dict(cursor)

            incidents_sql, params, limit = build_incident_query(req)

            if params:
                cursor.execute(incidents_sql, tuple(params))
            else:
                cursor.execute(incidents_sql)

            incidents = fetch_all_dict(cursor)

        finally:
            cursor.close()
            conn.close()

        filters = {
            "status": req.params.get("status"),
            "empId": req.params.get("empId"),
            "error": req.params.get("error"),
            "limit": limit
        }

        response_body = {
            "status": "completed",
            "message": "Employee registration mismatch incidents retrieved successfully.",
            "filters": filters,
            "summary": validation_summary,
            "queueSummary": queue_summary,
            "incidentCount": len(incidents),
            "incidents": incidents
        }

        return func.HttpResponse(
            json.dumps(response_body, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as ex:
        logging.exception("EmployeeRegistrationIncidents endpoint failed.")

        return func.HttpResponse(
            json.dumps({
                "status": "failed",
                "error_type": type(ex).__name__,
                "error": str(ex)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )