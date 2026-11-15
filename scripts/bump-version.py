#!/usr/bin/env python3
"""Apply an explicit stable/prerelease chart semver transition; never change images."""
import argparse
import json
from pathlib import Path
import re


def next_version(current, kind):
    match = re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?', current)
    if not match:
        raise ValueError('Unsupported chart semver: ' + current)
    major, minor, patch = map(int, match.groups()[:3])
    prerelease = match[4]
    if kind == 'stable':
        if not prerelease:
            raise ValueError('The version is already stable')
        return f'{major}.{minor}.{patch}'
    if kind == 'prerelease':
        if prerelease:
            prefix, separator, number = prerelease.rpartition('.')
            if not separator or not number.isdigit():
                raise ValueError('Prerelease increment requires a numeric final identifier')
            return f'{major}.{minor}.{patch}-{prefix}.{int(number) + 1}'
        return f'{major}.{minor}.{patch + 1}-alpha.1'
    if prerelease:
        raise ValueError('Choose stable to promote this prerelease, or prerelease to increment it')
    if kind == 'major':
        return f'{major + 1}.0.0'
    if kind == 'minor':
        return f'{major}.{minor + 1}.0'
    if kind == 'patch':
        return f'{major}.{minor}.{patch + 1}'
    raise ValueError('Unknown version transition')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bump', required=True, choices=['patch', 'minor', 'major', 'prerelease', 'stable'])
    parser.add_argument('--chart', type=Path, default=Path(__file__).resolve().parents[1] / 'charts/pihole/Chart.yaml')
    args = parser.parse_args()
    text = args.chart.read_text()
    line = re.search(r'^version:[ \t]*(\S+)[ \t]*$', text, re.M)
    if not line:
        parser.error('Missing chart version')
    current = line[1].strip('"\'')
    try:
        updated = next_version(current, args.bump)
    except ValueError as error:
        parser.error(str(error))
    args.chart.write_text(text[:line.start()] + 'version: ' + updated + text[line.end():])
    print(json.dumps({'current': current, 'new_version': updated}))


if __name__ == '__main__':
    main()
