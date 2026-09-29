"""Railway Infrastructure as Code for the canvas-mcp-lite service.

Replaces the deprecated railway.json. Apply changes with:
    railway config plan     # preview (needs the dev extra: railway-sdk)
    railway config apply    # push to Railway after review

`preserve()` keeps a variable exactly as it is set in the Railway dashboard
without putting its value in this file. Removing an entry from `env` makes
`railway config apply` DELETE that variable, so keep every secret listed.
"""

from railway_sdk import define_railway, preserve, project, service


@define_railway
def main(ctx=None):
    canvas_mcp_lite = service(
        "canvas-mcp-lite",
        start="python -m canvas_mcp_lite.server",
        replicas={"us-east4-eqdc4a": 1},
        env={
            "CANVAS_API_TOKEN": preserve(),
            "CANVAS_API_URL": preserve(),
            "GOOGLE_OAUTH_CLIENT_ID": preserve(),
            "GOOGLE_OAUTH_CLIENT_SECRET": preserve(),
            "GOOGLE_OAUTH_REFRESH_TOKEN": preserve(),
            "MCP_PATH": preserve(),
            "MCP_TRANSPORT": preserve(),
        },
    )
    return project("canvas-mcp-lite", resources=[canvas_mcp_lite])
