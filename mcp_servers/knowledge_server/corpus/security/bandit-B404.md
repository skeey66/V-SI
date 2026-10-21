---
id: bandit-B404
title: bandit B404 import_subprocess
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B404, CWE-78
---
Consider possible security implications associated with the subprocess module.

대상 호출: subprocess
심각도: LOW · CWE-78

B404: import_subprocess
-----------------------

Consider possible security implications associated with these modules.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B404 | import_subprocess   | - subprocess                       | low       |
+------+---------------------+------------------------------------+-----------+
