"""Per-vendor OAuth provider configs. Each provider exposes:

  build_authorize_url(client_id, redirect_uri, state, code_challenge) -> str
  exchange_code(code, code_verifier, client_id, client_secret, redirect_uri) -> dict
  refresh_token(refresh_token, client_id, client_secret) -> dict

The dict shape is consistent across providers — see slack.py for the keys.
"""
