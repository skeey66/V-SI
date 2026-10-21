---
id: bandit-B308
title: bandit B308 mark_safe
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B308, CWE-79
---
Use of mark_safe() may expose cross-site scripting vulnerabilities and should be reviewed.

대상 호출: django.utils.safestring.mark_safe
심각도: MEDIUM · CWE-79

B308: mark_safe
---------------

Use of mark_safe() may expose cross-site scripting vulnerabilities and should
be reviewed.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B308 | mark_safe           | - django.utils.safestring.mark_safe| Medium    |
+------+---------------------+------------------------------------+-----------+
