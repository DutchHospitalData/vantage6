from pathlib import Path
from vantage6.common.globals import APPNAME

#
#   NODE SETTINGS
#
DEFAULT_NODE_SYSTEM_FOLDERS = False

#
#   INSTALLATION SETTINGS
#
PACKAGE_FOLDER = Path(__file__).parent.parent.parent

NODE_PROXY_SERVER_HOSTNAME = "proxyserver"

DATA_FOLDER = PACKAGE_FOLDER / APPNAME / "_data"

# with open(Path(PACKAGE_FOLDER) / APPNAME / "node" / "VERSION") as f:
#     VERSION = f.read()


# constants for retrying node login
SLEEP_BTWN_NODE_LOGIN_TRIES = 10  # retry every 10s
TIME_LIMIT_RETRY_CONNECT_NODE = 60 * 60 * 24 * 7  # i.e. 1 week

# constant for waiting for the initial websocket connection
TIME_LIMIT_INITIAL_CONNECTION_WEBSOCKET = 60

# Upper bound for the exponential backoff between websocket reconnect attempts.
# python-socketio defaults to 5 seconds, which means a fleet of nodes keeps
# hammering a struggling server roughly every 5 seconds indefinitely. Nodes
# still retry forever, just less aggressively. Override per node with
# `socketio.reconnection_delay_max` in the node configuration file.
#
# Note that python-socketio adds only a fixed +/- 0.5 second of jitter on top
# of this delay, so reconnect attempts stay roughly synchronised across a
# collaboration. That is harmless for a handful of nodes, but a large fleet
# would also need a higher `randomization_factor` to spread the load out.
DEFAULT_SOCKET_RECONNECTION_DELAY_MAX = 60

# Pause after an unexpected error in a worker loop that talks to the server.
# Without it a failing loop retries as fast as the CPU allows, which turns one
# broken run into a stream of requests.
ERROR_RETRY_DELAY_SECONDS = 10

#
#    VPN CONFIGURATION RELATED CONSTANTS
#
# TODO move part of these constants elsewhere?! Or make context?
VPN_CLIENT_IMAGE = "ghcr.io/vantage6/infrastructure/vpn-client"
NETWORK_CONFIG_IMAGE = "ghcr.io/vantage6/infrastructure/vpn-configurator"
ALPINE_IMAGE = "ghcr.io/vantage6/infrastructure/alpine"
MAX_CHECK_VPN_ATTEMPTS = 60  # max attempts to obtain VPN IP (1 second apart)
FREE_PORT_RANGE = range(49152, 65535)
DEFAULT_ALGO_VPN_PORT = "8888"  # default VPN port for algorithm container

#
#   SSH TUNNEL RELATED CONSTANTS
#
SSH_TUNNEL_IMAGE = "ghcr.io/vantage6/infrastructure/ssh-tunnel"

#
#   SQUID RELATED CONSTANTS
#
SQUID_IMAGE = "ghcr.io/vantage6/infrastructure/squid"

# Environment variables that should be set in the Dockerfile and that may not
# be overwritten by the user.
ENV_VARS_NOT_SETTABLE_BY_NODE = ["PKG_NAME"]

# default policies
DEFAULT_REQUIRE_ALGO_IMAGE_PULL = True
