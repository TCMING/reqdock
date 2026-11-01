"""Local, revisioned classification independent of imported request definitions."""
import copy
import json
from uuid import uuid4, uuid5, NAMESPACE_URL
from .case_modules import CaseModules


def clean_name(value, business=False):
    value = value.strip()
    parts = [p.strip() for p in value.split('/')]
    if (not value or len(value) > (200 if business else 80)
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or any(not p or p in {'.', '..'} for p in parts)
        or not business and len(parts) != 1):
        raise ValueError('名称不合法；模块不能包含 /，业务可使用 / 表示层级')
    return '/'.join(parts)


class CaseTaxonomy:
    def __init__(self, db, state=None):
        legacy = CaseModules(db)
        self.legacy = legacy
        self.data = copy.deepcopy(state) if state else {
            'revision': 0, 'modules': [
                {'id': d['id'], 'name': d['name'], 'root_group': d['prefixes'][0]}
                for d in legacy.definitions], 'businesses': [], 'assignments': {}}
        self.case_ids = {row[0] for row in db.execute('SELECT id FROM cases')}
        # Only previously unseen cases inherit imported directories. Existing local
        # assignments survive request edits, reimports, index rebuilds and restarts.
        paths_by_case = {}
        for cid, path in legacy.memberships:
            paths_by_case.setdefault(cid, []).append(path)
        for case_id in sorted(self.case_ids - self.data['assignments'].keys()):
            memberships = []
            for path in paths_by_case.get(case_id, []):
                definition = legacy.owner(path)
                module_id = definition['id']
                if not any(m['id'] == module_id for m in self.data['modules']):
                    self.data['modules'].append({'id': module_id, 'name': definition['name'],
                                                'root_group': definition['prefixes'][0]})
                relative = legacy.relative(path, definition)
                business = None
                if relative:
                    parts = relative.split('/')
                    for n in range(1, len(parts)+1):
                        name = '/'.join(parts[:n])
                        business = next((b for b in self.data['businesses']
                                         if b['module_id'] == module_id and b['name'] == name), None)
                        if not business:
                            business = {'id': definition['prefixes'][0]+'/'+name,
                                        'module_id': module_id, 'name': name}
                            # A new import must not reuse a locally renamed business ID.
                            if any(b['id'] == business['id'] for b in self.data['businesses']):
                                business['id'] = str(uuid5(NAMESPACE_URL, 'api-lab:'+module_id+':'+name+':'+str(self.data['revision'])))
                            self.data['businesses'].append(business)
                membership = {'module_id': module_id, 'business_id': business['id'] if business else None}
                if membership not in memberships:
                    memberships.append(membership)
            self.data['assignments'][case_id] = memberships

    def module(self, id):
        matches = [m for m in self.data['modules'] if m['id'] == id]
        if not matches:
            matches = [m for m in self.data['modules'] if m['name'] == id]
        if len(matches) != 1:
            raise ValueError('模块不存在或名称不唯一，请刷新后选择')
        return matches[0]

    def business(self, id):
        return next((b for b in self.data['businesses'] if b['id'] == id), None)

    def assignments(self, id):
        return self.data['assignments'].get(id, [])

    def view(self):
        modules = []
        for m in self.data['modules']:
            count = sum(any(a['module_id'] == m['id'] for a in self.assignments(cid)) for cid in self.case_ids)
            modules.append({**m, 'count': count})
        businesses = []
        for b in self.data['businesses']:
            count = sum(any(a['business_id'] == b['id'] for a in self.assignments(cid)) for cid in self.case_ids)
            businesses.append({**b, 'count': count})
        return {'revision': self.data['revision'], 'modules': modules, 'businesses': businesses}

    def decorate(self, item):
        groups = {}
        for a in self.assignments(item['id']):
            m = self.module(a['module_id'])
            entry = groups.setdefault(m['id'], {'id': m['id'], 'name': m['name'], 'groups': []})
            b = self.business(a['business_id'])
            entry['groups'].append(b['name'] if b else '未分类')
        return {**item, 'modules': list(groups.values()), 'classification': self.assignments(item['id'])}

    def select(self, module, group):
        selected_module = self.module(module) if module else None
        bs = [b for b in self.data['businesses'] if not selected_module or b['module_id'] == selected_module['id']]
        options = [b['id'] for b in sorted(bs, key=lambda b: (self.module(b['module_id'])['name'], b['name']))]
        labels = {b['id']: b['name'] if selected_module else self.module(b['module_id'])['name']+' / '+b['name'] for b in bs}
        for m in self.data['modules']:
            if selected_module and m['id'] != selected_module['id']:
                continue
            if any(any(a['module_id'] == m['id'] and not a['business_id'] for a in self.assignments(cid)) for cid in self.case_ids):
                options.append(m['root_group'])
                labels[m['root_group']] = '未分类' if selected_module else m['name']+' / 未分类'
        selected_business = self.business(group) if group else None
        if group and not selected_business:
            legacy_module = self.legacy.owner(group)
            if legacy_module and (not selected_module or legacy_module['id'] == selected_module['id']):
                old_relative = self.legacy.relative(group, legacy_module)
                selected_business = next((b for b in bs if b['module_id'] == legacy_module['id'] and b['name'] == old_relative), None)
        relative = group
        if selected_module and group:
            if group in {selected_module['root_group'], '未分组', '未分类'}:
                relative = ''
            elif not selected_business:
                selected_business = next((b for b in bs if b['name'] == group), None)
        def matches(a):
            if selected_module and a['module_id'] != selected_module['id']:
                return False
            if not group:
                return True
            b = self.business(a['business_id'])
            if selected_business:
                return bool(b and b['module_id'] == selected_business['module_id'] and
                            (b['name'] == selected_business['name'] or b['name'].startswith(selected_business['name']+'/')))
            return not b and (relative == '' or self.module(a['module_id'])['root_group'] == group)
        allowed = [cid for cid in self.case_ids if any(matches(a) for a in self.assignments(cid))]
        return {'groups': options, 'group_labels': labels, 'modules': self.view()['modules']}, json.dumps(allowed)

    def mutate(self, command):
        if command['revision'] != self.data['revision']:
            raise ValueError('分类已被修改，请刷新后重试')
        action, kind = command['action'], command['kind']
        collection = self.data['modules' if kind == 'module' else 'businesses']
        target = next((x for x in collection if x['id'] == command.get('id')), None)
        if action != 'create' and not target:
            raise ValueError('分类不存在，请刷新')
        if action == 'delete':
            if kind == 'module':
                occupied = any(a['module_id'] == target['id'] for cid in self.case_ids for a in self.assignments(cid))
                occupied |= any(b['module_id'] == target['id'] for b in self.data['businesses'])
            else:
                occupied = any(a['business_id'] == target['id'] for cid in self.case_ids for a in self.assignments(cid))
                occupied |= any(b['module_id'] == target['module_id'] and b['name'].startswith(target['name']+'/') for b in collection)
            if occupied:
                raise ValueError('分类中仍有用例或下级业务，请先调整归属或删除下级业务')
            collection.remove(target)
        else:
            name = clean_name(command.get('name', ''), kind == 'business')
            module_id = target['module_id'] if target and kind == 'business' else command.get('module_id')
            if kind == 'business':
                module_id = self.module(module_id)['id']
            if any(x is not target and x['name'].casefold() == name.casefold()
                   and (kind == 'module' or x['module_id'] == module_id) for x in collection):
                raise ValueError('名称已存在')
            if action == 'create':
                if len(collection) >= (1000 if kind == 'module' else 5000):
                    raise ValueError('分类数量已达到上限')
                value = {'id': str(uuid4()), 'name': name}
                value.update({'root_group': '@'+value['id']} if kind == 'module' else {'module_id': module_id})
                collection.append(value)
            else:
                # Renaming a parent business renames its descendants atomically.
                changes = {x['id']: name+x['name'][len(target['name']):] for x in collection
                           if kind == 'business' and x['module_id'] == module_id and
                           (x['id'] == target['id'] or x['name'].startswith(target['name']+'/'))}
                if kind == 'business':
                    future = [changes.get(x['id'], x['name']).casefold() for x in collection if x['module_id'] == module_id]
                    if len(set(future)) != len(future) or any(len(n)>200 for n in future):
                        raise ValueError('修改后业务名称冲突或超过长度限制')
                    for x in collection:
                        if x['id'] in changes:
                            x['name'] = changes[x['id']]
                else:
                    target['name'] = name
        if kind == 'business' and action != 'delete':
            for b in list(self.data['businesses']):
                parts = b['name'].split('/')
                for n in range(1, len(parts)):
                    parent = '/'.join(parts[:n])
                    if not any(x['module_id'] == b['module_id'] and x['name'] == parent for x in self.data['businesses']):
                        if len(self.data['businesses']) >= 5000:
                            raise ValueError('业务数量已达到上限')
                        self.data['businesses'].append({'id': str(uuid4()), 'module_id': b['module_id'], 'name': parent})
        self.data['revision'] += 1

    def assign(self, case_id, revision, memberships):
        if revision != self.data['revision']:
            raise ValueError('分类已被修改，请刷新后重试')
        if case_id not in self.case_ids:
            raise KeyError('用例不存在')
        unique = []
        for a in memberships:
            module = self.module(a['module_id'])
            a = {**a, 'module_id': module['id']}
            b = self.business(a.get('business_id'))
            if a.get('business_id') and (not b or b['module_id'] != a['module_id']):
                raise ValueError('业务不属于所选模块')
            if a not in unique:
                unique.append(a)
        self.data['assignments'][case_id] = unique
        self.data['revision'] += 1
