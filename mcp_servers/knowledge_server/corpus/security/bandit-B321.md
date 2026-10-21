---
id: bandit-B321
title: bandit B321 ftplib
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B321, CWE-319
---
FTP-related functions are being called. FTP is considered insecure. Use SSH/SFTP/SCP or some other encrypted protocol.

대상 호출: ftplib.FTP
심각도: HIGH · CWE-319

B321: ftplib
------------

FTP-related functions are being called. FTP is considered insecure. Use
SSH/SFTP/SCP or some other encrypted protocol.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Calls                             |  Severity |
+======+=====================+====================================+===========+
| B321 | ftplib              | - ftplib.\*                        | High      |
+------+---------------------+------------------------------------+-----------+
