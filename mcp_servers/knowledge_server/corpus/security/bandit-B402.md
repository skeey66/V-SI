---
id: bandit-B402
title: bandit B402 import_ftplib
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B402, CWE-319
---
A FTP-related module is being imported.  FTP is considered insecure. Use SSH/SFTP/SCP or some other encrypted protocol.

대상 호출: ftplib
심각도: HIGH · CWE-319

B402: import_ftplib
-------------------
A FTP-related module is being imported.  FTP is considered insecure. Use
SSH/SFTP/SCP or some other encrypted protocol.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B402 | import_ftplib       | - ftplib                           | high      |
+------+---------------------+------------------------------------+-----------+
