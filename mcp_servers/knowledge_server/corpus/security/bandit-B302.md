---
id: bandit-B302
title: bandit B302 marshal
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B302, CWE-502
---
Deserialization with the marshal module is possibly dangerous.

대상 호출: marshal.load, marshal.loads
심각도: MEDIUM · CWE-502

B302: marshal
-------------

Deserialization with the marshal module is possibly dangerous.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B302 | marshal             | - marshal.load                     | Medium    |
|      |                     | - marshal.loads                    |           |
+------+---------------------+------------------------------------+-----------+
