import json
import logging
import azure.functions as func

from shared_code.downtime_impact_by_site import get_downtime_impact_by_site_data


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("DowntimeImpactBySite endpoint started.")

    try:
        result = get_downtime_impact_by_site_data(req)

        return func.HttpResponse(
            json.dumps(result, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as ex:
        logging.exception("DowntimeImpactBySite endpoint failed.")

        return func.HttpResponse(
            json.dumps({
                "status": "error",
                "error_type": type(ex).__name__,
                "error": str(ex)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )