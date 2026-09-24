"""Explicit role permissions; legacy role names remain supported."""
ALIASES = {'admin': 'scrb_admin', 'operator': 'field_operator'}
ROLES = {'scrb_admin', 'field_operator', 'sector_supervisor', 'department_head',
         'technical_admin', 'auditor', 'judiciary', 'viewer', 'admin', 'operator'}


def role(who):
    name = who.get('role', '')
    return ALIASES.get(name, name)


def permitted(who, method, path):
    name = role(who)
    if name not in ROLES:
        return False
    if path in ('/api/auth/me', '/api/auth/logout'):
        return True
    read = method in ('GET', 'HEAD') or (method == 'POST' and path == '/api/blockchain/verify')
    if path in ('/docs', '/openapi.json', '/redoc'):
        return read
    if name == 'scrb_admin':
        return True
    if name == 'technical_admin':
        return read and path in ('/', '/api/health')
    if name == 'judiciary':
        if path.endswith('/assignments'):
            return False
        return read and (path == '/' or path.startswith('/api/cases') or
                         path.startswith('/api/evidence/') or path == '/api/blockchain/verify')
    if name == 'auditor':
        return read and (path == '/' or path.startswith('/api/alerts') or
                         path.startswith('/api/blockchain') or path == '/api/registry/audit')
    if path.startswith('/api/reviews'):
        return name in ('department_head', 'sector_supervisor')
    if read:
        if path == '/api/registry/audit' and name not in ('department_head',):
            return False
        return True
    if name == 'viewer':
        return False
    if path.startswith('/api/alerts/') and path.endswith('/acknowledge'):
        return True
    if path.startswith('/api/cases'):
        # Sharing evidence outside operational roles remains a network-admin action.
        if path.endswith('/assignments'):
            return name == 'department_head'
        return name in ('department_head', 'sector_supervisor') and not path.endswith('/access')
    if path.startswith('/api/aci/'):
        return name in ('department_head', 'sector_supervisor')
    if path.startswith('/api/cameras') or path.startswith('/api/topology'):
        return name == 'department_head'
    if path.startswith('/api/stream/') or path == '/api/sentinel/connect':
        return name == 'department_head'
    return False
