"""Explicit local case reclassification using request model fields, not case titles."""
import json
import re
from collections import Counter
from uuid import uuid4

MODEL_KEYS = {'model', 'model_name', 'model_id', 'ibrain_model_id', 'third_party_model_name'}
CONTAINERS = {'parameters', 'params', 'input', 'request', 'body', 'config', 'options', 'data'}
FAMILIES = [
    ('Seedream', r'seedream'), ('Seedance', r'seedance'), ('Seed Audio', r'seed[-_]audio'),
    ('Hailuo', r'hailuo'), ('DeepSeek', r'deepseek'), ('Gemini', r'gemini'),
    ('Claude', r'claude'), ('Qwen', r'qwen'), ('GLM', r'(?:^|[-_.])glm'),
    ('Kimi', r'kimi|moonshot'), ('MiniMax', r'minimax'), ('Vidu', r'vidu'),
    ('Suno', r'suno'), ('HappyHorse', r'happy[-_]?horse'), ('万相（Wan）', r'(?:^|[-_/])wan(?:x|\d)'),
    ('Veo', r'(?:^|[-_.])veo'), ('GPT', r'(?:^|[-_.])gpt(?:[-_.]|\d)'),
]


def models(body):
    try:
        parsed = json.loads(body)
    except (ValueError, RecursionError):
        return [], False
    found = []
    prompt = False
    stack = [(parsed, 0)]
    while stack:
        value, depth = stack.pop()
        if depth > 8:
            continue
        if isinstance(value, dict):
            for key, child in value.items():
                key = key.lower()
                if key in MODEL_KEYS and isinstance(child, (str, int)):
                    found.append(str(child).strip())
                elif key == 'prompt_id' and child:
                    prompt = True
                elif key in CONTAINERS and isinstance(child, (dict, list)):
                    stack.append((child, depth+1))
        elif isinstance(value, list):
            stack.extend((v, depth+1) for v in value[:16])
    return found, prompt


def family(model):
    if not model or '{{' in model or model.lower().startswith('ep-'):
        return None
    return next((name for name, pattern in FAMILIES if re.search(pattern, model, re.I)), None)


def reclassify(catalog, cases):
    changes = []
    families = Counter()
    old_ids = set()
    for case in cases:
        values, prompt = models(case.get('request', {}).get('body', ''))
        kinds = {family(value) for value in values}
        name = None
        if values:
            name = next(iter(kinds)) if len(kinds) == 1 and None not in kinds else '待确认模型'
        elif prompt:
            name = '待确认模型'
        else:
            modules = catalog.decorate(case)['modules']
            if any(m['id'] in {'base', 'prompt', 'gpt'} for m in modules):
                route = case.get('request', {}).get('path', '')
                if re.search(r'/models(?:[/?]|$)', route, re.I):
                    name = '通用接口'
                elif re.search(r'(?:query|result|status|fetch|cancel)', route, re.I):
                    name = '任务查询'
                elif any(re.match(r'(?:OpenAI|Prompt-|对话|生图片|生视频|生音频|图片生成|视频生成|多模态)', group)
                         for m in modules for group in m['groups']):
                    name = '待确认模型'
        if not name:
            continue
        before = catalog.assignments(case['id'])
        module_ids = list(dict.fromkeys(a['module_id'] for a in before))
        after = []
        for module_id in module_ids:
            business = next((b for b in catalog.data['businesses'] if b['module_id'] == module_id and b['name'] == name), None)
            if not business:
                business = {'id': str(uuid4()), 'module_id': module_id, 'name': name}
                catalog.data['businesses'].append(business)
            after.append({'module_id': module_id, 'business_id': business['id']})
        families[name] += 1
        if before != after:
            changes.append({'id': case['id'], 'name': case['name'], 'family': name,
                            'before': before, 'after': after})
            old_ids.update(a['business_id'] for a in before if a['business_id'])
            catalog.data['assignments'][case['id']] = after
    # Remove only superseded, now-empty imported folders and their empty parents.
    old_names = [(b['module_id'], b['name']) for b in catalog.data['businesses'] if b['id'] in old_ids]
    for b in catalog.data['businesses']:
        if any(m == b['module_id'] and n.startswith(b['name']+'/') for m,n in old_names):
            old_ids.add(b['id'])
    used = {a['business_id'] for cid in catalog.case_ids for a in catalog.assignments(cid)}
    changed = True
    while changed:
        changed = False
        for b in list(catalog.data['businesses']):
            if b['id'] in old_ids and b['id'] not in used and not any(
                x['module_id'] == b['module_id'] and x['name'].startswith(b['name']+'/')
                for x in catalog.data['businesses']):
                catalog.data['businesses'].remove(b)
                changed = True
    if len(catalog.data['businesses']) > 5000:
        raise ValueError('业务数量已达到上限')
    if changes:
        catalog.data['revision'] += 1
    return {'changed': len(changes), 'families': dict(families), 'changes': changes,
            'revision': catalog.data['revision']}
