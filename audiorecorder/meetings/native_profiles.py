"""Native provider identity shared by configuration and detector adapters."""

PROFILES = {
    "com.microsoft.teams2": "Microsoft Teams",
    "us.zoom.xos": "Zoom",
}
SIGNING_POLICIES = {
    "com.microsoft.teams2": {
        "identity": "UBF8T346G9:com.microsoft.teams2",
        "team_id": "UBF8T346G9",
        "validation_categories": (6,),  # Developer ID
    },
    "us.zoom.xos": {
        "identity": "BJ4HAAB9B3:us.zoom.xos",
        "team_id": "BJ4HAAB9B3",
        "validation_categories": (6,),  # Developer ID
    },
}
MATCHER_REVISION = "call-controls-v4-signed-provider"


def signing_identity(bundle):
    policy = SIGNING_POLICIES.get(bundle)
    return policy["identity"] if policy else ""
