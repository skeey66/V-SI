---
id: bandit-B401
title: bandit B401 import_telnetlib
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B401, CWE-319
---
A telnet-related module is being imported.  Telnet is considered insecure. Use SSH or some other encrypted protocol.

대상 호출: telnetlib
심각도: HIGH · CWE-319

B401: import_telnetlib
----------------------

A telnet-related module is being imported. Telnet is considered insecure. Use
SSH or some other encrypted protocol.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B401 | import_telnetlib    | - telnetlib                        | high      |
+------+---------------------+------------------------------------+-----------+
