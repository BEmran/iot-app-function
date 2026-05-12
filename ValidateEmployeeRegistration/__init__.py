import json
import logging
import datetime
import html

import azure.functions as func

from shared_code.iot_logic import get_sql_connection, send_email


MAX_EMAILS_PER_CALL = 1


MERGE_VALIDATION_SQL = """
MERGE TAIoT.dbo.EmployeeRegistrationValidation AS Target
USING (
    SELECT
        X.EmpId,
        X.Check8Digits,
        X.CheckAllowedPattern,
        X.CheckExistsInWFPro,

        CASE
            WHEN X.Check8Digits = 1
             AND X.CheckAllowedPattern = 1
             AND X.CheckExistsInWFPro = 1
            THEN 'VALID'
            ELSE 'INVALID'
        END AS ValidationStatus,

        NULLIF(CONCAT(
            CASE WHEN X.Check8Digits = 0
                 THEN 'ID is not exactly 8 digits; '
                 ELSE ''
            END,
            CASE WHEN X.CheckAllowedPattern = 0
                 THEN 'ID does not match allowed patterns 650xxxxx, 655xxxxx, or 250xxxxx; '
                 ELSE ''
            END,
            CASE WHEN X.CheckExistsInWFPro = 0
                 THEN 'ID does not exist in WFPro_MSY.dbo.M04T009; '
                 ELSE ''
            END
        ), '') AS FailedChecks

    FROM (
        SELECT
            S.EmpId,

            CASE
                WHEN LEN(S.EmpId) = 8
                 AND S.EmpId NOT LIKE '%[^0-9]%'
                THEN CAST(1 AS BIT)
                ELSE CAST(0 AS BIT)
            END AS Check8Digits,

            CASE
                WHEN S.EmpId LIKE '650[0-9][0-9][0-9][0-9][0-9]'
                  OR S.EmpId LIKE '655[0-9][0-9][0-9][0-9][0-9]'
                  OR S.EmpId LIKE '250[0-9][0-9][0-9][0-9][0-9]'
                THEN CAST(1 AS BIT)
                ELSE CAST(0 AS BIT)
            END AS CheckAllowedPattern,

            CASE
                WHEN MSY.M04T009C007 IS NOT NULL
                THEN CAST(1 AS BIT)
                ELSE CAST(0 AS BIT)
            END AS CheckExistsInWFPro

        FROM (
            SELECT DISTINCT
                LTRIM(RTRIM(CAST(NB.M04T009C007 AS NVARCHAR(50)))) AS EmpId
            FROM NewBluebird.dbo.M04T009 AS NB
            WHERE NB.M04T009C007 IS NOT NULL
        ) AS S
        LEFT JOIN WFPro_MSY.dbo.M04T009 AS MSY
            ON S.EmpId = LTRIM(RTRIM(CAST(MSY.M04T009C007 AS NVARCHAR(50))))
    ) AS X
) AS Source
ON Target.EmpId = Source.EmpId

WHEN MATCHED THEN
    UPDATE SET
        Target.Check8Digits = Source.Check8Digits,
        Target.CheckAllowedPattern = Source.CheckAllowedPattern,
        Target.CheckExistsInWFPro = Source.CheckExistsInWFPro,
        Target.ValidationStatus = Source.ValidationStatus,
        Target.FailedChecks = Source.FailedChecks,
        Target.LastCheckedUtc = SYSUTCDATETIME(),
        Target.UpdatedUtc = SYSUTCDATETIME(),

        Target.FirstInvalidUtc =
            CASE
                WHEN Source.ValidationStatus = 'INVALID'
                 AND Target.FirstInvalidUtc IS NULL
                THEN SYSUTCDATETIME()
                ELSE Target.FirstInvalidUtc
            END,

        Target.LastInvalidUtc =
            CASE
                WHEN Source.ValidationStatus = 'INVALID'
                THEN SYSUTCDATETIME()
                ELSE Target.LastInvalidUtc
            END,

        Target.AlertStatus =
            CASE
                WHEN Source.ValidationStatus = 'INVALID'
                 AND Target.EmailSent = 0
                THEN 'PENDING_EMAIL'
                WHEN Source.ValidationStatus = 'VALID'
                THEN 'NOT_REQUIRED'
                ELSE Target.AlertStatus
            END,

        Target.IsResolved =
            CASE
                WHEN Source.ValidationStatus = 'VALID'
                THEN 1
                ELSE 0
            END,

        Target.ResolvedUtc =
            CASE
                WHEN Source.ValidationStatus = 'VALID'
                 AND Target.ValidationStatus = 'INVALID'
                THEN SYSUTCDATETIME()
                ELSE Target.ResolvedUtc
            END

WHEN NOT MATCHED THEN
    INSERT (
        EmpId,
        Check8Digits,
        CheckAllowedPattern,
        CheckExistsInWFPro,
        ValidationStatus,
        FailedChecks,
        FirstDetectedUtc,
        LastCheckedUtc,
        FirstInvalidUtc,
        LastInvalidUtc,
        AlertStatus,
        EmailSent,
        IsResolved,
        CreatedUtc,
        UpdatedUtc
    )
    VALUES (
        Source.EmpId,
        Source.Check8Digits,
        Source.CheckAllowedPattern,
        Source.CheckExistsInWFPro,
        Source.ValidationStatus,
        Source.FailedChecks,
        SYSUTCDATETIME(),
        SYSUTCDATETIME(),
        CASE WHEN Source.ValidationStatus = 'INVALID' THEN SYSUTCDATETIME() ELSE NULL END,
        CASE WHEN Source.ValidationStatus = 'INVALID' THEN SYSUTCDATETIME() ELSE NULL END,
        CASE WHEN Source.ValidationStatus = 'INVALID' THEN 'PENDING_EMAIL' ELSE 'NOT_REQUIRED' END,
        0,
        CASE WHEN Source.ValidationStatus = 'VALID' THEN 1 ELSE 0 END,
        SYSUTCDATETIME(),
        SYSUTCDATETIME()
    );
"""


INSERT_QUEUE_SQL = """
INSERT INTO TAIoT.dbo.EmployeeRegistrationAlertQueue (
    ValidationId,
    EmpId,
    FailedChecks,
    QueueStatus,
    CreatedUtc
)
SELECT
    V.ValidationId,
    V.EmpId,
    V.FailedChecks,
    'PENDING',
    SYSUTCDATETIME()
FROM TAIoT.dbo.EmployeeRegistrationValidation AS V
WHERE V.ValidationStatus = 'INVALID'
  AND V.AlertStatus = 'PENDING_EMAIL'
  AND V.EmailSent = 0
  AND NOT EXISTS (
        SELECT 1
        FROM TAIoT.dbo.EmployeeRegistrationAlertQueue AS Q
        WHERE Q.ValidationId = V.ValidationId
          AND Q.QueueStatus IN ('PENDING', 'PROCESSING', 'FAILED')
  );
"""


PICK_ONE_QUEUE_ITEM_SQL = """
DECLARE @QueueId BIGINT;

SELECT TOP (1)
    @QueueId = QueueId
FROM TAIoT.dbo.EmployeeRegistrationAlertQueue
WHERE QueueStatus = 'PENDING'
  AND AttemptCount < 5
ORDER BY CreatedUtc ASC, QueueId ASC;

IF @QueueId IS NOT NULL
BEGIN
    UPDATE TAIoT.dbo.EmployeeRegistrationAlertQueue
    SET
        QueueStatus = 'PROCESSING',
        AttemptCount = AttemptCount + 1,
        LastAttemptUtc = SYSUTCDATETIME(),
        ErrorMessage = NULL
    OUTPUT
        inserted.QueueId,
        inserted.ValidationId,
        inserted.EmpId,
        inserted.FailedChecks
    WHERE QueueId = @QueueId;
END
"""


MARK_QUEUE_SENT_SQL = """
UPDATE TAIoT.dbo.EmployeeRegistrationAlertQueue
SET
    QueueStatus = 'SENT',
    SentUtc = SYSUTCDATETIME(),
    EmailTo = %s,
    EmailSubject = %s,
    EmailBody = %s,
    ErrorMessage = NULL
WHERE QueueId = %s;

UPDATE TAIoT.dbo.EmployeeRegistrationValidation
SET
    EmailSent = 1,
    EmailSentUtc = SYSUTCDATETIME(),
    AlertStatus = 'EMAIL_SENT',
    UpdatedUtc = SYSUTCDATETIME()
WHERE ValidationId = %s;
"""


MARK_QUEUE_FAILED_SQL = """
UPDATE TAIoT.dbo.EmployeeRegistrationAlertQueue
SET
    QueueStatus = 'FAILED',
    ErrorMessage = %s,
    LastAttemptUtc = SYSUTCDATETIME()
WHERE QueueId = %s;
"""


GET_SUMMARY_SQL = """
SELECT
    COUNT(*) AS TotalChecked,
    SUM(CASE WHEN ValidationStatus = 'VALID' THEN 1 ELSE 0 END) AS ValidCount,
    SUM(CASE WHEN ValidationStatus = 'INVALID' THEN 1 ELSE 0 END) AS InvalidCount,
    SUM(CASE WHEN ValidationStatus = 'INVALID' AND EmailSent = 0 THEN 1 ELSE 0 END) AS InvalidNotEmailedCount
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


def fetch_one_dict(cursor):
    row = cursor.fetchone()
    if not row:
        return None
    return row_to_dict(cursor, row)


def build_email(emp_id, failed_checks, queue_id, validation_id):
    emp_id_safe = html.escape(str(emp_id))
    failed_checks_safe = html.escape(str(failed_checks or ""))
    checked_utc = datetime.datetime.utcnow().isoformat() + "Z"

    subject = f"Invalid NewBluebird Employee Registration - Emp ID {emp_id_safe}"

    body = f"""
<html>
<body>
    <p>Dear Team,</p>

    <p>
        The IoT monitoring system detected an invalid employee registration
        in the NewBluebird employee master table.
    </p>

    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse: collapse;">
        <tr>
            <td><b>Employee ID</b></td>
            <td>{emp_id_safe}</td>
        </tr>
        <tr>
            <td><b>Source</b></td>
            <td>NewBluebird.dbo.M04T009.M04T009C007</td>
        </tr>
        <tr>
            <td><b>Reference Check</b></td>
            <td>WFPro_MSY.dbo.M04T009.M04T009C007</td>
        </tr>
        <tr>
            <td><b>Failed Checks</b></td>
            <td>{failed_checks_safe}</td>
        </tr>
        <tr>
            <td><b>Validation Queue ID</b></td>
            <td>{queue_id}</td>
        </tr>
        <tr>
            <td><b>Validation ID</b></td>
            <td>{validation_id}</td>
        </tr>
        <tr>
            <td><b>Checked UTC</b></td>
            <td>{checked_utc}</td>
        </tr>
    </table>

    <p>
        Required action: please verify whether this employee ID should exist
        in the official WFPro_MSY employee master before allowing it to remain
        active in NewBluebird.
    </p>

    <p>-- IoT Monitoring System</p>
</body>
</html>
"""

    return subject, body


def process_one_email(cursor, conn, to_address):
    cursor.execute(PICK_ONE_QUEUE_ITEM_SQL)
    picked = fetch_one_dict(cursor)
    conn.commit()

    if not picked:
        return {
            "status": "no_pending_email",
            "message": "No pending employee registration alert found."
        }

    queue_id = picked["QueueId"]
    validation_id = picked["ValidationId"]
    emp_id = picked["EmpId"]
    failed_checks = picked["FailedChecks"]

    subject, body = build_email(
        emp_id=emp_id,
        failed_checks=failed_checks,
        queue_id=queue_id,
        validation_id=validation_id
    )

    try:
        send_email(to_address, subject, body)

        cursor.execute(
            MARK_QUEUE_SENT_SQL,
            (to_address, subject, body, queue_id, validation_id)
        )
        conn.commit()

        return {
            "status": "email_sent",
            "queueId": queue_id,
            "validationId": validation_id,
            "empId": emp_id,
            "to": to_address,
            "subject": subject
        }

    except Exception as email_ex:
        error_text = f"{type(email_ex).__name__}: {str(email_ex)}"

        cursor.execute(
            MARK_QUEUE_FAILED_SQL,
            (error_text, queue_id)
        )
        conn.commit()

        logging.exception("Failed to send employee registration alert email.")

        return {
            "status": "email_failed",
            "queueId": queue_id,
            "validationId": validation_id,
            "empId": emp_id,
            "error": error_text
        }


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("ValidateEmployeeRegistration function started.")

    try:
        import os

        to_address = (
            req.params.get("to")
            or os.environ.get("EmployeeRegistrationAlertEmailTo")
            or os.environ.get("AlertEmailTo")
        )

        if not to_address:
            return func.HttpResponse(
                json.dumps({
                    "status": "config_error",
                    "error": "Missing EmployeeRegistrationAlertEmailTo or AlertEmailTo application setting."
                }, indent=2),
                status_code=500,
                mimetype="application/json"
            )

        results = []

        conn = get_sql_connection()
        cursor = conn.cursor()

        try:
            logging.info("Running validation MERGE.")
            cursor.execute(MERGE_VALIDATION_SQL)
            conn.commit()

            logging.info("Inserting invalid validation records into alert queue.")
            cursor.execute(INSERT_QUEUE_SQL)
            conn.commit()

            for _ in range(MAX_EMAILS_PER_CALL):
                results.append(process_one_email(cursor, conn, to_address))

            cursor.execute(GET_SUMMARY_SQL)
            validation_summary = fetch_one_dict(cursor)

            cursor.execute(GET_QUEUE_SUMMARY_SQL)
            queue_summary = fetch_one_dict(cursor)

        finally:
            cursor.close()
            conn.close()

        response_body = {
            "status": "completed",
            "message": "Employee registration validation completed.",
            "emailProcessing": results,
            "validationSummary": validation_summary,
            "queueSummary": queue_summary
        }

        return func.HttpResponse(
            json.dumps(response_body, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as ex:
        logging.exception("ValidateEmployeeRegistration failed.")

        return func.HttpResponse(
            json.dumps({
                "status": "failed",
                "error_type": type(ex).__name__,
                "error": str(ex)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )