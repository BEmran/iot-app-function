import json
import azure.functions as func
from shared_code.dashboard_cache import refresh_operational_dashboard_cache


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        result = refresh_operational_dashboard_cache()

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