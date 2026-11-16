"""Local-only setup helper. Never creates or starts a public tunnel."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from remote_config import load_config
from share_snapshot import readonly


def disable(path):
    raw = json.loads(path.read_text(encoding='utf-8-sig'))
    raw['enabled'] = False
    with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=path.parent,delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(raw,stream,ensure_ascii=False,indent=2)
    os.replace(temporary,path)


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['validate','disable','list-policies'])
    parser.add_argument('--config',type=Path)
    parser.add_argument('--data',type=Path,default=Path(__file__).resolve().parents[2]/'data'/'demo')
    args = parser.parse_args()
    if args.command == 'list-policies':
        db = readonly(args.data/'monitor.sqlite3')
        try:
            for row in db.execute('SELECT id,config FROM policies WHERE deleted=0'):
                config = json.loads(row['config'])
                print(json.dumps({'id':row['id'],'name':config['name']},ensure_ascii=False))
        finally:
            db.close()
    elif not args.config:
        parser.error('--config가 필요합니다.')
    elif args.command == 'disable':
        disable(args.config.resolve())
        print('원격 공유를 껐습니다. 이후 요청은 차단됩니다. cloudflared 프로세스도 종료하세요.')
    else:
        try:
            config = load_config(args.config)
            if not config.enabled or config.hostname == 'monitor.example.com' or config.audience == '0'*64 or config.team_domain == 'your-team.cloudflareaccess.com':
                raise ValueError('예시 값을 실제 설정으로 바꾸고 enabled=true로 지정하세요.')
            print(json.dumps({'valid':True,'origin':config.public_origin,'shared_policy_count':len(config.shared_policies)}))
        except Exception as exc:
            print(str(exc),file=sys.stderr)
            raise SystemExit(2)
