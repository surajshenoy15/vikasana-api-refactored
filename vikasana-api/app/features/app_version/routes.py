import os

from fastapi import APIRouter, Response


router = APIRouter(
    prefix="/app",
    tags=["App Version"],
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)

    if value is None:
        return default

    return value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@router.get("/version-policy")
async def get_version_policy(response: Response):
    """
    Public mobile app version policy.

    No authentication is required because the mobile app must be able
    to check its minimum supported version before login.
    """

    # Do not allow stale version-policy responses to be cached.
    response.headers["Cache-Control"] = "no-store"

    android_minimum = os.getenv(
        "APP_ANDROID_MIN_VERSION",
        "1.0.1",
    )
    android_latest = os.getenv(
        "APP_ANDROID_LATEST_VERSION",
        android_minimum,
    )

    ios_minimum = os.getenv(
        "APP_IOS_MIN_VERSION",
        "1.0.1",
    )
    ios_latest = os.getenv(
        "APP_IOS_LATEST_VERSION",
        ios_minimum,
    )

    return {
        "android": {
            "minimum_version": android_minimum,
            "latest_version": android_latest,
            "force_update": _env_bool(
                "APP_ANDROID_FORCE_UPDATE",
                False,
            ),
            "store_url": (
                "https://play.google.com/store/apps/details"
                "?id=com.surajshenoy15.socialactivitytracker"
            ),
        },
        "ios": {
            "minimum_version": ios_minimum,
            "latest_version": ios_latest,
            "force_update": _env_bool(
                "APP_IOS_FORCE_UPDATE",
                False,
            ),
            "store_url": (
                "https://apps.apple.com/app/id6778459793"
            ),
        },
    }
