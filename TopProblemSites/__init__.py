import json
import logging
import azure.functions as func

from shared_code.top_problem_sites import get_top_problem_sites_data


def main(req: func.HttpRequest) -> func.HttpResponse:
    logging.info("TopProblemSites endpoint started.")

    try:
        result = get_top_problem_sites_data(req)

        return func.HttpResponse(
            json.dumps(result, indent=2, default=str),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as ex:
        logging.exception("TopProblemSites endpoint failed.")

        return func.HttpResponse(
            json.dumps({
                "status": "failed",
                "error_type": type(ex).__name__,
                "error": str(ex)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )