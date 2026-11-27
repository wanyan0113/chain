"""Strict versioned input contracts and bounded activity instantiation."""
from copy import deepcopy
from dataclasses import dataclass
import hashlib
from itertools import product
import json
import math
from pathlib import Path
import unicodedata

from jsonschema import Draft202012Validator

SAFE_INTEGER = 2 ** 53 - 1
DEFAULT_GROUND_LIMIT = 10000
SCHEMA = json.loads(Path(__file__).with_name('schema.json').read_text(encoding='utf-8'))


class ContractError(ValueError):
    def __init__(self, message, *, code='invalid_input', path=()):
        self.code = code
        self.path = tuple(path)
        super().__init__(message)


def require(condition, message, *, code='invalid_input'):
    if not condition:
        raise ContractError(message, code=code)


def _check_tree(value, depth=0, counter=None, *, allow_finite_floats=False):
    counter = [0] if counter is None else counter
    counter[0] += 1
    require(counter[0] <= (1000000 if allow_finite_floats else 100000) and depth <= 48, 'input exceeds structural bounds')
    if isinstance(value, dict):
        for key, child in value.items():
            require(type(key) is str, 'JSON keys must be strings')
            _check_tree(key, depth + 1, counter, allow_finite_floats=allow_finite_floats)
            _check_tree(child, depth + 1, counter, allow_finite_floats=allow_finite_floats)
    elif isinstance(value, list):
        for child in value:
            _check_tree(child, depth + 1, counter, allow_finite_floats=allow_finite_floats)
    elif type(value) is str:
        require(unicodedata.normalize('NFC', value) == value, 'strings must be NFC normalized')
        try:
            value.encode('utf-8')
        except UnicodeError as exc:
            raise ContractError('invalid Unicode string') from exc
    elif type(value) is int:
        require(abs(value) <= SAFE_INTEGER, 'integer exceeds exact interoperability range')
    elif type(value) is float and allow_finite_floats:
        require(math.isfinite(value), 'non-finite report number')
    elif value is not None and type(value) is not bool:
        raise ContractError('only JSON objects, arrays, strings, booleans, null and bounded integers are supported')


def read_json(path, *, allow_finite_floats=False):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, f'duplicate JSON key: {key}')
            result[key] = value
        return result

    def constant(value):
        raise ContractError(f'non-finite JSON number: {value}')

    try:
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'),
                          object_pairs_hook=pairs, parse_constant=constant)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as exc:
        raise ContractError(f'invalid JSON: {exc}') from exc
    _check_tree(data, allow_finite_floats=allow_finite_floats)
    return data


def canonical_sha256(data):
    """Sort object keys; list order and integer spelling are part of the contract."""
    _check_tree(data)
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()


def validate_shape(data, kind):
    require(kind in ('catalog', 'request', 'sequence'), 'unknown contract kind')
    _check_tree(data)
    schema = {'$schema': SCHEMA['$schema'], '$defs': SCHEMA['$defs'], '$ref': '#/$defs/' + kind}
    error = next(Draft202012Validator(schema).iter_errors(data), None)
    if error is not None:
        raise ContractError(f'{kind} {list(error.absolute_path)}: {error.message}',
                            code='input_incomplete' if error.validator == 'required' else 'invalid_input',
                            path=error.absolute_path)


def _unique(items, key, label):
    out = {}
    for item in items:
        name = item[key]
        require(name not in out, f'duplicate {label}: {name}')
        out[name] = item
    return out


def _bindings(parameters, objects):
    names = sorted(parameters)
    require(all(domain in objects for domain in parameters.values()), 'unknown parameter domain')
    for values in product(*(sorted(objects[parameters[name]]) for name in names)):
        yield dict(zip(names, values))


def _size(parameters, objects):
    require(all(domain in objects for domain in parameters.values()), 'unknown parameter domain')
    return math.prod(len(objects[t]) for t in parameters.values())


def resolve_fact(problem, ref, parameters=None):
    parameters = {} if parameters is None else parameters
    require(ref['predicate'] in problem.predicates, f'unknown predicate: {ref["predicate"]}')
    declaration = problem.predicates[ref['predicate']]
    require(len(ref['args']) == len(declaration['arguments']), 'fact arity mismatch')
    args = []
    for value, domain in zip(ref['args'], declaration['arguments']):
        if isinstance(value, dict):
            require(value['param'] in parameters, f'unbound parameter: {value["param"]}')
            value = parameters[value['param']]
        require(value in problem.catalog['objects'][domain], f'object outside domain {domain}: {value}')
        args.append(value)
    return (ref['predicate'], *args)


def check_value(declaration, value, unit=None, *, nullable=False):
    if declaration['type'] == 'boolean':
        require(unit is None, 'boolean fact must not have a unit')
        require(type(value) is bool or (nullable and value is None), 'boolean fact requires exact bool')
    else:
        require(unit == declaration['unit'], 'integer unit mismatch')
        require(type(value) is int or (nullable and value is None), 'integer fact requires exact int')
        if value is not None:
            require(declaration['minimum'] <= value <= declaration['maximum'], 'integer fact outside bounds')


def check_condition(problem, condition, parameters=None):
    if 'all' in condition or 'any' in condition:
        for child in condition.get('all', condition.get('any')):
            check_condition(problem, child, parameters)
    elif 'not' in condition:
        check_condition(problem, condition['not'], parameters)
    else:
        test = condition['test']
        key = resolve_fact(problem, test['fact'], parameters)
        declaration = problem.fact_specs[key]
        # Integer thresholds may lie outside the fact range, but keep type and unit.
        if declaration['type'] == 'integer':
            require(type(test['value']) is int and test.get('unit') == declaration['unit'], 'comparison type/unit mismatch')
        else:
            check_value(declaration, test['value'], test.get('unit'))
            require(test['op'] in ('eq', 'ne'), 'boolean comparison supports only eq/ne')


def _condition_predicates(condition):
    if 'test' in condition:
        return {condition['test']['fact']['predicate']}
    if 'not' in condition:
        return _condition_predicates(condition['not'])
    return set().union(*(_condition_predicates(c) for c in condition.get('all', condition.get('any'))))


@dataclass
class Problem:
    catalog: dict
    request: dict
    predicates: dict
    fact_specs: dict
    activities: dict
    instances: tuple
    derived_rules: tuple
    initial_base: dict
    catalog_sha256: str
    request_sha256: str


def prepare_problem(catalog, request, *, max_groundings=DEFAULT_GROUND_LIMIT):
    """Validate and own copies of inputs. No search or external resource inference."""
    validate_shape(catalog, 'catalog')
    validate_shape(request, 'request')
    require(type(max_groundings) is int and 1 <= max_groundings <= 100000, 'invalid instantiation budget')
    catalog, request = deepcopy(catalog), deepcopy(request)
    sha = canonical_sha256(catalog)
    require((request['catalog_id'], request['catalog_version'], request['catalog_sha256']) ==
            (catalog['catalog_id'], catalog['version'], sha), 'catalog identity/version/digest mismatch')
    objects = catalog['objects']
    flat = [v for domain in objects.values() for v in domain]
    require(len(set(flat)) == len(flat), 'object identities must be unique across domains')
    predicates = _unique(catalog['facts'], 'id', 'predicate')
    activities = _unique(catalog['activities'], 'id', 'activity')
    capabilities = _unique(catalog['capabilities'], 'uri', 'capability')
    rules = _unique(catalog['derived_rules'], 'id', 'derived rule')
    fact_specs = {}
    total = 0
    for declaration in predicates.values():
        require(all(t in objects for t in declaration['arguments']), 'unknown fact domain')
        require(declaration['type'] != 'integer' or declaration['minimum'] <= declaration['maximum'], 'reversed integer bounds')
        require(declaration['kind'] != 'derived' or declaration['type'] == 'boolean', 'derived facts must be boolean')
        count = math.prod(len(objects[t]) for t in declaration['arguments'])
        total += count
        require(total <= max_groundings, 'fact instantiation limit exceeded')
        for args in product(*(sorted(objects[t]) for t in declaration['arguments'])):
            fact_specs[(declaration['id'], *args)] = declaration
    problem = Problem(catalog, request, predicates, fact_specs, activities, (), (), {}, sha, canonical_sha256(request))
    dependencies = {name: set() for name, p in predicates.items() if p['kind'] == 'derived'}
    ground_rules = {}
    for rule in rules.values():
        total += _size(rule['parameters'], objects)
        require(total <= max_groundings, 'rule instantiation limit exceeded')
        for parameters in _bindings(rule['parameters'], objects):
            head = resolve_fact(problem, rule['head'], parameters)
            require(head[0] in dependencies, 'rule head must be derived')
            require(head not in ground_rules, 'multiple rules for one derived fact are not supported')
            check_condition(problem, rule['when'], parameters)
            dependencies[head[0]].update(_condition_predicates(rule['when']) & dependencies.keys())
            ground_rules[head] = (rule, parameters)
    require(set(ground_rules) == {k for k, p in fact_specs.items() if p['kind'] == 'derived'},
            'derived facts need exactly one rule each', code='input_incomplete')
    order = []
    while dependencies:
        ready = sorted(name for name, deps in dependencies.items() if not deps)
        require(bool(ready), 'cyclic derived rules are not supported')
        order.extend(ready)
        dependencies = {name: deps - set(ready) for name, deps in dependencies.items() if name not in ready}
    problem.derived_rules = tuple((head, *ground_rules[head]) for name in order for head in sorted(ground_rules) if head[0] == name)
    instances = []
    for name, activity in sorted(activities.items()):
        require(set(activity['required_capabilities']) <= capabilities.keys(),
                'undeclared required capability', code='input_incomplete')
        total += _size(activity['parameters'], objects)
        require(total <= max_groundings, 'activity instantiation limit exceeded')
        for parameters in _bindings(activity['parameters'], objects):
            check_condition(problem, activity['applicable_when'], parameters)
            check_condition(problem, activity['preconditions'], parameters)
            writes = set()
            for effect in activity['effects']:
                key = resolve_fact(problem, effect['fact'], parameters)
                declaration = fact_specs[key]
                require(declaration['kind'] == 'mutable', 'activity cannot write static or derived facts')
                require(key not in writes, 'duplicate/conflicting writes after parameter binding')
                writes.add(key)
                if 'set' not in effect:
                    continue
                expr = effect['set']
                if 'value' in expr:
                    check_value(declaration, expr['value'], expr.get('unit'))
                else:
                    source = fact_specs[resolve_fact(problem, expr['read'], parameters)]
                    require(source['type'] == declaration['type'] == 'integer', 'read/offset requires integer facts')
                    require(source['kind'] != 'derived', 'effects cannot read derived values')
                    require(source['unit'] == declaration['unit'] == expr['unit'], 'read/offset unit mismatch')
            instances.append({'activity_id': name, 'activity_version': activity['version'], 'parameters': parameters})
    problem.instances = tuple(instances)
    for field, kind in (('static_facts', 'static'), ('initial_state', 'mutable')):
        seen = set()
        for item in request[field]:
            key = resolve_fact(problem, item['fact'])
            require(key not in seen, 'duplicate initial fact, including positive/negative conflicts')
            seen.add(key)
            require(fact_specs[key]['kind'] == kind, f'{field} contains wrong fact kind')
            check_value(fact_specs[key], item['value'], item.get('unit'), nullable=True)
            problem.initial_base[key] = item['value']
    evidence_seen = set()
    for item in request['ontology_evidence']:
        key = resolve_fact(problem, item['fact'])
        require(key not in evidence_seen, 'duplicate ontology evidence for one fact')
        evidence_seen.add(key)
        require(fact_specs[key]['kind'] == 'static', 'ontology import supports static facts only')
        check_value(fact_specs[key], item['value'], item.get('unit'))
        require(key not in problem.initial_base or problem.initial_base[key] is None or
                (type(problem.initial_base[key]) is type(item['value']) and problem.initial_base[key] == item['value']),
                'ontology evidence conflicts with explicit static fact')
        problem.initial_base[key] = item['value']
    for condition in [request['goal'], *request['forbidden_conditions']]:
        check_condition(problem, condition)
    require(set(request['required_activities']) <= activities.keys(), 'unknown mandatory activity')
    require(set(request['activity_limits']) <= activities.keys(), 'unknown activity limit')
    return problem


def validate_instance(problem, instance):
    _check_tree(instance)
    schema = {'$defs': SCHEMA['$defs'], '$ref': '#/$defs/instance'}
    error = next(Draft202012Validator(schema).iter_errors(instance), None)
    require(error is None, f'invalid activity instance: {error.message if error else ""}')
    require(instance['activity_id'] in problem.activities, 'unknown activity instance')
    activity = problem.activities[instance['activity_id']]
    require(instance['activity_version'] == activity['version'], 'activity version mismatch')
    require(instance['parameters'].keys() == activity['parameters'].keys(), 'parameter bindings must be complete and exact')
    for name, domain in activity['parameters'].items():
        require(instance['parameters'][name] in problem.catalog['objects'][domain], 'instance parameter outside domain')
    return activity


def validate_sequence(problem, sequence):
    """Structural/version validation only; NOT an independent semantic replay."""
    validate_shape(sequence, 'sequence')
    require(sequence['catalog_sha256'] == problem.catalog_sha256 and
            sequence['request_sha256'] == problem.request_sha256, 'sequence input digest mismatch')
    require(len(sequence['steps']) <= problem.request['search']['max_length'], 'sequence exceeds length bound')
    counts = {}
    for instance in sequence['steps']:
        activity = validate_instance(problem, instance)
        name = instance['activity_id']
        counts[name] = counts.get(name, 0) + 1
        limit = min(activity['max_occurrences'], problem.request['activity_limits'].get(name, activity['max_occurrences']))
        require(counts[name] <= limit, 'sequence exceeds activity occurrence limit')
    return deepcopy(sequence)
