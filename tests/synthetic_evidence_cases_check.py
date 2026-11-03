"""Validate synthetic review fixtures, without claiming answer correctness."""
import json
from pathlib import Path


def main():
    path = Path(__file__).resolve().parents[1] / 'eval' / 'synthetic_evidence_cases.jsonl'
    cases = [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    assert len(cases) == 12
    assert len({case['id'] for case in cases}) == len(cases)
    assert {case['type'] for case in cases if case['split'] == 'development'} == {
        'allow', 'deny', 'conditional', 'unknown', 'conflict', 'cross_model', 'image_manual'}
    assert len([case for case in cases if case['split'] == 'holdout']) == 5
    for case in cases:
        assert case['question'] and case['expected'] and case['sources'], case['id']
        assert all(source.get('name') and source.get('device') and source.get('text')
                   for source in case['sources']), case['id']
        if case['type'] == 'cross_model':
            assert len({source['device'] for source in case['sources']}) > 1
        if case['type'] == 'conflict':
            assert len(case['sources']) == 2
        if case['type'] == 'image_manual':
            assert {source.get('kind') for source in case['sources']} == {'image_caption', 'document'}
    print('synthetic evidence fixtures: structure PASS; answer quality requires human review')


if __name__ == '__main__':
    main()
