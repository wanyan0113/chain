"""Build the frozen software data contract; never freeze a real study or import data."""
import argparse
import csv
import hashlib
import json
from pathlib import Path

VERSION = 'team-observation-contract-1.0'
REASONS = ['not_reached', 'planned_not_executed', 'execution_failure', 'output_lost', 'truth_missing', 'cannot_evaluate']


def fields():
    identifier = dict(type='string', minLength=1)
    nullable_id = dict(type=['string', 'null'], minLength=1)
    ids = dict(type='array', uniqueItems=True, items=identifier)
    timestamp_pattern = r'^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$'
    time = dict(type='string', format='date-time', pattern=timestamp_pattern)
    nullable_time = dict(type=['string', 'null'], format='date-time', pattern=timestamp_pattern)
    reason = dict(enum=[None, *REASONS])
    observation = dict(observation_id=identifier, sample_id=identifier, execution_id=identifier,
        actual_label=nullable_id, reference_label=nullable_id, output_reference=nullable_id,
        independent_reference=nullable_id, evaluation_method=identifier,
        observation_status=dict(enum=['recorded', 'missing']), reference_status=dict(enum=['known', 'unknown']),
        missing_reason=reason, missing_reason_detail=nullable_id, recorded_at=time)
    stage = dict(candidate_id=identifier, stage_instance_id=identifier, attempt_id=dict(type='integer', minimum=1))
    tables = {
        'samples': dict(sample_id=identifier, pair_id=nullable_id, task_id=identifier, population=identifier,
            cluster_id=identifier, split=dict(enum=['calibration', 'evaluation']), stratum=identifier,
            eligible=dict(type='boolean'), exclusion_reason=nullable_id,
            baseline_input_reference=identifier, registered_at=time),
        'executions': dict(execution_id=identifier, sample_id=identifier, arm_id=identifier, task_id=identifier,
            source_report_id=identifier, candidate_id=identifier, environment_reference=identifier,
            environment_stratum=identifier, assigned_order=dict(type='integer', minimum=1),
            execution_repeat_id=dict(type='integer', minimum=1), actual_order=dict(type=['integer','null'], minimum=1),
            order_deviation_reason=nullable_id, execution_input_reference=identifier, reset_reference=nullable_id,
            expected_stage_instance_ids=dict(type='array', minItems=1, uniqueItems=True, items=identifier),
            registered_at=time, started_at=nullable_time, ended_at=nullable_time),
        'stage_attempts': dict(execution_id=identifier, **stage, registered_at=time, started_at=nullable_time,
            ended_at=nullable_time, execution_status=dict(enum=['planned','not_reached','not_executed','failed','completed']),
            reason=reason, reason_detail=nullable_id),
        'member_observations': dict(**observation, **stage, artifact_id=identifier,
            artifact_version=nullable_id, capability_id=identifier,
            input_reference=nullable_id, input_group_observation_ids=ids),
        'group_outputs': dict(**observation, **stage, hub=identifier, member_observation_ids=ids,
            input_group_observation_ids=ids),
        'task_endpoints': dict(**observation, candidate_id=identifier, endpoint_event_id=identifier,
            contributing_group_observation_ids=ids)}
    return tables


def schemas():
    definitions = {}
    for name, props in fields().items():
        row = dict(type='object', additionalProperties=False, required=list(props), properties=props)
        if name in ('member_observations','group_outputs','task_endpoints'):
            row['allOf'] = [
                {'if': {'properties': {'observation_status': {'const':'recorded'}}},
                 'then': {'properties': {'actual_label': {'type':'string','minLength':1}, 'output_reference': {'type':'string','minLength':1}}}},
                {'if': {'properties': {'reference_status': {'const':'known'}}},
                 'then': {'properties': {'reference_label': {'type':'string','minLength':1}, 'independent_reference': {'type':'string','minLength':1}}}},
                {'if': {'properties': {'observation_status': {'const':'missing'}}},
                 'then': {'properties': {'actual_label': {'type':'null'}}}},
                {'if': {'properties': {'reference_status': {'const':'unknown'}}},
                 'then': {'properties': {'reference_label': {'type':'null'}}}},
                {'if': {'properties': {'observation_status': {'const':'recorded'}, 'reference_status': {'const':'known'}}},
                 'then': {'properties': {'missing_reason': {'type':'null'}}},
                 'else': {'properties': {'missing_reason': {'enum':REASONS}}}}]
            if name == 'member_observations':
                row['allOf'].append({'if': {'properties': {'observation_status': {'const':'recorded'}}},
                    'then': {'properties': {'artifact_version': {'type':'string','minLength':1,'not':{'const':'UNASSIGNED'}}}}})
        if name == 'stage_attempts':
            row['allOf'] = [
                {'if': {'properties': {'execution_status': {'enum':['not_reached','not_executed','failed']}}},
                 'then': {'properties': {'reason': {'enum':REASONS}}}},
                {'if': {'properties': {'execution_status': {'enum':['failed','completed']}}},
                 'then': {'properties': {'started_at': {'type':'string','format':'date-time'}, 'ended_at': {'type':'string','format':'date-time'}}}},
                {'if': {'properties': {'execution_status': {'const':'completed'}}},
                 'then': {'properties': {'reason': {'type':'null'}}}}]
        if name == 'samples':
            row['allOf'] = [{'if': {'properties': {'eligible': {'const':False}}},
                             'then': {'properties': {'exclusion_reason': {'type':'string','minLength':1}}},
                             'else': {'properties': {'exclusion_reason': {'type':'null'}}}}]
        definitions[name] = row
    return dict(**{'$schema':'https://json-schema.org/draft/2020-12/schema', '$id':'urn:team-observation-contract:1.0'},
                type='object', additionalProperties=False,
                required=['contract_version','study_id','protocol_revision_hash','data_origin', *definitions],
                properties=dict(contract_version=dict(const=VERSION),study_id=dict(type='string',minLength=1),
                    protocol_revision_hash=dict(type=['string','null'],pattern='^[0-9a-f]{64}$'),
                    data_origin=dict(enum=['uncollected','synthetic_simulation','real_observational']),
                    **{name:dict(type='array',items={'$ref':'#/$defs/'+name}) for name in definitions}),
                allOf=[{'if':{'properties':{'data_origin':{'const':'uncollected'}}},
                    'then':{'properties':{name:dict(maxItems=0) for name in definitions}},
                    'else':{'properties':{'study_id':{'not':{'const':'UNASSIGNED'}},'protocol_revision_hash':{'type':'string'}}}}],
                **{'$defs':definitions})


def specification():
    return dict(contract_version=VERSION, status='software_data_contract_frozen',
        study_protocol_status='not_frozen', importer_implemented=False, independent_data_verifier_implemented=False,
        scope='Categorical labels and categorical_equality_v1 only; raw continuous measurements may be external references.',
        observation_layers=['member_observations','group_outputs','task_endpoints'],
        registration_layers=['samples','executions','stage_attempts'],
        primary_keys=dict(samples=['sample_id'], executions=['execution_id'],
            stage_attempts=['execution_id','stage_instance_id','attempt_id'],
            member_observations=['observation_id'], group_outputs=['observation_id'], task_endpoints=['observation_id']),
        unique_keys=dict(samples=[['pair_id']], executions=[['sample_id','arm_id','execution_repeat_id'], ['sample_id','assigned_order']],
            member_observations=[['execution_id','stage_instance_id','attempt_id','artifact_id','capability_id']],
            group_outputs=[['execution_id','stage_instance_id','attempt_id']],
            task_endpoints=[['execution_id','endpoint_event_id']]),
        nullable_unique_key_policy='Null pair_id is ignored for uniqueness; non-null pair_id identifies one enrolled baseline sample.',
        identifiers='Keys are scoped to the study; observation_id is additionally unique across all three observation layers. Stage IDs are scoped by execution_id, never activity type.',
        hashing=dict(software_files='SHA256 of exact file bytes; freeze-manifest content_hash is SHA256 of the sorted compact JSON filename-to-file-hash mapping.',
            protocol_and_source_reports='SHA256 of task_flow.joint_identity.canonical JSON: NFC text, sorted object keys, finite numeric canonicalization, semantic array order preserved; not the raw JSON file byte hash.',
            plan_projections='Hash the frozen projected records sorted by their declared primary keys, preserving order within stage-sequence arrays; do not include mutable runtime fields.',
            multi_source='source_binding preserves the template baseline. Every arm source_report_id separately resolves to its source_reports content hash and verified candidate; the baseline does not certify another report.'),
        required_cross_table_checks=[
            'Execution references an eligible enrolled sample; task, population, split and arm/source/candidate assignment match the protocol.',
            'Cluster/sample identity is disjoint across calibration and evaluation; no split inferred from observed outcomes.',
            'Execution repeats have distinct execution IDs and prespecified repeat numbers. Repeats remain within the same sample/cluster, never new independent subjects.',
            'Expected stages and member/capability assignments come from an independently verified source chain; no arbitrary row-supplied roster.',
            'Every execution has one first-attempt registration for each expected stage, even if never reached; every registered attempt has declared member-capability and group observation slots.',
            'Attempts start at 1 and are contiguous, within the frozen maximum, registered before starting and never overlap within a stage; unknown/failed attempt does not disappear.',
            'Every observation references the same sample, execution, candidate and registered stage/attempt; members, capabilities, hub and actual versions match the protocol.',
            'Group member links refer to actual member observation rows of the same attempt; each required contribution has a row, which may explicitly report missing data.',
            'Input group links refer to declared earlier stages in the same execution, with matching output references and actual attempt lineage; label equality alone is insufficient.',
            'Independent reference must differ from the produced output reference; group/endpoint truth is not computed from member correctness, score, product or final stage label.',
            'Missing data or absent rows cause explicit coverage blockers; descriptive group/endpoint outcomes remain separate rather than fabricated from unavailable member data.',
            'Timestamps, duration/order, paired environment strata, baseline references and source facts require semantic checks beyond JSON Schema.'],
        retry_policy=dict(analysis_attempt_rule='first_registered_attempt_only',
            retries='Keep all attempts and reasons; no best-success selection or replacement of missing first attempts.',
            final_endpoint='Define independently after the protocol endpoint window; may include retry effects and need not equal conjunction of first attempts.',
            lineage='If a later stage consumed a retry output, retain that exact link; block conditional-model validation if it differs from the prespecified selected-attempt chain.'),
        missing_policy='missing_reason is the primary reason, with missing_reason_detail for concurrent causes; observation/reference status flags preserve each unavailable component independently.',
        denominators=dict(member='Report enrolled, reached, attempted, recorded, reference-known, evaluable, correct and missing per stage/member/capability/version; do not pool contributions as independent samples.',
            group='Independent group labels on prespecified attempts; member correctness is not group ground truth.',
            endpoint='Report enrolled eligible executions, endpoint-evaluable, success and missing separately; missing is neither success nor failure.',
            conditional='First factor is unconditional. Later factors condition on success of ALL prespecified preceding group events on the SAME evaluation cohort. Report numerator, denominator and unavailable prefix labels.',
            zero_support='A zero conditioning denominator gives null, never 0 or 1. Joint-event counts may still be reported independently.',
            pairing='Keep enrolled-pair denominator, complete-pair denominator, missing pair IDs and direction-of-change counts separately; incomplete coverage blocks confirmatory interpretation.'),
        probability_interpretation=dict(quality_scores='General quality products remain indices, not probabilities.',
            marginal='Joint-product interpretation needs appropriate independence evidence, not just equal scale identifiers.',
            conditional='Full-prefix conditional chain rule does not need independence; verify event definitions and support.',
            endpoint='Separately justify whether task success equals all stage events succeeding; correction can break equivalence.',
            prediction='Same-cohort conditional-count cancellation is an identity, not held-out prediction validation.'),
        pairing=dict(unit='One enrolled baseline sample, within the same task and declared population, executed in both arms with distinct execution IDs.',
            comparison='Arm source/candidate may differ in length or membership; task event and baseline difficulty must be comparable and independently reviewed.',
            controls='Prespecify randomization/counterbalancing or justified order, environment strata, reset/washout/carryover handling and all deviations. Pairing alone does not establish causality.',
            independent_arms='Do not manufacture pairs from unrelated samples/tasks. Analyze as a separately prespecified design.'),
        freeze_requirements=[
            'Fill task, member, group and endpoint measurement objects, direction, scale meaning, categorical success events, methods, independent references and population.',
            'Bind verified source reports and exact arm candidates, stages, assignments, hubs and actual equipment/firmware versions; UNASSIGNED is not ready.',
            'Prespecify eligibility, strata, disjoint cluster-level calibration/evaluation split, attempt maximum, endpoint window and pairing/order/environment/carryover controls.',
            'Prespecify minimum calibration/evaluation/prefix coverage, confidence level, prediction-error tolerance, subgroup and systematic-bias rules.',
            'Freeze protocol, baseline enrollment and execution-assignment projections before evaluation outputs; record a lock with content hashes, timestamp and review evidence.',
            'Freeze attempt policy and planned first-attempt slots; runtime retries are registered append-only before execution. Runtime status/timing updates must not rewrite frozen assignment projections.',
            'A study lock is an immutable new artifact. Protocol/arm/member changes require a new revision and lock, never editing an old lock.',
            'Synthetic examples validate software shape only. Real-world truth, construct validity, independence and causal claims require separate evidence.'],
        lock_plan_projections=dict(samples=list(fields()['samples']),
            executions=['execution_id','sample_id','arm_id','task_id','source_report_id','candidate_id','environment_stratum',
                        'assigned_order','execution_repeat_id','execution_input_reference','expected_stage_instance_ids','registered_at'],
            first_stage_attempts=['execution_id','candidate_id','stage_instance_id','attempt_id','registered_at'],
            runtime_fields='Actual status/timing/order/environment/reset observations are append-audited separately, never used to rewrite the above projections.'),
        csv_encoding=dict(encoding='UTF-8', delimiter=',', quoting='RFC4180', null='empty cell',
            arrays='JSON arrays in a quoted CSV cell', boolean='true or false', attempt_id='positive base-10 integer',
            timestamp='RFC3339 with explicit UTC offset', empty_string='Not allowed as a recorded label or identifier; use null plus missing reason.'),
        csv_packet_metadata='All six CSV tables require the accompanying dataset metadata containing contract_version, study_id, protocol_revision_hash, data_origin and the complete table_files mapping; headers alone do not identify a dataset.',
        future_acceptance=dict(importer='Not implemented in this contract-freeze step.',
            checks='Schema validation is structural only, not referential integrity, study readiness or domain validation. Timestamp patterns enforce lexical shape; actual calendar, UTC-offset and chronology checks require the later semantic verifier.',
            production_writeback='Forbidden: imported observations/statistics never automatically update production quality, weights, formula or policy.'),
        domain_validity='pending', formula_changed=False)


def template(draft):
    from copy import deepcopy
    arms = [dict(arm_id='UNASSIGNED', source_report_id='UNASSIGNED', **deepcopy(flow)) for flow in draft['flows']]
    for arm in arms:
        for stage in arm['stages']:
            for member in stage['members']:
                member['measurements'] = [dict(capability_id=cap,measurement_object='',direction='UNASSIGNED',
                    scale_meaning='',evaluation_method='',reference_method='',population='',success_event='') for cap in member['capabilities']]
    return dict(contract_version=VERSION, study_protocol_status='draft', study_id='UNASSIGNED', data_origin='uncollected',
        source_binding=draft['source_binding'],
        source_reports=[dict(source_report_id='UNASSIGNED', report_sha256=None)],
        arms=arms,
        measurement_method='categorical_equality_v1', analysis_attempt_rule='first_registered_attempt_only',
        maximum_attempts=None, planned_execution_repeats=None, endpoint_window='', retry_task_effect='',
        sampling_design='UNASSIGNED', split_policy='cluster_disjoint_calibration_evaluation',
        pairing_controls={k:v for k,v in draft['paired_design'].items() if k not in ('minimum_sample_coverage','acceptable_prediction_error')},
        acceptance=dict(alpha=None, minimum_calibration=None, minimum_evaluation=None, minimum_prefix_support=None,
                        acceptable_absolute_error=None, subgroup_plan='', systematic_bias_rule=''),
        domain_validity='pending', actual_study_frozen=False)


def protocol_schema():
    text = dict(type='string')
    identifier = dict(type='string', minLength=1)
    ready = dict(type='string', minLength=1, **{'not':{'const':'UNASSIGNED'}})
    source_binding = dict(type='object', additionalProperties=False,
        required=['source_input_hash','execution_context_hash','selected_candidate_ids','evaluation_context_hash'],
        properties={k:(dict(type='array', uniqueItems=True, items=identifier) if k=='selected_candidate_ids' else identifier)
                    for k in ['source_input_hash','execution_context_hash','selected_candidate_ids','evaluation_context_hash']})
    measure_props = {k:text for k in ['capability_id','measurement_object','direction','scale_meaning','evaluation_method','reference_method','population','success_event']}
    measurement = dict(type='object',additionalProperties=False,required=list(measure_props),properties=measure_props)
    member = dict(type='object', additionalProperties=False, required=['artifact_id','artifact_version','capabilities','measurements'],
        properties=dict(artifact_id=identifier, artifact_version=identifier,
                        capabilities=dict(type='array', minItems=1, uniqueItems=True, items=identifier),
                        measurements=dict(type='array',minItems=1,items=measurement)))
    stage_fields = ['stage_instance_id','activity_id','activity_version','activity_type','evaluation_plan_key','hub',
                    'capability_assignments','members','measurement_object','direction','scale_meaning','evaluation_method',
                    'reference_method','population','group_success_event','member_to_group_mapping','full_prefix_stage_instance_ids']
    stage_props = {k:text for k in stage_fields}
    stage_props.update(capability_assignments=dict(type='object', minProperties=1, additionalProperties=identifier),
        members=dict(type='array', minItems=1, maxItems=2, items=member),
        member_to_group_mapping=dict(type='object', additionalProperties=False, required=['status','policy'],
            properties=dict(status=dict(const='not_assumed'),policy=dict(type='null'))),
        full_prefix_stage_instance_ids=dict(type='array', uniqueItems=True, items=identifier))
    stage = dict(type='object', additionalProperties=False, required=stage_fields, properties=stage_props)
    arm_props = {k:text for k in ['arm_id','source_report_id','task_id','candidate_id','flow_id','endpoint_definition',
                                 'endpoint_method','endpoint_reference_method','endpoint_equals_all_stage_success']}
    arm_props['stages'] = dict(type='array', minItems=1, items=stage)
    arm = dict(type='object', additionalProperties=False, required=list(arm_props), properties=arm_props)
    acceptance = dict(type='object', additionalProperties=False,
        required=['alpha','minimum_calibration','minimum_evaluation','minimum_prefix_support','acceptable_absolute_error','subgroup_plan','systematic_bias_rule'],
        properties=dict(alpha=dict(type=['number','null'],exclusiveMinimum=0,exclusiveMaximum=.5),
            **{k:dict(type=['integer','null'],minimum=1) for k in ['minimum_calibration','minimum_evaluation','minimum_prefix_support']},
            acceptable_absolute_error=dict(type=['number','null'],exclusiveMinimum=0,exclusiveMaximum=1),
            subgroup_plan=text,systematic_bias_rule=text))
    pair_types = dict(enrollment_required=dict(const=True),baseline_sample_link_required=dict(const=True),
        **{k:text for k in ['assignment_method','execution_order_policy','carryover_control','population_comparability','calibration_evaluation_split']},
        )
    props = dict(contract_version=dict(const=VERSION),study_protocol_status=dict(enum=['draft','frozen']),study_id=identifier,
        data_origin=dict(enum=['uncollected','synthetic_simulation','real_observational']),source_binding=source_binding,
        source_reports=dict(type='array',minItems=1,items=dict(type='object',additionalProperties=False,required=['source_report_id','report_sha256'],
            properties=dict(source_report_id=identifier,report_sha256=dict(type=['string','null'],pattern='^[0-9a-f]{64}$')))),
        arms=dict(type='array',minItems=1,items=arm),measurement_method=dict(const='categorical_equality_v1'),
        analysis_attempt_rule=dict(const='first_registered_attempt_only'),maximum_attempts=dict(type=['integer','null'],minimum=1,maximum=10),
        planned_execution_repeats=dict(type=['integer','null'],minimum=1),endpoint_window=text,retry_task_effect=text,
        sampling_design=dict(enum=['UNASSIGNED','single_arm','paired_same_sample','independent_arms']),
        split_policy=dict(const='cluster_disjoint_calibration_evaluation'),
        pairing_controls=dict(type='object',additionalProperties=False,required=list(pair_types),properties=pair_types),
        acceptance=acceptance,domain_validity=dict(const='pending'),actual_study_frozen=dict(type='boolean'))
    ready_stage = dict(type='object',properties={k:ready for k in ['measurement_object','direction','scale_meaning',
        'evaluation_method','reference_method','population','group_success_event']})
    ready_measurement = dict(properties={k:ready for k in measure_props})
    ready_measurement['properties']['evaluation_method'] = dict(const='categorical_equality_v1')
    ready_stage['properties']['evaluation_method'] = dict(const='categorical_equality_v1')
    ready_stage['properties']['members'] = dict(items=dict(properties=dict(artifact_version=ready,measurements=dict(items=ready_measurement))))
    ready_arm = dict(properties={k:ready for k in ['arm_id','source_report_id','task_id','candidate_id','flow_id',
        'endpoint_definition','endpoint_method','endpoint_reference_method']})
    ready_arm['properties'].update(stages=dict(items=ready_stage),endpoint_equals_all_stage_success=dict(enum=['declared_equivalent','declared_not_equivalent']))
    ready_arm['properties']['endpoint_method'] = dict(const='categorical_equality_v1')
    frozen = dict(properties=dict(study_id=ready,data_origin=dict(enum=['synthetic_simulation','real_observational']),
        maximum_attempts=dict(type='integer'),planned_execution_repeats=dict(type='integer'),endpoint_window=ready,retry_task_effect=ready,
        sampling_design=dict(enum=['single_arm','paired_same_sample','independent_arms']),actual_study_frozen=dict(const=True),
        arms=dict(items=ready_arm),source_reports=dict(items=dict(properties=dict(source_report_id=ready,report_sha256=dict(type='string')))),
        acceptance=dict(properties={k:dict(type='integer') for k in ['minimum_calibration','minimum_evaluation','minimum_prefix_support']}),
        pairing_controls=dict(properties={k:ready for k in ['assignment_method','execution_order_policy','carryover_control','population_comparability','calibration_evaluation_split']})))
    frozen['properties']['acceptance']['properties'].update(alpha=dict(type='number'),acceptable_absolute_error=dict(type='number'),subgroup_plan=ready,systematic_bias_rule=ready)
    return dict(**{'$schema':'https://json-schema.org/draft/2020-12/schema'},type='object',additionalProperties=False,
        required=list(props),properties=props,allOf=[{'if':{'properties':{'study_protocol_status':{'const':'frozen'}}},
        'then':frozen,'else':{'properties':{'actual_study_frozen':{'const':False}}}}])


def build(output, draft):
    if output.exists():
        raise ValueError('contract output exists; choose a new directory, never overwrite a frozen baseline')
    output.mkdir(parents=True)
    documents = {'contract.json':specification(), 'schema.json':schemas(), 'protocol.schema.json':protocol_schema(),
                 'study-protocol.template.json':template(draft),
                 'empty-dataset.json':dict(contract_version=VERSION,study_id='UNASSIGNED',protocol_revision_hash=None,
                    data_origin='uncollected',**{name:[] for name in fields()}),
                 'dataset-metadata.template.json':dict(contract_version=VERSION,study_id='UNASSIGNED',protocol_revision_hash=None,
                    data_origin='uncollected',table_files={name:name+'.csv' for name in fields()})}
    for name, value in documents.items():
        (output/name).write_bytes((json.dumps(value, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))
    for table, props in fields().items():
        with (output/(table+'.csv')).open('w', encoding='utf-8', newline='') as f:
            csv.writer(f).writerow(props)
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(output.iterdir())}
    manifest = dict(contract_version=VERSION, status='software_data_contract_frozen',
        actual_study_frozen=False, importer_implemented=False, files=hashes,
        content_hash=hashlib.sha256(json.dumps(hashes, sort_keys=True, separators=(',',':')).encode('utf-8')).hexdigest())
    (output/'freeze-manifest.json').write_bytes((json.dumps(manifest, ensure_ascii=False, indent=2)+'\n').encode('utf-8'))
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args(argv)
    from task_flow.contracts import read_json
    draft = read_json(args.draft, allow_finite_floats=True)
    if draft.get('version') != 'team-statistical-protocol-draft-0.1':
        raise ValueError('expected the reviewed protocol draft')
    manifest = build(args.output_dir, draft)
    print(json.dumps(dict(status=manifest['status'], content_hash=manifest['content_hash'], actual_study_frozen=False, importer_implemented=False)))


if __name__ == '__main__':
    main()
