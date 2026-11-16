"""Cloudflare Access application JWT validation; no fallback/bypass authentication."""
import json
import re
import threading
import time
import urllib.request

import jwt


class AccessDenied(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AccessVerifier:
    def __init__(self, config):
        self.config = config
        self._keys = {}
        self._loaded_at = 0
        self._last_attempt = float('-inf')
        self._lock = threading.Lock()

    def _fetch_keys(self):
        # The URL comes solely from validated local config, never JWT iss/jku/x5u.
        url = self.config.issuer + '/cdn-cgi/access/certs'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(urllib.request.Request(url, headers={'Accept':'application/json'}), timeout=3) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise AccessDenied('jwks_size')
        raw = json.loads(data)
        keys = raw.get('keys')
        if not isinstance(keys, list) or not 1 <= len(keys) <= 10:
            raise AccessDenied('jwks_format')
        resolved = {}
        for key in keys:
            kid = key.get('kid')
            if not isinstance(kid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', kid) or kid in resolved:
                raise AccessDenied('jwks_kid')
            if key.get('kty') != 'RSA' or key.get('alg','RS256') != 'RS256' or key.get('use','sig') != 'sig' or 'd' in key:
                raise AccessDenied('jwks_algorithm')
            public = jwt.PyJWK.from_dict(key, algorithm='RS256').key
            if not 2048 <= public.key_size <= 8192:
                raise AccessDenied('jwks_key_size')
            resolved[kid] = public
        return resolved

    def _key(self, kid):
        with self._lock:
            stamp = time.monotonic()
            if stamp - self._loaded_at < 300 and kid in self._keys:
                return self._keys[kid]
            if stamp - self._last_attempt < 30:
                raise AccessDenied('jwks_refresh_throttled')
            self._last_attempt = stamp
            try:
                keys = self._fetch_keys()
            except Exception:
                raise AccessDenied('jwks_unavailable') from None
            self._keys, self._loaded_at = keys, stamp
            if kid not in keys:
                raise AccessDenied('unknown_key')
            return keys[kid]

    def verify(self, token):
        try:
            if not isinstance(token, str) or not 1 <= len(token) <= 16384:
                raise AccessDenied('missing_token')
            header = jwt.get_unverified_header(token)
            kid = header.get('kid')
            if header.get('alg') != 'RS256' or not isinstance(kid, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',kid):
                raise AccessDenied('token_header')
            if any(field in header for field in ('crit','jku','jwk','x5u')):
                raise AccessDenied('untrusted_header')
            claims = jwt.decode(token, self._key(kid), algorithms=['RS256'],
                                audience=self.config.audience, issuer=self.config.issuer,
                                options={'require':['exp','iat','iss','aud','sub','email']}, leeway=5)
            if type(claims['iat']) is not int or type(claims['exp']) is not int or claims['exp'] <= claims['iat']:
                raise AccessDenied('token_time')
            if not -5 <= time.time()-claims['iat'] <= self.config.token_max_age:
                raise AccessDenied('token_age')
            if not isinstance(claims['sub'], str) or not claims['sub']:
                raise AccessDenied('token_subject')
            email = claims['email']
            if not isinstance(email,str) or email.casefold() not in self.config.allowed_emails:
                raise AccessDenied('identity_not_allowed')
            return claims
        except AccessDenied:
            raise
        except Exception:
            raise AccessDenied('invalid_token') from None
