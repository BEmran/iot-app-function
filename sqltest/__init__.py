import azure.functions as func
import json
import os
import time


def main(req: func.HttpRequest) -> func.HttpResponse:
    start = time.time()

    try:
        import pymssql

        server = os.getenv("SqlServer")
        database = os.getenv("SqlDatabase")
        username = os.getenv("SqlUser")
        password = os.getenv("SqlPassword")

        missing = [
            name for name, value in {
                "SqlServer": server,
                "SqlDatabase": database,
                "SqlUser": username,
                "SqlPassword": password
            }.items()
            if not value
        ]

        if missing:
            return func.HttpResponse(
                json.dumps({
                    "status": "config_error",
                    "missing_settings": missing
                }, indent=2),
                status_code=500,
                mimetype="application/json"
            )

        with pymssql.connect(
            server=server,
            user=username,
            password=password,
            database=database,
            port=1433,
            tds_version="7.4",
            login_timeout=10,
            timeout=10,
            as_dict=True
        ) as conn:
            with conn.cursor() as cursor:
                cursor.execute("""
                    SELECT
                        DB_NAME() AS DatabaseName,
                        SUSER_SNAME() AS LoginName,
                        CONVERT(varchar(50), SYSDATETIMEOFFSET(), 127) AS SqlTime
                """)
                row = cursor.fetchone()

        return func.HttpResponse(
            json.dumps({
                "status": "success",
                "location": "azure_function",
                "database": row["DatabaseName"],
                "login": row["LoginName"],
                "sql_time": row["SqlTime"],
                "elapsed_seconds": round(time.time() - start, 2),
                "driver": "pymssql"
            }, indent=2),
            status_code=200,
            mimetype="application/json"
        )

    except Exception as e:
        return func.HttpResponse(
            json.dumps({
                "status": "sql_connection_failed",
                "error_type": type(e).__name__,
                "error": str(e),
                "elapsed_seconds": round(time.time() - start, 2)
            }, indent=2),
            status_code=500,
            mimetype="application/json"
        )
