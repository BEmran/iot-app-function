import json
import requests
import azure.functions as func

from shared_code.iothub_rest import parse_iothub_connection_string


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        parts = parse_iothub_connection_string()
        host = parts["HostName"]
        url = f"https://{host}/"

        try:
            response = requests.get(url, timeout=15)

            return func.HttpResponse(
                json.dumps({
                    "status": "reachable",
                    "host": host,
                    "url": url,
                    "http_status": response.status_code,
                    "response_preview": response.text[:300]
                }, indent=2),
                status_code=200,
                mimetype="application/json"
            )

        except Exception as network_ex:
            return func.HttpResponse(
                json.dumps({
                    "status": "network_failed",
                    "host": host,
                    "url": url,
                    "error_type": type(network_ex).__name__,
                    "error": str(network_ex)
                }, indent=2),
                status_code=500,
                mimetype="application/json"
            )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({
                "status": "config_error",
                "error_type": type(e).__name__,
                "error": str(e)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )
