import os
import requests


GRAPH_TOKEN_URL_TEMPLATE = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_SENDMAIL_URL_TEMPLATE = "https://graph.microsoft.com/v1.0/users/{from_address}/sendMail"


def get_graph_token():
    tenant_id = os.environ["GraphTenantId"]
    client_id = os.environ["GraphClientId"]
    client_secret = os.environ["GraphClientSecret"]

    token_url = GRAPH_TOKEN_URL_TEMPLATE.format(tenant_id=tenant_id)

    data = {
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials"
    }

    response = requests.post(token_url, data=data, timeout=30)

    if response.status_code >= 400:
        raise RuntimeError(
            f"Graph token request failed: HTTP {response.status_code} - {response.text}"
        )

    return response.json()["access_token"]


def send_graph_email(to_address: str, subject: str, html_body: str):
    from_address = os.environ["GraphFromAddress"]
    access_token = get_graph_token()

    send_url = GRAPH_SENDMAIL_URL_TEMPLATE.format(from_address=from_address)

    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body
            },
            "toRecipients": [
                {
                    "emailAddress": {
                        "address": to_address
                    }
                }
            ]
        },
        "saveToSentItems": "true"
    }

    response = requests.post(
        send_url,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json"
        },
        json=payload,
        timeout=30
    )

    if response.status_code not in [202]:
        raise RuntimeError(
            f"Graph sendMail failed: HTTP {response.status_code} - {response.text}"
        )

    return {
        "status_code": response.status_code,
        "from": from_address,
        "to": to_address
    }