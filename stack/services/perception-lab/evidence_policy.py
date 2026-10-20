"""Explicit per-arm evidence grants (HA, captions, one-lever family policy), shared by live and saved comparisons."""


def ha_policy(value=None):
    # Legacy recipes used common HA evidence. Preserve that behavior explicitly.
    if value is None:
        return {'a': True, 'b': True}
    if not isinstance(value, dict) or set(value) != {'a', 'b'} or any(type(v) is not bool for v in value.values()):
        raise ValueError('invalid per-arm HA evidence policy')
    return dict(value)


def caption_policy(value=None):
    # Historical Qwen captions never silently enter a baseline: withheld unless
    # the recipe grants them. Arms differ only under an explicit ablation recipe.
    if value is None:
        return {'a': False, 'b': False}
    if not isinstance(value, dict) or set(value) != {'a', 'b'} or any(type(v) is not bool for v in value.values()):
        raise ValueError('invalid per-arm caption evidence policy')
    return dict(value)


def filter_ha(records, policy):
    policy = ha_policy(policy)
    result = []
    for original in records:
        # A new top-level record; nested values are shared with the input, and
        # never mutated here or by the consumers (causal_evidence copies what
        # it admits). A withheld 'ha' key is removed from the copy only.
        record = dict(original)
        if 'ha' in record and not policy.get(record.get('arm'), False):
            record.pop('ha')
            # A pure HA fact with its contents withheld is not usable evidence.
            if not any(k in record for k in ('objects','posture','pose_instances','relations','tracks','occupancy')):
                continue
        result.append(record)
    return result


# One-lever recipes: a shared baseline of evidence families plus exactly the
# declared lever. Families with a legacy per-arm key keep it; the rest use a
# per-arm 'source_policy' {family: bool}. Absent means the baseline default.
LEGACY_KEYS = {'ha': 'ha_evidence', 'caption': 'caption_evidence'}
DEFAULTS = ('granted', 'withheld', 'not_provisioned')
# Resolved per-arm statuses recorded with each activation.
STATUSES = ('granted', 'withheld_by_experiment', 'not_in_baseline', 'not_provisioned')
# The resolver's historical defaults; the catalog baseline must agree with them.
LEGACY_DEFAULTS = {'ha': 'granted', 'caption': 'withheld'}


def baseline(catalog):
    from ambient_context import FAMILIES
    base = catalog.get('baseline') if isinstance(catalog, dict) else None
    families = base.get('families') if isinstance(base, dict) else None
    if (not isinstance(base, dict) or set(base) != {'version', 'families', 'note'} or base['version'] != 1
            or not isinstance(base['note'], str) or not base['note']
            or not isinstance(families, dict) or tuple(families) != FAMILIES
            or any(not isinstance(f, dict) or set(f) != {'default', 'note'} or f['default'] not in DEFAULTS
                   or not isinstance(f['note'], str) or not f['note'] for f in families.values())
            or any(families[k]['default'] != v for k, v in LEGACY_DEFAULTS.items())):
        raise ValueError('invalid evidence baseline')
    return {k: v['default'] for k, v in families.items()}


def lever_families(lever):
    """Evidence families a lever varies (pipeline levers name none)."""
    paths = lever.get('paths', []) if isinstance(lever, dict) else []
    legacy = {v: k for k, v in LEGACY_KEYS.items()}
    return sorted({legacy[p] for p in paths if p in legacy} | {p.split('.', 1)[1] for p in paths if p.startswith('source_policy.')})


def _leaves(arm, defaults):
    """Flat per-arm setting paths with every family grant made explicit."""
    leaves = {}
    for key, value in arm.items():
        if key != 'source_policy':
            leaves[key] = value
            continue
        if (not isinstance(value, dict) or not value or set(value) - (set(defaults) - set(LEGACY_KEYS))
                or any(type(v) is not bool or defaults[f] == 'not_provisioned' for f, v in value.items())):
            raise ValueError('invalid per-arm source policy')
        leaves.update({'source_policy.' + f: v for f, v in value.items()})
    for family, default in defaults.items():
        key = LEGACY_KEYS.get(family, 'source_policy.' + family)
        if type(leaves.setdefault(key, default == 'granted')) is not bool:
            raise ValueError('invalid per-arm evidence grant')
    return leaves


def recipe_grants(recipe, defaults):
    """{family: {arm: status}}: granted, or why not. Withheld families that the
    baseline grants (or that are the lever) are withheld by the experiment."""
    varied = set(lever_families(recipe.get('lever')))
    leaves = {arm: _leaves(recipe[arm], defaults) for arm in ('a', 'b')}
    grants = {}
    for family, default in defaults.items():
        key = LEGACY_KEYS.get(family, 'source_policy.' + family)
        grants[family] = {arm: 'not_provisioned' if default == 'not_provisioned' else 'granted' if leaves[arm][key]
                          else 'withheld_by_experiment' if default == 'granted' or family in varied else 'not_in_baseline'
                          for arm in ('a', 'b')}
    return grants


def validate_recipe(recipe, defaults):
    """The two arms differ exactly at the declared lever path(s)."""
    lever = recipe.get('lever')
    paths = lever.get('paths') if isinstance(lever, dict) else None
    if (not isinstance(lever, dict) or set(lever) - {'coupled'} != {'paths', 'note'}
            or not isinstance(paths, list) or not paths or len(set(paths)) != len(paths)
            or any(not isinstance(p, str) or not p for p in paths)
            or not isinstance(lever['note'], str) or not lever['note']
            or (len(paths) > 1) != ('coupled' in lever)
            or ('coupled' in lever and (not isinstance(lever['coupled'], str) or not lever['coupled']))):
        raise ValueError(f"recipe {recipe.get('id')!r} needs one declared lever (several paths only as a named coupled set)")
    a, b = (_leaves(recipe[arm], defaults) for arm in ('a', 'b'))
    differ = {k for k in set(a) | set(b) if a.get(k, None) != b.get(k, None) or (k in a) != (k in b)}
    if differ != set(paths):
        raise ValueError(f"recipe {recipe.get('id')!r} arms differ at {sorted(differ)}, lever declares {sorted(paths)}")
    return recipe_grants(recipe, defaults)


def validate_catalog(catalog):
    defaults = baseline(catalog)
    for recipe in catalog['experiments']:
        validate_recipe(recipe, defaults)
    return defaults


def baseline_digest(catalog):
    from reasoning import digest
    return digest(catalog['baseline'])


def source_policy(value=None, *, ha=None, caption=None):
    """Validated per-arm family statuses. Absent (legacy activations and
    runners) derives them from the HA/caption switches and today's baseline."""
    from ambient_context import FAMILIES
    ha, caption = ha_policy(ha), caption_policy(caption)
    if value is None:
        value = {'ha': {arm: 'granted' if ha[arm] else 'withheld_by_experiment' for arm in ('a', 'b')},
                 'ambient': {'a': 'granted', 'b': 'granted'},
                 'caption': {arm: 'granted' if caption[arm] else 'not_in_baseline' for arm in ('a', 'b')},
                 **{f: {'a': 'not_provisioned', 'b': 'not_provisioned'} for f in FAMILIES[3:]}}
    if (not isinstance(value, dict) or set(value) != set(FAMILIES)
            or any(not isinstance(v, dict) or set(v) != {'a', 'b'} or any(s not in STATUSES for s in v.values()) for v in value.values())
            or any((value[f][arm] == 'granted') != switch[arm] for f, switch in (('ha', ha), ('caption', caption)) for arm in ('a', 'b'))):
        raise ValueError('invalid per-arm source policy')
    return {f: dict(value[f]) for f in FAMILIES}
