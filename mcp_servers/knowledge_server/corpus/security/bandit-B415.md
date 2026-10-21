---
id: bandit-B415
title: bandit B415 import_pyghmi
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B415, CWE-319
---
An IPMI-related module is being imported. IPMI is considered insecure. Use an encrypted protocol.

대상 호출: pyghmi
심각도: HIGH · CWE-319

B415: import_pyghmi
-------------------
An IPMI-related module is being imported. IPMI is considered insecure. Use
an encrypted protocol.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B415 | import_pyghmi       | - pyghmi                           | high      |
+------+---------------------+------------------------------------+-----------+
