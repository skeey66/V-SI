---
id: bandit-B703
title: bandit B703 django_mark_safe
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B703
---
**B703: Potential XSS on mark_safe function**

:Example:

```

    >> Issue: [B703:django_mark_safe] Potential XSS on mark_safe function.
       Severity: Medium Confidence: High
       CWE: CWE-80 (https://cwe.mitre.org/data/definitions/80.html)
       Location: examples/mark_safe_insecure.py:159:4
       More Info: https://bandit.readthedocs.io/en/latest/plugins/b703_django_mark_safe.html
    158         str_arg = 'could be insecure'
    159     safestring.mark_safe(str_arg)

 - https://docs.djangoproject.com/en/dev/topics/security/#cross-site-scripting-xss-protection
 - https://docs.djangoproject.com/en/dev/ref/utils/#module-django.utils.safestring
 - https://docs.djangoproject.com/en/dev/ref/utils/#django.utils.html.format_html
 - https://cwe.mitre.org/data/definitions/80.html

    CWE information added
