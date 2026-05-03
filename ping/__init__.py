import azure.functions as func
import datetime
import json
import os

def main(req: func.HttpRequest) -> func.HttpResponse:
    body = {
        "status": "ok",
        "message": "Production Function App is running",
        "utc": datetime.datetime.utcnow().isoformat() + "Z",
        "worker_runtime": os.getenv("FUNCTIONS_WORKER_RUNTIME", "not_set"),
        "website_site_name": os.getenv("WEBSITE_SITE_NAME", "not_set")
    }

    return func.HttpResponse(
        json.dumps(body, indent=2),
        status_code=200,
        mimetype="application/json"
    )
