---
id: bandit-B412
title: bandit B412 import_httpoxy
source: https://bandit.readthedocs.io/en/1.9.4/plugins/index.html
license: Apache-2.0 (PyCQA bandit)
keys: B412, CWE-284
---
Consider possible security implications associated with {name} module.

대상 호출: wsgiref.handlers.CGIHandler, twisted.web.twcgi.CGIScript, twisted.web.twcgi.CGIDirectory
심각도: HIGH · CWE-284

B412: import_httpoxy
--------------------
httpoxy is a set of vulnerabilities that affect application code running in
CGI, or CGI-like environments. The use of CGI for web applications should be
avoided to prevent this class of attack. More details are available
at https://httpoxy.org/.

+------+---------------------+------------------------------------+-----------+
| ID   |  Name               |  Imports                           |  Severity |
+======+=====================+====================================+===========+
| B412 | import_httpoxy      | - wsgiref.handlers.CGIHandler      | high      |
|      |                     | - twisted.web.twcgi.CGIScript      |           |
+------+---------------------+------------------------------------+-----------+
