import datetime


AST_TZ = datetime.timezone(datetime.timedelta(hours=3))


def to_ast_string(value):
    """
    Convert SQL UTC datetime to AST string.
    SQL datetime values are assumed to be UTC.
    """
    if value is None:
        return None

    if isinstance(value, datetime.datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)

        return value.astimezone(AST_TZ).strftime("%Y-%m-%d %H:%M:%S AST")

    return value