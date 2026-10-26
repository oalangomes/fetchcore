#!/usr/bin/env python3
"""Offline #84 channel attribution; no backend I/O or source text in output."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import hashlib
import importlib.util
from pathlib import Path
from statistics import mean

SPEC = importlib.util.spec_from_file_location('hybrid84_runner', Path(__file__).with_name('run.py'))
bench = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bench)


def require(condition):
    if not condition:
        raise ValueError("frozen channel attribution validation failed")


def overlaps(document, truth):
    """Partial useful-region overlap, distinct from complete evidence recovery."""
    return any(
        document['source_ref'] == region.source_ref
        and document['span'][0] <= region.line_end
        and document['span'][1] >= region.line_start
        for requirement in truth.requirements
        for alternative in requirement.alternatives
        for region in alternative.regions
    )


def means(rows):
    return {
        **{key: mean(row[key] for row in rows) for key in bench.QUALITY},
        **{key: mean(row[key] for row in rows) for key in (
            'candidates_examined', 'returned_chars', 'returned_line_count', 'useful_hit_density',
        )},
        'context_yield': {
            str(b): mean(row['budgets'][str(b)] for row in rows)
            for b in bench.BASELINE_LINE_BUDGETS
        },
    }


def analyze(directory, output):
    if output.exists():
        raise ValueError('output exists; attribution artifacts are immutable')
    control = bench.read(directory / 'control.json.gz', stage='control')
    capture = bench.read(directory / 'capture.json.gz', stage='capture')
    replay = bench.read(directory / 'replay.json.gz', stage='replay')
    _, _, suite, _queries, frozen = bench.inputs()
    require(all(control['identity'][key] == value for key, value in frozen.items()))
    require(capture['identity'] == replay['identity'] == control['identity'])
    bench.verify_control(control, control['identity'])
    require(capture['control_artifact_fingerprint'] == bench.digest(directory / 'control.json.gz'))
    require(replay['capture_fingerprint'] == bench.digest(directory / 'capture.json.gz'))
    require(capture['snapshots'] == replay['snapshots'] == control['snapshots'])
    require(set(control['query_ids']) == set(_queries))
    truths = {case.query_id: case.truth for case in suite.cases}
    result = {
        'protocol': bench.PROTOCOL, 'schema_version': 1, 'stage': 'channel-analysis',
        'status': 'complete', 'identity': bench.public_identity(control['identity']),
        'processor_sha256': bench.digest(Path(__file__)),
        'snapshot_fingerprints': {arm: bench.mapping_fingerprint(snapshot)
                                  for arm, snapshot in control['snapshots'].items()},
        'inputs': {name: bench.digest(directory / (name + '.json.gz'))
                   for name in ('control', 'capture', 'replay')},
        'queries': {}, 'summary': {},
    }
    for backend in bench.BACKENDS:
        arm = bench.arm_id(backend, 'hybrid')
        dense_arm = bench.arm_id(backend, 'dense')
        result['queries'][backend] = {}
        require({d['query_id'] for d in capture['captures'][arm]} == set(_queries))
        require(len(capture['captures'][arm]) == len(_queries))
        for descriptor in capture['captures'][arm]:
            q = descriptor['query_id']
            truth = truths[q]
            payload = bench.load_checkpoint(
                bench.verify_checkpoint_descriptor(descriptor),
                identity_fingerprint=capture['checkpoint_identities'][arm], backend=backend, query_id=q,
            )
            bench.validate_query_checkpoint(payload, query_id=q, observation_keys=[arm + '/control'], capture=True)
            rows = payload['captures']
            # Every measured repetition must share the displayed channel ordering.
            require(len({row['input']['fingerprint'] for row in rows}) == 1)
            first = rows[0]
            full = bench.FusionInput.from_mapping(first['input'])
            selected = bench.select_input(full, ('control', None, None, 60), 80 if backend == 'qdrant-vector' else 20)
            fused = bench.FusionKernel().fuse(bench.FusionRequest(selected))
            require(all(row['real_fusion_output_fingerprint'] == fused.output_fingerprint for row in rows))
            documents = {}
            for doc in first['evidence']:
                require(doc['revision'] == suite.corpus['revision'])
                require(doc['checksum'] == 'sha256:' + hashlib.sha256(doc['content'].encode()).hexdigest())
                documents[doc['id']] = {
                    key: doc[key] for key in ('id', 'source_ref', 'revision', 'checksum')
                } | {'span': [doc['line_start'], doc['line_end']]}
            require(set(documents) == {c.candidate_id for c in full.candidates})
            dense_rows = [r for r in control['observations'][dense_arm] if r['query_id'] == q]
            hybrid_rows = [r for r in control['observations'][arm] if r['query_id'] == q]
            capture_observations = payload['observations'][arm + '/control']
            require(all(r['result_spans'] == hybrid_rows[0]['result_spans'] for r in capture_observations))
            dense_ids = [r['id'] for r in dense_rows[0]['result_spans']]
            hybrid_ids = [r['id'] for r in hybrid_rows[0]['result_spans']]
            require([c.candidate_id for c in fused.candidates if c.returned] == hybrid_ids)
            dense_candidates = sorted((c for c in full.candidates if c.dense), key=lambda c: c.dense.rank)
            dense_equivalent = [c.candidate_id for c in dense_candidates[:20]] == dense_ids
            require(dense_equivalent)
            for row in dense_rows + hybrid_rows:
                for provenance in row['evidence_provenance']:
                    if provenance['id'] in documents:
                        require(provenance['checksum'] == documents[provenance['id']]['checksum'])
                        require(provenance['span'] == documents[provenance['id']]['span'])
            fused_map = {c.candidate_id: asdict(c) for c in fused.candidates}
            full_map = {c.candidate_id: c for c in full.candidates}
            useful = {identity for identity, doc in documents.items() if overlaps(doc, truth)}
            details = []
            for identity in sorted(useful):
                candidate = full_map[identity]
                fused_candidate = fused_map.get(identity)
                dense_rank = candidate.dense.rank if candidate.dense else None
                inversions = [c.candidate_id for c in fused.candidates
                              if fused_candidate and c.fused_rank < fused_candidate['fused_rank']
                              and c.lexical_contribution > 0 and dense_rank is not None
                              and (c.dense_rank is None or c.dense_rank > dense_rank)]
                details.append({
                    **documents[identity], 'dense_top20_rank': dense_ids.index(identity) + 1 if identity in dense_ids else None,
                    'hybrid_top20_rank': hybrid_ids.index(identity) + 1 if identity in hybrid_ids else None,
                    'lexical_original': asdict(candidate.lexical) if candidate.lexical else None,
                    'dense_original': asdict(candidate.dense) if candidate.dense else None,
                    'fusion': fused_candidate, 'lexical_inversions_ahead': inversions,
                    'admission_excluded': fused_candidate is None,
                })
            dense_mean, hybrid_mean = means(dense_rows), means(hybrid_rows)
            delta = {key: hybrid_mean[key] - dense_mean[key] for key in bench.QUALITY}
            direction = 'improved' if delta['evidence_reciprocal_rank'] > 1e-9 else 'regressed' if delta['evidence_reciprocal_rank'] < -1e-9 else 'unchanged'
            left = {c.candidate_id for c in selected.candidates if c.lexical}
            right = {c.candidate_id for c in selected.candidates if c.dense}
            lost = sorted((set(dense_ids) - set(hybrid_ids)) & useful)
            classes = []
            if direction == 'regressed':
                classes.append('rrf-rank-contribution')
            if lost:
                classes.append('evidence-outside-top-k')
            if any(x['lexical_original'] and x['fusion'] and not x['fusion']['lexical_rank'] for x in details):
                classes.append('useful-lexical-signal-outside-admission-window')
            if hybrid_mean['returned_chars'] > dense_mean['returned_chars']:
                classes.append('context-inflation')
            if not useful:
                classes.append('ground-truth-not-in-acquired-pool')
            result['queries'][backend][q] = {
                'classifications': classes,
                'confidence': 'high for deterministic rank attribution; relevance limited to frozen Ground Truth',
                'limitations': 'One corpus; partial useful-span loss is not necessarily lost complete evidence; no authority inferred.',
                'capture_checkpoint_sha256': descriptor['sha256'],
                'full_acquisition_fingerprint': full.fingerprint,
                'fusion_output_fingerprint': fused.output_fingerprint,
                'expected_evidence': [r.to_mapping() for r in truth.requirements],
                'mrr_direction': direction, 'dense': dense_mean, 'hybrid': hybrid_mean,
                'quality_deltas': delta,
                'context_yield_deltas': {k: hybrid_mean['context_yield'][k] - v for k, v in dense_mean['context_yield'].items()},
                'acquired': {'lexical': first['lexical_acquired'], 'dense': first['dense_acquired'],
                             'eligible_intersection': sum(c.lexical is not None and c.dense is not None for c in full.candidates)},
                'admitted': {'lexical': len(left), 'dense': len(right), 'intersection': len(left & right), 'union': len(left | right)},
                'dense_only_top20': sorted(set(dense_ids) - set(hybrid_ids)),
                'hybrid_only_top20': sorted(set(hybrid_ids) - set(dense_ids)),
                'useful_dense_outside_hybrid_top20': lost,
                'useful_candidates': details,
                'candidates': [
                    {**documents[c.candidate_id], 'lexical': asdict(c.lexical) if c.lexical else None,
                     'dense': asdict(c.dense) if c.dense else None, 'fusion': fused_map.get(c.candidate_id)}
                    for c in full.candidates
                ],
                'checks': {'repetitions': 20, 'dense_order_equivalent': dense_equivalent,
                           'real_fusion_equivalent': True, 'materialized_provenance_equivalent': True},
            }
        qs = list(result['queries'][backend].values())
        result['summary'][backend] = {
            'mrr_directions': dict(Counter(q['mrr_direction'] for q in qs)),
            'useful_displaced_count': sum(len(q['useful_dense_outside_hybrid_top20']) for q in qs),
            'mean_admitted_intersection': mean(q['admitted']['intersection'] for q in qs),
            'dense': means(control['observations'][dense_arm]),
            'hybrid': means(control['observations'][arm]),
        }
    bench.write(output, result)
    print(result['summary'])


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-directory', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    analyze(args.private_directory, args.output)
