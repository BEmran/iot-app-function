import json
import os
import datetime
import azure.functions as func

from shared_code.graph_email import send_graph_email


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        to_address = req.params.get("to") or os.environ.get("AlertEmailTo")

        if not to_address:
            return func.HttpResponse(
                json.dumps({
                    "status": "config_error",
                    "error": "AlertEmailTo is missing and no 'to' query parameter was provided."
                }, indent=2),
                status_code=500,
                mimetype="application/json"
            )

        subject = "IoT Monitoring Test Email - Microsoft Graph"
        body = f"""
        <html>
        <body>
            <p><strong>This is a test email from the Azure IoT Monitoring Function App using Microsoft Graph.</strong></p>
            <p>UTC time: {datetime.datetime.utcnow().isoformat()}Z</p>
            <p>If you received this email, Graph-based alert sending is working.</p>
            <p><em>-- IoT Monitoring System</em></p>
        </body>
        </html>
        """

        result = send_graph_email(to_address, subject, body)

        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "message": "Graph test email sent.",
                "result": result
            }, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({
                "status": "email_failed",
                "error_type": type(e).__name__,
                "error": str(e)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )