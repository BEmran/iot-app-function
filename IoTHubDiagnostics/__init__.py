import json
import socket
import ssl
import requests
import azure.functions as func

from shared_code.iothub_rest import parse_iothub_connection_string


def main(req: func.HttpRequest) -> func.HttpResponse:
    result = {
        "status": "started",
        "dns": None,
        "tcp_443": None,
        "tls": None,
        "https_get": None
    }

    try:
        parts = parse_iothub_connection_string()
        host = parts["HostName"]
        result["host"] = host

        # DNS test
        try:
            addresses = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            result["dns"] = {
                "status": "success",
                "addresses": list({a[4][0] for a in addresses})
            }
        except Exception as e:
            result["dns"] = {
                "status": "failed",
                "error_type": type(e).__name__,
                "error": str(e)
            }

        # TCP test
        try:
            sock = socket.create_connection((host, 443), timeout=10)
            result["tcp_443"] = {"status": "success"}
        except Exception as e:
            result["tcp_443"] = {
                "status": "failed",
                "error_type": type(e).__name__,
                "error": str(e)
            }
            return func.HttpResponse(json.dumps(result, indent=2), status_code=500, mimetype="application/json")

        # TLS test
        try:
            context = ssl.create_default_context()
            tls_sock = context.wrap_socket(sock, server_hostname=host)
            result["tls"] = {
                "status": "success",
                "cipher": tls_sock.cipher()[0] if tls_sock.cipher() else None,
                "tls_version": tls_sock.version()
            }
            tls_sock.close()
        except Exception as e:
            result["tls"] = {
                "status": "failed",
                "error_type": type(e).__name__,
                "error": str(e)
            }
            return func.HttpResponse(json.dumps(result, indent=2), status_code=500, mimetype="application/json")

        # HTTPS GET test
        try:
            r = requests.get(f"https://{host}/", timeout=15)
            result["https_get"] = {
                "status": "completed",
                "http_status": r.status_code,
                "response_preview": r.text[:300]
            }
            result["status"] = "completed"
            return func.HttpResponse(json.dumps(result, indent=2), status_code=200, mimetype="application/json")
        except Exception as e:
            result["https_get"] = {
                "status": "failed",
                "error_type": type(e).__name__,
                "error": str(e)
            }
            return func.HttpResponse(json.dumps(result, indent=2), status_code=500, mimetype="application/json")

    except Exception as e:
        result["status"] = "failed"
        result["error_type"] = type(e).__name__
        result["error"] = str(e)
        return func.HttpResponse(json.dumps(result, indent=2), status_code=500, mimetype="application/json")