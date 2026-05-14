import json
import logging
import datetime
import html

import azure.functions as func

from shared_code.iot_logic import get_sql_connection
from shared_code.graph_email import send_graph_email


AST_TZ = datetime.timezone(datetime.timedelta(hours=3))
def to_ast_string(value):
    """
    Convert SQL UTC datetime to AST string.
    SQL datetime values are assumed to be UTC.
    """
    if value is None:
        return None

    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)

        return value.astimezone(AST_TZ).strftime("%Y-%m-%d %H:%M:%S AST")

    return value

GET_INCIDENT_BY_VALIDATION_ID_SQL = """
SELECT TOP (1)
    ValidationId,
    EmpId,
    ValidationStatus,
    FailedChecks,
    AlertStatus,
    EmailSent,
    EmailSentUtc,
    IncidentLifecycleStatus,
    AcknowledgedBy,
    AcknowledgedUtc,
    ClosedBy,
    ClosedUtc,
    ClosureNotes
FROM TAIoT.dbo.EmployeeRegistrationValidation
WHERE ValidationId = %s;
"""


GET_INCIDENT_BY_EMP_ID_SQL = """
SELECT TOP (1)
    ValidationId,
    EmpId,
    ValidationStatus,
    FailedChecks,
    AlertStatus,
    EmailSent,
    EmailSentUtc,
    IncidentLifecycleStatus,
    AcknowledgedBy,
    AcknowledgedUtc,
    ClosedBy,
    ClosedUtc,
    ClosureNotes
FROM TAIoT.dbo.EmployeeRegistrationValidation
WHERE EmpId = %s
ORDER BY ValidationId DESC;
"""


MARK_CLOSED_SQL = """
UPDATE TAIoT.dbo.EmployeeRegistrationValidation
SET
    IncidentLifecycleStatus = 'CLOSED',
    AcknowledgedBy = %s,
    AcknowledgedUtc = SYSUTCDATETIME(),
    ClosedBy = %s,
    ClosedUtc = SYSUTCDATETIME(),
    ClosureNotes = %s,
    IsResolved = 1,
    ResolvedUtc = CASE WHEN ResolvedUtc IS NULL THEN SYSUTCDATETIME() ELSE ResolvedUtc END,
    ResolutionStatus = 'CLOSED_ACKNOWLEDGED',
    UpdatedUtc = SYSUTCDATETIME(),
    CloseEmailError = NULL
WHERE ValidationId = %s;
"""


MARK_CLOSE_EMAIL_SENT_SQL = """
UPDATE TAIoT.dbo.EmployeeRegistrationValidation
SET
    CloseEmailSent = 1,
    CloseEmailSentUtc = SYSUTCDATETIME(),
    CloseEmailError = NULL,
    UpdatedUtc = SYSUTCDATETIME()
WHERE ValidationId = %s;
"""


MARK_CLOSE_EMAIL_FAILED_SQL = """
UPDATE TAIoT.dbo.EmployeeRegistrationValidation
SET
    CloseEmailSent = 0,
    CloseEmailError = %s,
    UpdatedUtc = SYSUTCDATETIME()
WHERE ValidationId = %s;
"""


def row_to_dict(cursor, row):
    columns = [col[0] for col in cursor.description]
    result = {}

    for i, col in enumerate(columns):
        value = row[i]

        if hasattr(value, "isoformat"):
            value = to_ast_string(value)

            if col.endswith("Utc"):
                col = col[:-3] + "Ast"

        result[col] = value

    return result


def fetch_incident(cursor, validation_id=None, emp_id=None):
    if validation_id is not None:
        cursor.execute(GET_INCIDENT_BY_VALIDATION_ID_SQL, (validation_id,))
    else:
        cursor.execute(GET_INCIDENT_BY_EMP_ID_SQL, (emp_id,))

    row = cursor.fetchone()
    if not row:
        return None

    return row_to_dict(cursor, row)


def ast_now_text():
    return datetime.datetime.now(
        datetime.timezone(datetime.timedelta(hours=3))
    ).strftime("%Y-%m-%d %H:%M:%S AST")


def build_closure_email(incident, closed_by, closure_notes):
    emp_id = html.escape(str(incident.get("EmpId", "")))
    validation_id = html.escape(str(incident.get("ValidationId", "")))
    closed_by_safe = html.escape(str(closed_by or ""))
    notes_safe = html.escape(str(closure_notes or "No notes provided."))

    closed_time = ast_now_text()

    subject = f"Employee Registration Incident Closed - Emp ID {emp_id}"

    body = f"""
<html>
<body style="font-family: Arial, sans-serif; font-size: 14px; color: #222;">
    <p>Dear Team,</p>

    <p>
        The following employee registration validation incident has been acknowledged and closed.
    </p>

    <table border="1" cellpadding="8" cellspacing="0"
           style="border-collapse: collapse; border: 1px solid #999; width: 100%; max-width: 750px;">

        <tr style="background-color: #d4edda;">
            <td style="font-weight: bold; width: 220px;">Incident Status</td>
            <td style="font-weight: bold; color: #155724;">CLOSED</td>
        </tr>

        <tr>
            <td style="font-weight: bold;">Employee ID</td>
            <td>{emp_id}</td>
        </tr>

        <tr>
            <td style="font-weight: bold;">Validation ID</td>
            <td>{validation_id}</td>
        </tr>

        <tr style="background-color: #d1ecf1;">
            <td style="font-weight: bold;">Closed By</td>
            <td>{closed_by_safe}</td>
        </tr>

        <tr style="background-color: #d1ecf1;">
            <td style="font-weight: bold;">Closed Time</td>
            <td>{closed_time}</td>
        </tr>

        <tr style="background-color: #d1ecf1;">
            <td style="font-weight: bold;">Closure Notes</td>
            <td>{notes_safe}</td>
        </tr>
    </table>

    <p style="margin-top: 16px;">
        This message confirms that the incident was acknowledged and closed in the IoT monitoring system.
    </p>

    <p>-- IoT Monitoring System</p>
</body>
</html>
"""

    return subject, body


def get_request_json(req):
    try:
        return req.get_json()
    except ValueError:
        return {}


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("CloseEmployeeRegistrationIncident endpoint started.")

    try:
        body = get_request_json(req)

        validation_id = body.get("validationId") or req.params.get("validationId")
        emp_id = body.get("empId") or req.params.get("empId")
        closed_by = body.get("closedBy") or req.params.get("closedBy") or "Unknown user"
        closure_notes = body.get("notes") or body.get("closureNotes") or req.params.get("notes") or ""

        to_address = (
            body.get("emailTo")
            or req.params.get("emailTo")
            or body.get("to")
            or req.params.get("to")
        )

        if not to_address:
            import os
            to_address = (
                os.environ.get("EmployeeRegistrationAlertEmailTo")
                or os.environ.get("AlertEmailTo")
            )

        if not validation_id and not emp_id:
            return func.HttpResponse(
                json.dumps({
                    "status": "failed",
                    "error": "Provide either validationId or empId."
                }, indent=2),
                status_code=400,
                mimetype="application/json"
            )

        if not to_address:
            return func.HttpResponse(
                json.dumps({
                    "status": "failed",
                    "error": "Missing recipient email. Provide emailTo or configure EmployeeRegistrationAlertEmailTo."
                }, indent=2),
                status_code=400,
                mimetype="application/json"
            )

        if validation_id is not None:
            try:
                validation_id = int(validation_id)
            except ValueError:
                return func.HttpResponse(
                    json.dumps({
                        "status": "failed",
                        "error": "validationId must be an integer."
                    }, indent=2),
                    status_code=400,
                    mimetype="application/json"
                )

        conn = get_sql_connection()
        cursor = conn.cursor()

        try:
            incident = fetch_incident(cursor, validation_id=validation_id, emp_id=emp_id)

            if not incident:
                return func.HttpResponse(
                    json.dumps({
                        "status": "failed",
                        "error": "Incident was not found."
                    }, indent=2),
                    status_code=404,
                    mimetype="application/json"
                )

            validation_id = incident["ValidationId"]

            if incident.get("IncidentLifecycleStatus") == "CLOSED":
                return func.HttpResponse(
                    json.dumps({
                        "status": "already_closed",
                        "message": "Incident is already closed.",
                        "incident": incident
                    }, indent=2, default=str),
                    status_code=200,
                    mimetype="application/json"
                )

            cursor.execute(
                MARK_CLOSED_SQL,
                (closed_by, closed_by, closure_notes, validation_id)
            )
            conn.commit()

            subject, email_body = build_closure_email(
                incident=incident,
                closed_by=closed_by,
                closure_notes=closure_notes
            )

            email_status = "not_sent"

            try:
                send_graph_email(to_address, subject, email_body)

                cursor.execute(MARK_CLOSE_EMAIL_SENT_SQL, (validation_id,))
                conn.commit()

                email_status = "sent"

            except Exception as email_ex:
                error_text = f"{type(email_ex).__name__}: {str(email_ex)}"

                cursor.execute(
                    MARK_CLOSE_EMAIL_FAILED_SQL,
                    (error_text, validation_id)
                )
                conn.commit()

                email_status = "failed"

                logging.exception("Closure email failed.")

            updated_incident = fetch_incident(cursor, validation_id=validation_id)

        finally:
            cursor.close()
            conn.close()

        return func.HttpResponse(
            json.dumps({
                "status": "closed",
                "message": "Employee registration incident was closed.",
                "emailStatus": email_status,
                "emailTo": to_address,
                "validationId": validation_id,
                "empId": updated_incident.get("EmpId") if updated_incident else emp_id,
                "closedBy": closed_by,
                "closureNotes": closure_notes,
                "incident": updated_incident
            }, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as ex:
        logging.exception("CloseEmployeeRegistrationIncident failed.")

        return func.HttpResponse(
            json.dumps({
                "status": "failed",
                "error_type": type(ex).__name__,
                "error": str(ex)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )

