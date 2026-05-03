import json
import azure.functions as func

from shared_code.incident_polling import process_incident_events_once


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        result = process_incident_events_once()

        return func.HttpResponse(
            json.dumps(result, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({
                "status": "error",
                "error_type": type(e).__name__,
                "error": str(e)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )
