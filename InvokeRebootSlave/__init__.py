import json
import datetime
import azure.functions as func

from shared_code.iot_logic import get_sql_connection
from shared_code.iothub_rest import invoke_direct_method


def _to_bool(value: str) -> bool:
    return str(value).lower() in ["1", "true", "yes", "y"]


def _audit_command(device_id, method_name, payload, result_code, result_message, requested_by):
    conn = get_sql_connection()
    cursor = conn.cursor()

    try:
        cursor.execute("""
            INSERT INTO dbo.CommandAudit
                (DeviceId, MethodName, RequestedBy, RequestedUtc, Payload, ResultCode, ResultMessage, CompletedUtc)
            VALUES (%s, %s, %s, GETUTCDATE(), %s, %s, %s, GETUTCDATE())
        """, (
            device_id,
            method_name,
            requested_by,
            json.dumps(payload),
            result_code,
            result_message
        ))

        conn.commit()

    finally:
        cursor.close()
        conn.close()


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        device_id = req.params.get("deviceId")
        confirm = _to_bool(req.params.get("confirm", "false"))
        requested_by = req.params.get("requestedBy") or "manual-http-test"

        if not device_id:
            return func.HttpResponse(
                json.dumps({
                    "status": "bad_request",
                    "error": "Missing required query parameter: deviceId"
                }, indent=2),
                status_code=400,
                mimetype="application/json"
            )

        method_name = "reboot_slave"
        payload = {}

        if not confirm:
            return func.HttpResponse(
                json.dumps({
                    "status": "dry_run",
                    "message": "No command was sent. Add confirm=true to invoke reboot_slave.",
                    "deviceId": device_id,
                    "methodName": method_name,
                    "payload": payload
                }, indent=2),
                status_code=200,
                mimetype="application/json"
            )

        result = invoke_direct_method(
            device_id=device_id,
            method_name=method_name,
            payload=payload,
            response_timeout=30,
            connect_timeout=30
        )

        result_code = result.get("http_status")
        result_message = json.dumps({
            "iotHubResponse": result.get("response"),
            "utc": datetime.datetime.utcnow().isoformat() + "Z"
        }, default=str)

        try:
            _audit_command(
                device_id=device_id,
                method_name=method_name,
                payload=payload,
                result_code=result_code,
                result_message=result_message,
                requested_by=requested_by
            )
        except Exception as audit_ex:
            result_message = json.dumps({
                "iotHubResponse": result.get("response"),
                "audit_error": str(audit_ex),
                "utc": datetime.datetime.utcnow().isoformat() + "Z"
            }, default=str)

        return func.HttpResponse(
            json.dumps({
                "status": "command_invoked",
                "deviceId": device_id,
                "methodName": method_name,
                "resultCode": result_code,
                "result": result.get("response")
            }, indent=2, default=str),
            status_code=200 if result_code and result_code < 400 else 500,
            mimetype="application/json"
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({
                "status": "command_failed",
                "error_type": type(e).__name__,
                "error": str(e)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )
