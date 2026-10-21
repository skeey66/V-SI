---
id: bandit-B307
title: bandit B307 eval
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B307, CWE-78
---
Use of possibly insecure function - consider using safer ast.literal_eval.

대상 호출: eval
심각도: MEDIUM · CWE-78

B307: eval
----------

Use of possibly insecure function - consider using safer ast.literal_eval.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B307 | eval                | - eval                             | Medium    |
+------+---------------------+------------------------------------+-----------+
