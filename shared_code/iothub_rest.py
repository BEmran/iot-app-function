import base64
import hashlib
import hmac
import os
import time
import urllib.parse
import requests


def parse_iothub_connection_string():
    cs = os.environ.get("IoTHubServiceConnectionString")

    if not cs:
        raise ValueError("IoTHubServiceConnectionString is missing.")

    parts = {}

    for item in cs.split(";"):
        if "=" in item:
            key, value = item.split("=", 1)
            parts[key] = value

    required = ["HostName", "SharedAccessKeyName", "SharedAccessKey"]

    for key in required:
        if key not in parts or not parts[key]:
            raise ValueError(f"IoTHubServiceConnectionString missing {key}")

    return parts


def build_sas_token(resource_uri, key_name, key, ttl_seconds=3600):
    expiry = int(time.time()) + ttl_seconds
    encoded_resource_uri = urllib.parse.quote(resource_uri, safe="").lower()

    string_to_sign = f"{encoded_resource_uri}\n{expiry}".encode("utf-8")
    decoded_key = base64.b64decode(key)

    signature = base64.b64encode(
        hmac.new(decoded_key, string_to_sign, hashlib.sha256).digest()
    )

    encoded_signature = urllib.parse.quote(signature)

    return (
        f"SharedAccessSignature sr={encoded_resource_uri}"
        f"&sig={encoded_signature}"
        f"&se={expiry}"
        f"&skn={key_name}"
    )


def invoke_direct_method(device_id, method_name, payload=None, response_timeout=30, connect_timeout=30):
    parts = parse_iothub_connection_string()

    host = parts["HostName"]
    key_name = parts["SharedAccessKeyName"]
    key = parts["SharedAccessKey"]

    resource_uri = host
    sas_token = build_sas_token(resource_uri, key_name, key)

    url = (
        f"https://{host}/twins/{urllib.parse.quote(device_id, safe='')}"
        f"/methods?api-version=2021-04-12"
    )

    body = {
        "methodName": method_name,
        "responseTimeoutInSeconds": response_timeout,
        "connectTimeoutInSeconds": connect_timeout,
        "payload": payload or {}
    }

    response = requests.post(
        url,
        headers={
            "Authorization": sas_token,
            "Content-Type": "application/json"
        },
        json=body,
        timeout=connect_timeout + response_timeout + 10
    )

    try:
        response_body = response.json()
    except Exception:
        response_body = response.text

    return {
        "http_status": response.status_code,
        "response": response_body
    }


def test_iothub_rest_connection():
    parts = parse_iothub_connection_string()

    return {
        "hostName": parts["HostName"],
        "sharedAccessKeyName": parts["SharedAccessKeyName"],
        "connectionStringParsed": True
    }
