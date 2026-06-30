import boto3

from app.config import settings

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.yandex_storage_endpoint_url,
            aws_access_key_id=settings.yandex_storage_access_key_id,
            aws_secret_access_key=settings.yandex_storage_secret_access_key,
            region_name="ru-central1",
        )
    return _client


def upload(key: str, data: bytes) -> None:
    _get_client().put_object(Bucket=settings.yandex_storage_bucket, Key=key, Body=data)


def presigned_url(key: str, expires_in: int = 3600) -> str:
    return _get_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.yandex_storage_bucket, "Key": key},
        ExpiresIn=expires_in,
    )


def delete(key: str) -> None:
    _get_client().delete_object(Bucket=settings.yandex_storage_bucket, Key=key)
