---
id: bandit-B306
title: bandit B306 mktemp_q
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B306, CWE-377
---
Use of insecure and deprecated function (mktemp).

대상 호출: tempfile.mktemp
심각도: MEDIUM · CWE-377

B306: mktemp_q
--------------

Use of insecure and deprecated function (mktemp).

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B306 | mktemp_q            | - tempfile.mktemp                  | Medium    |
+------+---------------------+------------------------------------+-----------+
