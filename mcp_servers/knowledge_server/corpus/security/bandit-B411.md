---
id: bandit-B411
title: bandit B411 import_xmlrpclib
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B411, CWE-20
---
Using {name} to parse untrusted XML data is known to be vulnerable to XML attacks. Use defusedxml.xmlrpc.monkey_patch() function to monkey-patch xmlrpclib and mitigate XML vulnerabilities.

대상 호출: xmlrpc
심각도: HIGH · CWE-20

B411: import_xmlrpclib
----------------------

XMLRPC is particularly dangerous as it is also concerned with communicating
data over a network. Use defusedxml.xmlrpc.monkey_patch() function to
monkey-patch xmlrpclib and mitigate remote XML attacks.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B411 | import_xmlrpclib    | - xmlrpc                           | high      |
+------+---------------------+------------------------------------+-----------+
