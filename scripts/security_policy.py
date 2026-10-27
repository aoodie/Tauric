"""Fail a release on every high/critical image finding without an exact exception."""

import json
from datetime import date, datetime, timedelta, timezone


def evaluate(report, exceptions, *, kind, platform, today=None):
    today = today or datetime.now(timezone.utc).date()
    if report.get('SchemaVersion') != 2 or not report.get('Results'):
        raise ValueError('A complete current scanner report is required.')
    for exception in exceptions:
        for field in ('kinds', 'platforms'):
            if field in exception and (not isinstance(exception[field], list) or not exception[field]
                                       or any(not isinstance(x, str) for x in exception[field])):
                raise ValueError('Exception scopes must be explicit lists of image kinds and platforms.')
    blocked, accepted = [], []
    for result in report['Results']:
        for finding in result.get('Vulnerabilities') or []:
            severity = finding.get('Severity', 'UNKNOWN')
            if severity in {'LOW', 'MEDIUM'}:
                continue
            target = result['Target']
            if result.get('Class') == 'os-pkgs':
                system = report['Metadata']['OS']
                target = 'os:' + system['Family'] + ':' + system['Name']
            row = {'kind': kind, 'platform': platform, 'target': target,
                   'id': finding['VulnerabilityID'], 'package': finding['PkgName'],
                   'version': finding['InstalledVersion'], 'severity': severity,
                   'package_path': finding.get('PkgPath') or ''}
            matching = [e for e in exceptions if
                        row['kind'] in e.get('kinds', [e.get('kind')]) and
                        row['platform'] in e.get('platforms', [e.get('platform')]) and
                        all(e.get(k) == v for k, v in row.items() if k not in {'kind', 'platform'})]
            valid = [e for e in matching if today < date.fromisoformat(e['expires']) <= today + timedelta(days=30)
                     and e.get('reason') and e.get('evidence') and e.get('reviewer') and not finding.get('FixedVersion')]
            (accepted if valid else blocked).append(row)
    return {'blocked': blocked, 'accepted_exceptions': accepted}


def main():
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report')
    parser.add_argument('--kind', required=True)
    parser.add_argument('--platform', required=True)
    parser.add_argument('--policy', default='deploy/security-exceptions.json')
    args = parser.parse_args()
    with open(args.report) as source, open(args.policy) as policy:
        value = evaluate(json.load(source), json.load(policy), kind=args.kind, platform=args.platform)
    print(json.dumps(value, sort_keys=True))
    return bool(value['blocked'])


if __name__ == '__main__':
    raise SystemExit(main())
