"""Phase-one complementary execution plans. No scoring or scheduling policy."""
from copy import deepcopy
import json
import math
from pathlib import Path

from jsonschema import Draft202012Validator

from .contracts import require
from .design_bridge import prepare_mapping
from .joint_identity import sha, canonical

VERSION = 'task-flow-team-feasibility-1.0'
PLAN_MODE = 'complementary_team_v1'
IDENTITY = 'team-candidate-1.0'


def prepare(mapping, config):
    require(mapping.get('format_version') == 'task-flow-team-mapping-1.0', 'expected explicit team mapping')
    require(mapping.get('evaluation', {}).get('mode') == 'feasibility_only', 'team phase one is unscored')
    # Reuse only the legacy mapping/compiler validation, never its scoring path.
    legacy = deepcopy(mapping)
    legacy['format_version'] = 'task-flow-design-1.0'
    legacy['evaluation']['mode'] = 'sequential_product_v2'
    legacy = prepare_mapping(legacy)
    schema = json.loads(Path(__file__).with_name('team_config.schema.json').read_text(encoding='utf-8'))
    errors = list(Draft202012Validator(schema).iter_errors(config))
    require(not errors, 'invalid team config: ' + '; '.join(e.message for e in errors[:3]))
    canonical(config)  # finite numbers / canonical identity validation
    for intervals in config['schedules'].values():
        previous = None
        for interval in intervals:
            require(interval['end_s'] > interval['start_s'], 'stage interval must have positive duration')
            require(previous is None or interval['start_s'] >= previous, 'serial stages must not overlap')
            previous = interval['end_s']
    return legacy, deepcopy(config)


def edge_requests(previous, current, relation):
    """Hub handoff plus every contributing controller -> every executing member.

    This conservative direct-control policy is explicit, not arbitrary any-edge
    acceptance. Internal communication remains a hub star, not all-to-all.
    """
    edges = {(previous['hub'], current['hub'], 'communication')}
    if relation == 'control':
        edges.update((a, b, 'control') for a in previous['members'] for b in current['members'])
    return sorted(edges)


def internal_requests(plan):
    return [(member, plan['hub'], 'communication') for member in plan['members'] if member != plan['hub']]


def identity(task_id, flow_id, steps, context):
    assignment = [dict(step_instance_id=s['step_instance_id'], members=sorted(s['members']),
                       capability_assignments=s['capability_assignments'], hub=s['hub']) for s in steps]
    return 'team-chain-' + sha(dict(identity_version=IDENTITY, task_id=task_id, flow_id=flow_id,
                                    assignment=assignment, context=context))


def reservations(steps, artifacts, schedule):
    """All real members occupy the full explicit stage interval plus cooldown.

    No duration is inferred from single-equipment metrics. Every repeated
    execution is retained (including inventory), even for an identical member.
    """
    usage = []
    for i, stage in enumerate(steps):
        interval = schedule[i] if schedule else None
        for member in stage['members']:
            end = interval['end_s'] + artifacts[member]['cooldown_s'] if interval else None
            require(end is None or math.isfinite(end), 'member cooldown interval overflow')
            usage.append(dict(artifact=member, capacity=1, inventory=int(artifacts[member]['consumable']),
                              start_s=interval['start_s'] if interval else None, end_s=end))
    return usage
