"""Explicit, non-secret configuration for the read-only external viewer."""
from dataclasses import dataclass
import json
import hashlib
from pathlib import Path
import re
from urllib.parse import urlsplit


@dataclass(frozen=True)
class RemoteConfig:
    enabled: bool
    public_origin: str
    team_domain: str
    audience: str
    allowed_emails: tuple[str, ...]
    shared_policies: tuple[tuple[str, str], ...]
    snapshot_max_age: int = 300
    token_max_age: int = 3600

    @property
    def issuer(self):
        return 'https://' + self.team_domain

    @property
    def hostname(self):
        return urlsplit(self.public_origin).netloc

    @property
    def selection_revision(self):
        return hashlib.sha256(json.dumps(self.shared_policies,ensure_ascii=False).encode()).hexdigest()


def load_config(path):
    raw = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(raw, dict) or type(raw.get('enabled')) is not bool:
        raise ValueError('enabled를 명시적으로 true 또는 false로 지정하세요.')
    allowed_keys = {'enabled','public_origin','team_domain','audience','allowed_emails',
                    'shared_policies','snapshot_max_age','token_max_age'}
    if set(raw) - allowed_keys:
        raise ValueError('알 수 없는 원격 설정 항목이 있습니다.')
    origin = raw.get('public_origin', '')
    team = raw.get('team_domain', '')
    aud = raw.get('audience', '')
    if not isinstance(origin, str) or not re.fullmatch(r'https://[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?', origin):
        raise ValueError('public_origin은 경로·포트 없는 소문자 HTTPS 호스트여야 합니다.')
    hostname = urlsplit(origin).hostname
    if not hostname or '.' not in hostname or '..' in hostname or hostname.endswith('.localhost') or hostname.replace('.', '').isdigit():
        raise ValueError('유효한 공개 DNS 호스트명을 지정하세요.')
    if not isinstance(team, str) or not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.cloudflareaccess\.com', team):
        raise ValueError('team_domain은 자신의 팀명.cloudflareaccess.com 형식이어야 합니다.')
    if not isinstance(aud, str) or not re.fullmatch(r'[0-9a-f]{64}', aud):
        raise ValueError('Access 애플리케이션의 64자리 AUD 태그를 지정하세요.')
    emails = raw.get('allowed_emails')
    if not isinstance(emails, list) or not 1 <= len(emails) <= 10 or any(
            not isinstance(e, str) or not re.fullmatch(r'[^\s@*]+@[^\s@*]+\.[^\s@*]+', e) for e in emails):
        raise ValueError('allowed_emails에 허용할 개별 이메일을 1~10개 지정하세요. 와일드카드는 허용하지 않습니다.')
    shared = raw.get('shared_policies')
    if not isinstance(shared, list) or len(shared) > 20:
        raise ValueError('shared_policies는 최대 20개 정책 목록이어야 합니다.')
    pairs = []
    for entry in shared:
        if not isinstance(entry, dict) or set(entry) != {'id','alias'}:
            raise ValueError('공유 정책은 id와 alias만 지정하세요.')
        policy_id, alias = entry['id'], entry['alias']
        if not isinstance(policy_id, str) or not re.fullmatch(r'[0-9a-f]{32}', policy_id):
            raise ValueError('정책 ID가 올바르지 않습니다.')
        if not isinstance(alias, str) or not 1 <= len(alias.strip()) <= 50 or any(ord(c) < 32 for c in alias):
            raise ValueError('별칭은 1~50자의 한 줄 문자열이어야 합니다.')
        pairs.append((policy_id, alias.strip()))
    if len({p[0] for p in pairs}) != len(pairs) or len({p[1] for p in pairs}) != len(pairs):
        raise ValueError('공유 정책 ID와 별칭은 고유해야 합니다.')
    ages = []
    for key, default, low, high in [('snapshot_max_age',300,30,600), ('token_max_age',3600,300,3600)]:
        value = raw.get(key, default)
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f'{key}는 {low}~{high} 사이 정수여야 합니다.')
        ages.append(value)
    return RemoteConfig(raw['enabled'], origin, team, aud, tuple(e.casefold() for e in emails), tuple(pairs), *ages)
