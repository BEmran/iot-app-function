import json
import azure.functions as func

from shared_code.iothub_rest import test_iothub_rest_connection


def main(req: func.HttpRequest) -> func.HttpResponse:
    try:
        result = test_iothub_rest_connection()

        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "message": "IoT Hub connection string parsed successfully. REST method path is ready.",
                "result": result
            }, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({
                "status": "iothub_test_failed",
                "error_type": type(e).__name__,
                "error": str(e)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )
