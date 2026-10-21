---
id: bandit-B611
title: bandit B611 django_rawsql_used
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B611
---
**B611: Potential SQL injection on RawSQL function**

:Example:

```

    >> Issue: [B611:django_rawsql_used] Use of RawSQL potential SQL attack vector.
       Severity: Medium Confidence: Medium
       CWE: CWE-89 (https://cwe.mitre.org/data/definitions/89.html)
       Location: examples/django_sql_injection_raw.py:11:26
       More Info: https://bandit.readthedocs.io/en/latest/plugins/b611_django_rawsql_used.html
    10        ' WHERE "username"="admin" OR 1=%s --'
    11  User.objects.annotate(val=RawSQL(raw, [0]))

 - https://docs.djangoproject.com/en/dev/topics/security/#sql-injection-protection
 - https://cwe.mitre.org/data/definitions/89.html

    CWE information added
