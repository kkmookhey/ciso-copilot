#!/bin/bash
# LWA entrypoint for ChatStreamFn.
#
# The Lambda Web Adapter layer is attached and AWS_LAMBDA_EXEC_WRAPPER is set
# to /opt/bootstrap; the layer starts this script, waits for the web server to
# be listening on $AWS_LWA_PORT, then proxies the Function URL request to it.
#
# uvicorn serves the Starlette ASGI app in app.py on the LWA port. We start a
# single worker — a Lambda execution environment serves one request at a time.
exec python -m uvicorn app:app --host 0.0.0.0 --port "${AWS_LWA_PORT:-8080}"
